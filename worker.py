"""Cloudflare-hosted Tasktrack: authenticated routes and isolated workspaces."""

import asyncio
import base64
import hashlib
import json
import re
import struct
import uuid
from html import escape
from urllib.parse import parse_qs, quote, unquote, urlsplit

from workers import DurableObject, Request, Response, WorkerEntrypoint

from tasktrack import (
    agent_auth,
    agent_routes,
    app_routes,
    import_fetches,
    import_routes,
    membership_api,
    notifications,
    passkeys,
    previews,
    service_apps,
)
from tasktrack.access import Access
from tasktrack.accounts import Accounts, digest, safe_next, timestamp, token
from tasktrack.asset_version import ASSET_VERSION
from tasktrack.cloud_store import CloudStore
from tasktrack.db import Error, encode
from tasktrack.hosted_pages import (
    account_page,
    auth_page,
    error_page,
    invite_page,
    share_page,
)
from tasktrack.service import Service

MAX_UPLOAD = 10 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024


class Workspace(DurableObject):
    """Only reachable through the authenticated Workspaces binding."""

    def __init__(self, ctx, env):
        self.ctx, self.env = ctx, env

        def initialize():
            self.store = CloudStore(self.ctx.storage)
            self.service = Service(self.store, env.PUBLIC_URL)

        self.ctx.storage.transactionSync(initialize)

    async def execute(self, payload):
        failure = None

        def run():
            nonlocal failure
            try:
                if not payload.get("identity"):
                    raise Error(401, "sign_in_required", "Sign in to your workspace.")
                self.service.access = Access(payload["identity"])
                if payload["method"] == "GET":
                    attachment = re.fullmatch(
                        r"/api/v1/attachments/(\d+)/content", payload["path"]
                    )
                    if attachment:
                        if payload.get("query"):
                            raise Error(
                                422,
                                "unknown_filter",
                                "Attachment downloads do not accept filters.",
                            )
                        result = self.service.attachment_metadata(int(attachment[1]))
                    else:
                        result = self.service.read(
                            payload["path"], payload.get("query", {}), payload["actor"]
                        )
                    return {"status": 200, "data": result}
                status, result = self.service.mutate(
                    payload["method"],
                    payload["path"],
                    payload["body"],
                    payload["actor"],
                    payload["request_id"],
                    payload.get("session"),
                    payload.get("via", "api"),
                    payload.get("upload"),
                )
                return {"status": status, "data": result}
            except Error as exc:
                failure = exc
                raise  # The transaction must roll back on a domain error.

        try:
            return self.ctx.storage.transactionSync(run)
        except Exception:
            if failure:
                return {"status": failure.status, "data": failure.payload}
            raise


def html(content, status=200):
    return Response(
        content, status=status, headers={"Content-Type": "text/html; charset=utf-8"}
    )


def redirect(path, cookie=None):
    return Response(
        None,
        status=303,
        headers={"Location": path, **({"Set-Cookie": cookie} if cookie else {})},
    )


async def bounded_body(request, limit):
    if not request.body:
        return b""
    declared = request.headers.get("Content-Length")
    if declared and (not declared.isdigit() or int(declared) > limit):
        raise Error(413, "body_too_large", "This request is too large.")
    reader = request.js_object.body.getReader()
    pieces, size = [], 0
    try:
        while True:
            item = await reader.read()
            if item.done:
                return b"".join(pieces)
            piece = item.value.to_bytes()
            size += len(piece)
            if size > limit:
                await reader.cancel()
                raise Error(413, "body_too_large", "This request is too large.")
            pieces.append(piece)
    finally:
        reader.releaseLock()


async def body_data(request, form=False):
    raw = await bounded_body(request, 16384 if form else MAX_JSON)
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].lower()
    try:
        if form and content_type == "application/x-www-form-urlencoded":
            pairs = parse_qs(raw.decode(), keep_blank_values=True)
            if any(len(values) != 1 for values in pairs.values()):
                raise ValueError("Repeated fields")
            return {key: values[0] for key, values in pairs.items()}
        if content_type != "application/json":
            raise Error(415, "content_type", "Send JSON or a form.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return value
    except (ValueError, UnicodeError):
        raise Error(400, "invalid_body", "The request body is invalid.") from None


class Default(WorkerEntrypoint):
    async def task_call(
        self,
        identity,
        path,
        method="GET",
        body=None,
        query=None,
        request_id=None,
        session=None,
        via="ui",
        upload=None,
    ):
        stub = self.env.WORKSPACES.get(
            self.env.WORKSPACES.idFromName(identity["workspace_id"])
        )
        result = await stub.execute(
            {
                "identity": identity,
                "path": path,
                "method": method,
                "actor": identity.get("actor", "customer"),
                "body": body,
                "query": query or {},
                "request_id": request_id,
                "session": session,
                "via": via,
                "upload": upload,
            }
        )
        if result["status"] >= 400:
            error = result["data"]["error"]
            raise Error(
                result["status"],
                error["code"],
                error["message"],
                error.get("fields"),
                **{
                    k: v
                    for k, v in error.items()
                    if k not in {"code", "message", "fields"}
                },
            )
        return result["status"], result["data"]

    async def fetch(self, request):
        accounts = Accounts(self.env)
        parsed = urlsplit(request.url)
        path = unquote(parsed.path)
        try:
            is_preview = (
                not accounts.local
                and bool(previews.origin(self.env))
                and parsed.netloc == urlsplit(previews.origin(self.env)).netloc
            )
            # Wrangler rewrites loopback upstream hosts. Keep both local origins
            # usable without trusting forwarded-host headers in production.
            if (
                accounts.local
                and getattr(self.env, "LOCAL_EMAIL", "") == "1"
                and (path.startswith("/p/") or path == "/callback")
            ):
                is_preview = True
            if parsed.netloc != urlsplit(accounts.origin).netloc and not is_preview:
                raise Error(
                    400, "foreign_host", "Use the configured Tasktrack address."
                )
            if request.method not in {"GET", "HEAD"}:
                origin = request.headers.get("Origin")
                if (
                    origin is not None and origin != accounts.origin
                ) or request.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise Error(
                        403,
                        "foreign_origin",
                        "Use this Tasktrack site to submit the form.",
                    )
            pairs = parse_qs(parsed.query, keep_blank_values=True)
            if any(len(v) != 1 for v in pairs.values()):
                raise Error(
                    400, "duplicate_filter", "Supply each query parameter only once."
                )
            query = {k: v[0] for k, v in pairs.items()}
            if is_preview and path not in {
                "/style.css",
                "/hosted.css",
                "/favicon.svg",
                "/preview.js",
                "/passkeys.js",
            }:
                response = await previews.route(self, request, accounts, path, query)
            else:
                response = await self.route(request, accounts, path, query)
        except Error as exc:
            response = (
                Response.json(exc.payload, status=exc.status)
                if path.startswith("/api/")
                or "application/json" in request.headers.get("Accept", "")
                else html(error_page(str(exc), exc.status), exc.status)
            )
        except Exception as exc:
            # Never log URLs, email links, cookies, tokens or request bodies.
            print("Tasktrack request failed:", type(exc).__name__)
            if accounts.local:
                import traceback

                traceback.print_exc()
            response = Response.json(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Something went wrong. Please try again.",
                        "fields": {},
                    }
                },
                status=500,
            )
        headers = dict(response.headers.items())
        headers.update(
            {
                "Cache-Control": response.headers.get("Cache-Control", "no-store"),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "strict-origin",
                "X-Robots-Tag": "noindex, nofollow",
                "Content-Security-Policy": response.headers.get(
                    "Content-Security-Policy"
                )
                or "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        if not accounts.local:
            headers["Strict-Transport-Security"] = "max-age=31536000"
        if response.status == 429:
            headers["Retry-After"] = "600"
        body = response.js_object.body
        return Response(
            None if request.method == "HEAD" or not body else body,
            status=response.status,
            headers=headers,
        )

    async def route(self, request, accounts, path, query):
        method = "GET" if request.method == "HEAD" else request.method
        if (
            path
            in {
                "/app.js",
                "/auth.js",
                "/preview.js",
                "/passkeys.js",
                "/style.css",
                "/hosted.js",
                "/agent-access.js",
                "/service-apps.js",
                "/imports.js",
                "/hosted.css",
                "/favicon.svg",
            }
            and method == "GET"
        ):
            asset = await self.env.ASSETS.fetch(request)
            headers = dict(asset.headers.items())
            headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if query.get("v") == ASSET_VERSION
                else "public, max-age=0, must-revalidate"
            )
            return Response(
                None if not asset.js_object.body else asset.js_object.body,
                status=asset.status,
                headers=headers,
            )
        if path == "/healthz" and method == "GET":
            await accounts.one("SELECT 1 FROM users LIMIT 1")
            return Response.json({"status": "ok", "storage": "cloudflare"})
        if path in {"/oauth/device_authorization", "/oauth/token"} and method == "POST":
            data = await body_data(request, form=True)
            ip = request.headers.get("CF-Connecting-IP", "local")
            if path.endswith("device_authorization"):
                return Response.json(await agent_auth.start(accounts, data, ip))
            await accounts.limit("device-poll:" + ip, 120, 60)
            if data.get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code":
                result = await agent_auth.poll(accounts, data.get("device_code"))
            elif data.get("grant_type") == "client_credentials":
                result = await service_apps.exchange(accounts, data, ip)
            elif data.get("grant_type") == "refresh_token":
                result = await agent_auth.refresh(accounts, data.get("refresh_token"))
            else:
                result = {"error": "unsupported_grant_type"}
            return Response.json(result, status=400 if "error" in result else 200)
        returning = re.fullmatch(
            r"/preview/return/([a-f0-9-]{36})/([A-Za-z0-9_-]{43})", path
        )
        if method == "GET" and (path == "/preview/authorize" or returning):
            return await previews.authorize(
                self,
                request,
                accounts,
                {"id": returning[1], "challenge": returning[2]} if returning else query,
            )
        if path in {"/login", "/signup"} and method == "GET":
            return html(auth_page(path[1:], safe_next(query.get("next"))))
        if path.startswith("/auth/passkeys/"):
            return await passkeys.route(
                accounts, request, path, await body_data(request)
            )
        if path == "/auth/link" and method == "POST":
            data = await body_data(request, form=True)
            challenge = await accounts.login_link(
                data.get("email"),
                data.get("next"),
                request.headers.get("CF-Connecting-IP", "local"),
            )
            return html(
                auth_page(challenge=challenge, next_path=safe_next(data.get("next")))
            )
        if path == "/auth/code" and method == "POST":
            data = await body_data(request, form=True)
            session, next_path = await accounts.redeem_code(
                data.get("challenge"),
                data.get("code"),
                request.headers.get("CF-Connecting-IP", "local"),
            )
            if "application/json" in request.headers.get("Accept", ""):
                return Response.json(
                    {"next": next_path},
                    headers={"Set-Cookie": accounts.cookie(session)},
                )
            return redirect(next_path, accounts.cookie(session))
        if path == "/auth/verify":
            if method == "GET":
                secret = query.get("token", "")
                if not await accounts.one(
                    "SELECT 1 FROM login_links WHERE token_hash=? AND expires_at>?",
                    digest(secret),
                    timestamp(),
                ):
                    raise Error(
                        400,
                        "expired_link",
                        "This sign-in link has expired or was already used.",
                    )
                return html(auth_page(secret=secret))
            if method == "POST":
                data = await body_data(request, form=True)
                session, next_path = await accounts.redeem(
                    data.get("token"), request.headers.get("CF-Connecting-IP", "local")
                )
                return redirect(next_path, accounts.cookie(session))
        shared = re.fullmatch(r"/s/([A-Za-z0-9_-]{43})(/preview.png)?", path)
        if shared and method == "GET":
            share = await accounts.one(
                "SELECT * FROM shares WHERE token=? AND revoked_at IS NULL", shared[1]
            )
            if not share:
                raise Error(404, "share_missing", "This shared task is unavailable.")
            creator = await accounts.member_identity(
                share["created_by"], share["workspace_id"]
            )
            if not creator:
                raise Error(404, "share_missing", "This shared task is unavailable.")
            _, task = await self.task_call(
                creator, "/api/v1/tasks/" + str(share["task_id"])
            )
            if shared[2]:
                blob = await self.env.FILES.get(share["preview_key"])
                if not blob:
                    raise Error(
                        404, "image_missing", "The task preview is unavailable."
                    )
                return Response(blob.body, headers={"Content-Type": "image/png"})
            return html(share_page(share, task, accounts.origin))
        identity = await accounts.authenticate(request)
        invitation = re.fullmatch(r"/invite/([A-Za-z0-9_-]{43})", path)
        if invitation and method == "GET":
            row = await accounts.one(
                "SELECT w.name FROM invites i JOIN workspaces w ON w.id=i.workspace_id WHERE i.token_hash=? AND i.expires_at>? AND i.used_by IS NULL",
                digest(invitation[1]),
                timestamp(),
            )
            if not row:
                raise Error(404, "invite_missing", "This invitation is unavailable.")
            return html(invite_page(row["name"], invitation[1], identity))
        if not identity:
            if path.startswith("/api/"):
                raise Error(401, "sign_in_required", "Sign in to your workspace.")
            return redirect("/login?next=" + quote(safe_next(path), safe=""))
        if identity["bearer"] and not path.startswith("/api/v1/"):
            raise Error(
                403,
                "browser_session_required",
                "Sign in through the browser for account settings.",
            )
        selected = request.headers.get("X-Workspace-ID")
        if selected and selected != identity["workspace_id"]:
            raise Error(
                409,
                "workspace_changed",
                "Your workspace changed in another tab. Reload to continue.",
            )
        if path == "/account" and method == "GET":
            return html(account_page(identity, await accounts.overview(identity)))
        if path.startswith("/api/v1/import-fetches"):
            return await import_fetches.route(
                self, request, accounts, identity, path, method, body_data, bounded_body
            )
        if path == "/apps" or path.startswith("/api/v1/service-apps"):
            return await app_routes.route(
                self, request, accounts, identity, path, method, body_data
            )
        if path == "/agents" or path.startswith(
            (
                "/api/v1/agents",
                "/api/v1/agent-enrollments",
                "/api/v1/agent-notifications",
                "/api/v1/agent-channels",
                "/api/v1/agent-deliveries",
                "/api/v1/approval-requests",
            )
        ):
            response = await agent_routes.route(
                self, request, accounts, identity, path, method, body_data
            )
            if response is not None:
                return response
        if (
            re.fullmatch(r"/api/v1/import-jobs/[a-f0-9-]+/people", path)
            or path == "/imports"
            or path.startswith(
                (
                    "/api/v1/import-providers",
                    "/api/v1/import-people",
                    "/api/v1/import-connections",
                    "/api/v1/import-analyze",
                    "/api/v1/import-previews",
                    "/integrations/",
                )
            )
            or re.fullmatch(
                r"/api/v1/import-jobs/[^/]+/(source|files/[a-f0-9]{64}|rollback/(challenge|decision))",
                path,
            )
        ):
            response = await import_routes.route(
                self,
                request,
                accounts,
                identity,
                path,
                method,
                query,
                body_data,
                bounded_body,
            )
            if response is not None:
                return response
        if (
            path.startswith("/account/") or path == "/auth/logout" or invitation
        ) and method == "POST":
            data = await body_data(request, form=True)
            accounts.csrf(request, identity, data)
            if path == "/auth/logout":
                await accounts.run(
                    "DELETE FROM sessions WHERE token_hash=?", identity["session_hash"]
                )
                return redirect("/login", accounts.cookie("", clear=True))
            if path == "/account/switch":
                await accounts.switch(identity, data.get("workspace_id", ""))
                return redirect("/")
            if invitation:
                await accounts.join(identity, invitation[1])
                return redirect("/")
        if path.startswith("/api/account"):
            return await self.account_api(request, accounts, identity, path, method)
        if path == "/api/v1/me" and method == "GET":
            return Response.json(
                {
                    "user_id": identity["user_id"],
                    "email": identity["email"],
                    "workspace_id": identity["workspace_id"],
                    "membership": {
                        "id": identity["membership_id"],
                        "kind": identity["kind"],
                        "role": identity["access_role"],
                        "revision": identity["membership_revision"],
                    },
                    "workspaces": await accounts.many(
                        "SELECT w.id,w.name,m.kind,m.access_role AS role,m.revision FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? AND m.status='active' AND (?=0 OR w.id=?)",
                        identity["user_id"],
                        int(identity["bearer"]),
                        identity["workspace_id"],
                    ),
                }
            )
        if path == "/api/v1/memberships" and method == "GET":
            return Response.json(
                {"items": await membership_api.members(accounts, identity)}
            )
        membership_match = re.fullmatch(r"/api/v1/memberships/([a-f0-9]{32})", path)
        if membership_match and method == "PATCH":
            accounts.csrf(request, identity)
            return Response.json(
                await membership_api.patch(
                    accounts, identity, membership_match[1], await body_data(request)
                )
            )
        if path == "/api/v1/tenants" and method == "POST":
            accounts.csrf(request, identity)
            return Response.json(
                await membership_api.create_tenant(
                    accounts,
                    identity,
                    await body_data(request),
                    request.headers.get("Idempotency-Key"),
                ),
                status=201,
            )
        if path.startswith("/api/v1/"):
            preview_route = re.fullmatch(
                r"/api/v1/tasks/([A-Za-z0-9-]+)/client-preview", path
            )
            if preview_route:
                return await self.client_preview_api(
                    request, accounts, identity, preview_route[1], method
                )
            share_route = re.fullmatch(
                r"/api/v1/tasks/([A-Za-z0-9-]+)/shares(?:/([a-f0-9-]+))?", path
            )
            if share_route:
                return await self.share_api(
                    request, accounts, identity, share_route, method
                )
            upload, body = None, None
            if method != "GET":
                accounts.csrf(request, identity)
                if query:
                    raise Error(
                        422,
                        "unknown_filter",
                        "Write routes do not accept query parameters.",
                    )
                await accounts.limit("writes:" + identity["workspace_id"], 300, 60)
                if path.endswith("/attachments") and method == "POST":
                    raw = await bounded_body(request, MAX_UPLOAD + 65536)
                    form = await Request(
                        request.url,
                        method="POST",
                        headers=dict(request.headers.items()),
                        body=raw,
                    ).form_data()
                    keys = list(form.keys())
                    if (
                        len(set(keys)) != len(keys)
                        or set(keys) - {"file", "comment_id"}
                        or "file" not in keys
                    ):
                        raise Error(
                            422,
                            "upload_fields",
                            "Supply one file and an optional comment_id.",
                        )
                    file = form["file"]
                    if isinstance(file, str):
                        raise Error(422, "upload_file", "Choose a file to attach.")
                    upload = await file.bytes()
                    if len(upload) > MAX_UPLOAD:
                        raise Error(
                            413, "upload_too_large", "Files must be 10 MiB or smaller."
                        )
                    body = {
                        "filename": file.name,
                        "media_type": file.content_type or "application/octet-stream",
                    }
                    if "comment_id" in keys:
                        try:
                            body["comment_id"] = int(form["comment_id"])
                        except ValueError:
                            raise Error(
                                422, "comment_id", "Use a valid comment ID."
                            ) from None
                    await self.task_call(identity, path.rsplit("/", 1)[0])
                    await self.env.FILES.put(
                        identity["workspace_id"]
                        + "/blobs/"
                        + hashlib.sha256(upload).hexdigest(),
                        upload,
                    )
                else:
                    body = await body_data(request)
                    if (
                        re.fullmatch(r"/api/v1/projects/[^/]+/access", path)
                        and method == "PATCH"
                    ):
                        body = await membership_api.resolve_grants(
                            accounts, identity, body
                        )
            status, result = await self.task_call(
                identity,
                path,
                method,
                body,
                query,
                request.headers.get("Idempotency-Key"),
                request.headers.get("X-Session"),
                request.headers.get("X-Via", "api"),
                upload,
            )
            if (
                method == "POST"
                and path == "/api/v1/approval-requests"
                and status == 201
            ):
                try:
                    await notifications.notify(
                        accounts,
                        identity,
                        "approval:" + result["id"],
                        "Your agent wants to "
                        + result["summary"]
                        + ". Review the exact action and approve or deny: "
                        + accounts.origin
                        + "/agents",
                    )
                except Error:
                    pass  # The request remains visible in the human inbox if notifications are throttled.
            if method == "GET" and re.fullmatch(
                r"/api/v1/attachments/\d+/content", path
            ):
                blob = await self.env.FILES.get(
                    identity["workspace_id"] + "/blobs/" + result["sha256"]
                )
                if not blob:
                    raise Error(
                        503,
                        "missing_blob",
                        "This attachment is temporarily unavailable.",
                    )
                return Response(
                    blob.body,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Disposition": "attachment; filename=\"download\"; filename*=UTF-8''"
                        + quote(result["filename"], safe=""),
                    },
                )
            if method == "GET" and re.fullmatch(
                r"/api/v1/projects/[^/]+/participants", path
            ):
                rows = await accounts.many(
                    "SELECT m.id,m.user_id,u.name,u.email,m.kind,m.customer_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.status='active'",
                    identity["workspace_id"],
                )
                selected = [
                    row
                    for row in rows
                    if (result["all_internal"] and row["kind"] == "internal")
                    or (
                        row["id"] in result["membership_ids"]
                        and (
                            row["kind"] == "internal"
                            or row["customer_id"] == result["customer_id"]
                        )
                    )
                ]
                return Response.json(
                    {
                        "items": [
                            {k: row[k] for k in ("id", "user_id", "name", "kind")}
                            for row in selected
                        ]
                    }
                )
            return Response.json(result, status=status)
        if method == "GET" and (
            path in {"/", "/triage"}
            or re.fullmatch(
                r"/(projects/[A-Za-z0-9]+(?:/settings)?|tasks/[A-Za-z0-9-]+)", path
            )
        ):
            asset = await self.env.ASSETS.fetch(accounts.origin + "/")
            content = await asset.text()
            context = {
                k: identity[k]
                for k in (
                    "email",
                    "name",
                    "actor",
                    "csrf",
                    "workspace_id",
                    "workspace_name",
                )
            }
            initial = {}

            async def preload(route, query=None):
                _, value = await self.task_call(
                    identity, "/api/v1" + route, query=query
                )
                key = route + (
                    "?"
                    + "&".join(
                        k + "=" + quote(str(v), safe="")
                        for k, v in sorted(query.items())
                    )
                    if query
                    else ""
                )
                initial[key] = value
                return value

            tasks_path = path.startswith("/tasks/")
            if tasks_path:
                projects, task = await asyncio.gather(
                    preload("/projects", {"limit": "200"}), preload(path)
                )
                first_view = (
                    '<div class="page-head"><div><p class="eyebrow">'
                    + escape(task["reference"])
                    + "</p><h1>"
                    + escape(task["title"])
                    + "</h1></div></div>"
                )
            else:
                projects = await preload("/projects", {"limit": "200"})
                first_view = (
                    '<div class="page-head"><h1>Projects</h1></div><a class="triage-entry" href="/triage">TRIAGE · Recent work →</a><div class="project-grid">'
                    + "".join(
                        '<a class="project-tile" href="/projects/'
                        + escape(p["key"])
                        + '"><h2>'
                        + escape(p["name"])
                        + "</h2></a>"
                        for p in projects["items"]
                    )
                    + "</div>"
                )
                if path.startswith("/projects/"):
                    project = next(
                        (
                            p
                            for p in projects["items"]
                            if path.split("/")[2] in [p["key"], *p["aliases"]]
                        ),
                        None,
                    )
                    if not project:
                        project = await preload(
                            "/projects/by-key/" + path.split("/")[2]
                        )
                    pid = str(project["id"])
                    if not path.endswith("/settings"):
                        await preload("/board", {"project_id": pid, "limit": "20"})
                    first_view = (
                        '<div class="page-head"><h1>'
                        + escape(project["name"])
                        + "</h1></div>"
                    )
                elif path == "/triage":
                    await preload(
                        "/tasks", {"view": "summary", "sort": "recent", "limit": "30"}
                    )
                    first_view = '<div class="page-head"><h1>TRIAGE</h1></div>'
            content = content.replace(
                '<div id="initial-view"><h1>Projects</h1></div>', first_view
            )
            initial_json = (
                encode(initial)
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
            )
            content = content.replace(
                "</head>",
                '<script id="tt-bootstrap" type="application/json">'
                + initial_json
                + "</script></head>",
            )
            for asset in ("app.js", "style.css", "hosted.css", "favicon.svg"):
                content = content.replace(
                    '"/' + asset + '"', '"/' + asset + "?v=" + ASSET_VERSION + '"'
                )
            content = content.replace(
                "<head>",
                '<head><meta name="tt-context" content="'
                + escape(encode(context), quote=True)
                + '"><link rel="stylesheet" href="/hosted.css"><link rel="icon" href="/favicon.svg">',
                1,
            )
            for asset in ("hosted.css", "favicon.svg"):
                content = content.replace(
                    '"/' + asset + '"', '"/' + asset + "?v=" + ASSET_VERSION + '"'
                )
            return html(
                content.replace("Local work, lasting context", "A place for the work")
            )
        raise Error(404, "not_found", "Page not found.")

    async def account_api(self, request, accounts, identity, path, method):
        if identity["bearer"]:
            raise Error(
                403,
                "browser_session_required",
                "Sign in through the browser for account settings.",
            )
        if method != "POST":
            raise Error(405, "method", "Use POST for this action.")
        data = await body_data(request)
        accounts.csrf(request, identity, data)
        if path.startswith("/api/account/preview"):
            return await previews.api(
                self, request, accounts, identity, path, method, data
            )
        if path == "/api/account/invite":
            return Response.json({"url": await accounts.invite(identity)})
        if path == "/api/account/token":
            return Response.json(
                {"token": await accounts.create_token(identity, data.get("name", ""))}
            )
        if path == "/api/account/revoke-token":
            await accounts.run(
                "DELETE FROM api_tokens WHERE id=? AND user_id=? AND workspace_id=?",
                data.get("id", ""),
                identity["user_id"],
                identity["workspace_id"],
            )
            return Response.json({"revoked": True})
        if path == "/api/account/remove-member":
            await accounts.remove_member(identity, data.get("id", ""))
            return Response.json({"removed": True})
        if path == "/api/account/revoke-invite":
            accounts.owner(identity)
            await accounts.run(
                "DELETE FROM invites WHERE token_hash=? AND workspace_id=?",
                data.get("id", ""),
                identity["workspace_id"],
            )
            return Response.json({"revoked": True})
        if path == "/api/account/workspace-name":
            accounts.owner(identity)
            name = data.get("name", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise Error(
                    422, "workspace_name", "Use a workspace name of 1–80 characters."
                )
            await accounts.run(
                "UPDATE workspaces SET name=? WHERE id=?",
                name.strip(),
                identity["workspace_id"],
            )
            return Response.json({"saved": True})
        raise Error(404, "not_found", "Account action not found.")

    async def client_preview_api(self, request, accounts, identity, identifier, method):
        _, task = await self.task_call(identity, "/api/v1/tasks/" + identifier)
        wid = identity["workspace_id"]
        if method == "GET":
            row = await accounts.one(
                "SELECT version,image FROM client_previews WHERE workspace_id=? AND task_id=?",
                wid,
                task["id"],
            )
            return Response.json(
                row if row and row["version"] == task["version"] else {"version": None}
            )
        accounts.csrf(request, identity)
        if method != "PUT":
            raise Error(405, "method", "Use GET or PUT.")
        data = await body_data(request)
        if data.get("expected_version") != task["version"]:
            raise Error(
                409,
                "version_conflict",
                "The task changed. Reopen it to generate its latest preview.",
            )
        image = data.get("image")
        self.preview_png(image)
        await accounts.limit("preview:" + wid, 300, 60)
        await accounts.run(
            "INSERT INTO client_previews(workspace_id,task_id,version,image) VALUES(?,?,?,?) ON CONFLICT(workspace_id,task_id) DO UPDATE SET version=excluded.version,image=excluded.image WHERE excluded.version>=client_previews.version",
            wid,
            task["id"],
            task["version"],
            image,
        )
        return Response.json({"version": task["version"], "image": image})

    @staticmethod
    def preview_png(image):
        if not isinstance(image, str) or len(image) > 1400000:
            raise Error(422, "preview", "Create a PNG preview smaller than 1 MiB.")
        try:
            png = base64.b64decode(image, validate=True)
            if (
                len(png) < 33
                or len(png) > 1024 * 1024
                or png[:8] != b"\x89PNG\r\n\x1a\n"
                or png[12:16] != b"IHDR"
                or struct.unpack(">II", png[16:24]) != (1200, 630)
            ):
                raise ValueError("Invalid preview")
        except (ValueError, TypeError):
            raise Error(
                422, "preview", "The preview must be a 1200 × 630 PNG."
            ) from None
        return png

    async def share_api(self, request, accounts, identity, match, method):
        _, task = await self.task_call(identity, "/api/v1/tasks/" + match[1])
        wid = identity["workspace_id"]
        if method == "GET" and not match[2]:
            rows = await accounts.many(
                "SELECT id,token,created_at FROM shares WHERE workspace_id=? AND task_id=? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 50",
                wid,
                task["id"],
            )
            return Response.json(
                {
                    "items": [
                        {
                            "id": s["id"],
                            "url": accounts.origin + "/s/" + s["token"],
                            "created_at": s["created_at"],
                        }
                        for s in rows
                    ]
                }
            )
        accounts.csrf(request, identity)
        if method == "DELETE" and match[2]:
            await accounts.run(
                "UPDATE shares SET revoked_at=? WHERE id=? AND workspace_id=? AND task_id=?",
                timestamp(),
                match[2],
                wid,
                task["id"],
            )
            return Response.json({"revoked": True})
        if method != "POST" or match[2]:
            raise Error(405, "method", "Use POST to create a share link.")
        data = await body_data(request)
        summary, image = data.get("summary"), data.get("image")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 4000:
            raise Error(
                422, "summary", "Write a customer update of 1–4,000 characters."
            )
        if data.get("use_cached_preview"):
            cached = await accounts.one(
                "SELECT version,image FROM client_previews WHERE workspace_id=? AND task_id=?",
                wid,
                task["id"],
            )
            if not cached or cached["version"] != task["version"]:
                raise Error(
                    409,
                    "preview_pending",
                    "Generating client preview. Reopen the task and try again.",
                )
            image = cached["image"]
        png = self.preview_png(image)
        request_key = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(request_key) <= 200:
            raise Error(422, "request_key", "Supply an Idempotency-Key.")
        fingerprint = digest(
            encode(
                {
                    "task_id": task["id"],
                    "summary": summary,
                    "image": hashlib.sha256(png).hexdigest(),
                }
            )
        )
        old = await accounts.one(
            "SELECT * FROM shares WHERE workspace_id=? AND created_by=? AND request_key=?",
            wid,
            identity["user_id"],
            request_key,
        )
        if old:
            if old["fingerprint"] != fingerprint:
                raise Error(
                    409,
                    "idempotency_conflict",
                    "This request key was used for different share content.",
                )
            if old["revoked_at"]:
                raise Error(
                    409,
                    "share_revoked",
                    "This share link was revoked. Create a new link.",
                )
            return Response.json(
                {"id": old["id"], "url": accounts.origin + "/s/" + old["token"]},
                status=201,
            )
        await accounts.limit("shares:" + wid, 30, 3600)
        if data.get("expected_version") != task["version"]:
            raise Error(
                409,
                "version_conflict",
                "The task changed. Try again to share its latest version.",
            )
        identifier, secret = str(uuid.uuid4()), token()
        key = wid + "/shares/" + identifier + ".png"
        snapshot = {k: task[k] for k in ("title", "status", "reference", "updated_at")}
        await self.env.FILES.put(key, png)
        await accounts.run(
            "INSERT OR IGNORE INTO shares VALUES (?,?,?,?,?,?,NULL,?,?,?,?,?)",
            identifier,
            secret,
            wid,
            task["id"],
            identity["user_id"],
            timestamp(),
            summary.strip(),
            encode(snapshot),
            key,
            request_key,
            fingerprint,
        )
        saved = await accounts.one(
            "SELECT * FROM shares WHERE workspace_id=? AND created_by=? AND request_key=?",
            wid,
            identity["user_id"],
            request_key,
        )
        if saved["fingerprint"] != fingerprint:
            raise Error(
                409,
                "idempotency_conflict",
                "This request key was used for different share content.",
            )
        return Response.json(
            {"id": saved["id"], "url": accounts.origin + "/s/" + saved["token"]},
            status=201,
        )

    async def scheduled(self, controller, env, ctx):
        accounts = Accounts(self.env)
        now = timestamp()
        await accounts.db.batch(
            [
                accounts.statement("DELETE FROM sessions WHERE expires_at<?", now),
                accounts.statement("DELETE FROM login_links WHERE expires_at<?", now),
                accounts.statement("DELETE FROM login_codes WHERE expires_at<?", now),
                accounts.statement(
                    "DELETE FROM passkey_challenges WHERE expires_at<?", now
                ),
                accounts.statement(
                    "DELETE FROM preview_grants WHERE expires_at<?", now
                ),
                accounts.statement(
                    "DELETE FROM preview_sessions WHERE expires_at<? OR session_hash NOT IN (SELECT token_hash FROM sessions)",
                    now,
                ),
                accounts.statement("DELETE FROM api_tokens WHERE expires_at<?", now),
                accounts.statement(
                    "DELETE FROM service_app_tokens WHERE expires_at<?", now
                ),
                accounts.statement(
                    "DELETE FROM human_challenges WHERE expires_at<?", now
                ),
                accounts.statement(
                    "DELETE FROM import_oauth_states WHERE expires_at<?", now
                ),
                accounts.statement(
                    "UPDATE agent_enrollments SET status='expired' WHERE expires_at<? AND status IN ('pending','approved')",
                    now,
                ),
                accounts.statement(
                    "UPDATE security_deliveries SET status='expired',body='' WHERE expires_at<? AND status='queued'",
                    now,
                ),
                accounts.statement(
                    "UPDATE security_deliveries SET status='uncertain' WHERE lease_expires<? AND status='claimed'",
                    now,
                ),
                accounts.statement(
                    "UPDATE security_deliveries SET body='' WHERE expires_at<?",
                    now - 86400,
                ),
                accounts.statement(
                    "DELETE FROM agent_notifications WHERE created_at<?",
                    now - 30 * 86400,
                ),
                accounts.statement(
                    "DELETE FROM agent_enrollments WHERE expires_at<?", now - 30 * 86400
                ),
                accounts.statement("DELETE FROM invites WHERE expires_at<?", now),
                accounts.statement(
                    "DELETE FROM rate_limits WHERE updated_at<?", now - 86400
                ),
            ]
        )
