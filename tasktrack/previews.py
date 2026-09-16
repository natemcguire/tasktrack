"""Private, project-scoped preview documents with host-only sign-in handoff."""

import re
import uuid
from html import escape as esc
from urllib.parse import quote

from workers import Response

from .accounts import cookie_value, digest, timestamp, token
from .db import Error
from .hosted_pages import page


def origin(env):
    return getattr(env, "PREVIEW_URL", "").rstrip("/")


def cookie(accounts, name, value, age):
    prefix = "" if accounts.local else "__Host-"
    return f"{prefix}{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}" + (
        "" if accounts.local else "; Secure"
    )


def read_cookie(accounts, request, name):
    return cookie_value(request, ("" if accounts.local else "__Host-") + name)


async def allowed(worker, accounts, identity, preview):
    # Session membership is rechecked by authenticate on every document request.
    if not identity:
        return False
    member = await accounts.member_identity(
        identity["user_id"], preview["workspace_id"]
    )
    if not member:
        return False
    if (
        member["kind"] == "external"
        and getattr(worker.env, "CUSTOMER_ACCESS_V2", "") != "1"
    ):
        return False
    try:
        await worker.task_call(member, "/api/v1/projects/" + str(preview["project_id"]))
    except Error:
        return False
    if member["access_role"] == "admin":
        return True
    return bool(
        await accounts.one(
            "SELECT 1 FROM preview_projects p WHERE p.workspace_id=? AND p.project_id=? AND (p.team_access=1 OR EXISTS(SELECT 1 FROM preview_members m WHERE m.workspace_id=p.workspace_id AND m.project_id=p.project_id AND m.email=?))",
            preview["workspace_id"],
            preview["project_id"],
            identity["email"],
        )
    )


async def lookup(worker, accounts, identity, identifier):
    row = await accounts.one(
        "SELECT * FROM previews WHERE id=? AND revoked_at IS NULL", identifier
    )
    if not row or not await allowed(worker, accounts, identity, row):
        raise Error(
            404, "preview_missing", "This preview is unavailable for your account."
        )
    return row


async def api(worker, request, accounts, identity, path, method, data):
    accounts.owner(identity)
    accounts.csrf(request, identity)
    wid = identity["workspace_id"]
    if path == "/api/account/previews" and method == "POST":
        if not origin(worker.env):
            raise Error(503, "preview_unconfigured", "Configure a preview host first.")
        _, project = await worker.task_call(
            identity, "/api/v1/projects/" + str(data.get("project_id", ""))
        )
        title, content = data.get("title"), data.get("html")
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 120
            or not isinstance(content, str)
            or not 1 <= len(content.encode()) <= 1024 * 1024
        ):
            raise Error(
                422, "preview_content", "Supply a title and HTML document up to 1 MiB."
            )
        identifier = str(uuid.uuid4())
        key = wid + "/previews/" + identifier + ".html"
        await worker.env.FILES.put(key, content)
        await accounts.run(
            "INSERT INTO previews VALUES (?,?,?,?,?,?,NULL)",
            identifier,
            wid,
            project["id"],
            title.strip(),
            key,
            timestamp(),
        )
        return Response.json(
            {"id": identifier, "url": origin(worker.env) + "/p/" + identifier},
            status=201,
        )
    match = re.fullmatch(r"/api/account/preview-projects/(\d+)", path)
    if match and method == "POST":
        _, project = await worker.task_call(identity, "/api/v1/projects/" + match[1])
        emails, team = data.get("emails", []), data.get("team_access", False)
        if (
            type(team) is not bool
            or not isinstance(emails, list)
            or len(emails) > 100
            or any(
                not isinstance(e, str)
                or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", e)
                or len(e) > 254
                for e in emails
            )
        ):
            raise Error(
                422,
                "preview_access",
                "Supply team_access and up to 100 teammate email addresses.",
            )
        pid = project["id"]
        await accounts.db.batch(
            [
                accounts.statement(
                    "INSERT INTO preview_projects VALUES (?,?,?) ON CONFLICT(workspace_id,project_id) DO UPDATE SET team_access=excluded.team_access",
                    wid,
                    pid,
                    int(team),
                ),
                accounts.statement(
                    "DELETE FROM preview_members WHERE workspace_id=? AND project_id=?",
                    wid,
                    pid,
                ),
                *[
                    accounts.statement(
                        "INSERT OR IGNORE INTO preview_members VALUES (?,?,?)",
                        wid,
                        pid,
                        e.lower(),
                    )
                    for e in emails
                ],
            ]
        )
        return Response.json({"saved": True})
    match = re.fullmatch(r"/api/account/previews/([a-f0-9-]{36})/revoke", path)
    if match and method == "POST":
        await accounts.run(
            "UPDATE previews SET revoked_at=? WHERE id=? AND workspace_id=?",
            timestamp(),
            match[1],
            wid,
        )
        return Response.json({"revoked": True})
    raise Error(404, "not_found", "Preview action not found.")


async def authorize(worker, request, accounts, query):
    identifier, challenge = query.get("id", ""), query.get("challenge", "")
    if (
        not origin(worker.env)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)
        or not re.fullmatch(r"[a-f0-9-]{36}", identifier)
    ):
        raise Error(400, "preview_request", "Open the preview link again.")
    identity = await accounts.authenticate(request)
    if not identity:
        # Relative allowlisted route survives code/link sign-in, including invitation flow.
        return Response(
            None,
            status=303,
            headers={
                "Location": "/login?next="
                + quote("/preview/return/" + identifier + "/" + challenge, safe="")
            },
        )
    if identity["bearer"]:
        raise Error(403, "browser_session_required", "Sign in through your browser.")
    await lookup(worker, accounts, identity, identifier)
    grant = token()
    await accounts.run(
        "INSERT INTO preview_grants VALUES (?,?,?,?,?)",
        digest(grant),
        digest(challenge),
        identity["session_hash"],
        identifier,
        timestamp() + 60,
    )
    return Response(
        None,
        status=303,
        headers={"Location": origin(worker.env) + "/callback?ticket=" + grant},
    )


async def route(worker, request, accounts, path, query):
    if request.method not in {"GET", "HEAD"}:
        raise Error(405, "method", "Use the main application to manage previews.")
    if path == "/callback":
        challenge = read_cookie(accounts, request, "preview_state")
        ticket = query.get("ticket", "")
        if not challenge or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            raise Error(
                400, "preview_handoff", "Open the preview link again in this browser."
            )
        grant = await accounts.one(
            "DELETE FROM preview_grants WHERE token_hash=? AND challenge_hash=? AND expires_at>? RETURNING session_hash,preview_id",
            digest(ticket),
            digest(challenge),
            timestamp(),
        )
        if not grant:
            raise Error(
                400,
                "preview_handoff",
                "This sign-in handoff expired. Open the preview link again.",
            )
        secret = token()
        await accounts.run(
            "INSERT INTO preview_sessions VALUES (?,?,?)",
            digest(secret),
            grant["session_hash"],
            timestamp() + 30 * 86400,
        )
        return Response(
            None,
            status=303,
            headers={
                "Location": "/p/" + grant["preview_id"],
                "Set-Cookie": cookie(accounts, "preview_session", secret, 30 * 86400),
            },
        )
    match = re.fullmatch(r"/p/([a-f0-9-]{36})(/content)?", path)
    if not match:
        return Response(None, status=303, headers={"Location": accounts.origin + "/"})
    secret = read_cookie(accounts, request, "preview_session")
    identity = (
        await accounts.authenticate(request, preview_secret=secret) if secret else None
    )
    if not identity:
        if match[2]:
            raise Error(401, "sign_in_required", "Open the preview to sign in.")
        challenge = token()
        return Response(
            None,
            status=303,
            headers={
                "Location": accounts.origin
                + "/preview/authorize?id="
                + match[1]
                + "&challenge="
                + challenge,
                "Set-Cookie": cookie(accounts, "preview_state", challenge, 300),
            },
        )
    row = await lookup(worker, accounts, identity, match[1])
    if match[2]:
        blob = await worker.env.FILES.get(row["object_key"])
        if not blob:
            raise Error(404, "preview_missing", "This preview is unavailable.")
        return Response(
            blob.body,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": "sandbox allow-scripts allow-modals; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
            },
        )
    content = f'''<main class="preview-shell"><header><h1>{esc(row["title"])}</h1><a href="{esc(accounts.origin)}" class="button">Back to projects</a></header><p class="small muted">Design preview · sample data. Leave feedback in the linked task.</p><iframe title="{esc(row["title"])}" sandbox="allow-scripts allow-modals" src="/p/{esc(row["id"])}/content"></iframe></main>'''
    return Response(
        page(row["title"], content, '<script src="/preview.js" defer></script>'),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
