"""Cloudflare-hosted Tasktrack: authenticated routes and isolated workspaces."""

import base64
import hashlib
import json
import re
import struct
import uuid
from html import escape
from urllib.parse import parse_qs, quote, unquote, urlsplit

from workers import DurableObject, Request, Response, WorkerEntrypoint

from tasktrack.accounts import Accounts, digest, safe_next, timestamp, token
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
            if parsed.netloc != urlsplit(accounts.origin).netloc:
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
            response = await self.route(
                request, accounts, path, {k: v[0] for k, v in pairs.items()}
            )
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
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "strict-origin",
                "X-Robots-Tag": "noindex, nofollow",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
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
            in {"/app.js", "/style.css", "/hosted.js", "/hosted.css", "/favicon.svg"}
            and method == "GET"
        ):
            return await self.env.ASSETS.fetch(request)
        if path == "/healthz" and method == "GET":
            await accounts.one("SELECT 1 FROM users LIMIT 1")
            return Response.json({"status": "ok", "storage": "cloudflare"})
        if path in {"/login", "/signup"} and method == "GET":
            return html(auth_page(path[1:], safe_next(query.get("next"))))
        if path == "/auth/link" and method == "POST":
            data = await body_data(request, form=True)
            await accounts.login_link(
                data.get("email"),
                data.get("next"),
                request.headers.get("CF-Connecting-IP", "local"),
            )
            return html(
                auth_page(
                    message="Your sign-in link is on its way. It expires in 15 minutes."
                )
            )
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
            if shared[2]:
                blob = await self.env.FILES.get(share["preview_key"])
                if not blob:
                    raise Error(
                        404, "image_missing", "The task preview is unavailable."
                    )
                return Response(blob.body, headers={"Content-Type": "image/png"})
            _, task = await self.task_call(
                share, "/api/v1/tasks/" + str(share["task_id"])
            )
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
        if path.startswith("/api/v1/"):
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
            return Response.json(result, status=status)
        if method == "GET" and (
            path == "/"
            or re.fullmatch(r"/(projects/[A-Za-z0-9]+|tasks/[A-Za-z0-9-]+)", path)
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
            content = content.replace(
                "<head>",
                '<head><meta name="tt-context" content="'
                + escape(encode(context), quote=True)
                + '"><link rel="stylesheet" href="/hosted.css"><link rel="icon" href="/favicon.svg">',
                1,
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
                accounts.statement("DELETE FROM api_tokens WHERE expires_at<?", now),
                accounts.statement("DELETE FROM invites WHERE expires_at<?", now),
                accounts.statement(
                    "DELETE FROM rate_limits WHERE updated_at<?", now - 86400
                ),
            ]
        )
