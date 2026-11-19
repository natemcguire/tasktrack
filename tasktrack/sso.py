"""Live tenant/product authorization delegated to Nate's SSO hub."""

import json
import secrets
import uuid
from html import escape
from urllib.parse import urlencode

from .accounts import SESSION_AGE, cookie_value, digest, safe_next, timestamp, token
from .db import Error
from .hosted_pages import sso_page as page

PRODUCT = "tasktrack"
TENANT = "nates-software"
OWNER = "nate.mcguire@gmail.com"


def enabled(accounts):
    return getattr(accounts.env, "SSO_REQUIRED", "") == "1"


async def hub(accounts, path, data):
    from workers import Request

    response = await accounts.env.AUTH_HUB.fetch(
        Request(
            "https://auth.internal" + path,
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(data),
        )
    )
    if response.status >= 500:
        raise Error(503, "sso_unavailable", "Sign-in is temporarily unavailable.")
    payload = await response.json()
    if response.status >= 400:
        return {}
    return payload


def allowed(claims):
    return (
        claims.get("active") is True
        and claims.get("tenant_id") == TENANT
        and PRODUCT in claims.get("products", [])
    )


async def entitlement(accounts, email):
    return allowed(
        await hub(
            accounts,
            "/internal/entitlement",
            {"email": email, "audience": PRODUCT, "tenant_id": TENANT},
        )
    )


async def authorize_identity(accounts, identity):
    if not identity or not enabled(accounts):
        return identity
    if identity["bearer"]:
        email = identity.get("email")
        if identity.get("app_id"):
            creator = await accounts.one(
                "SELECT u.email FROM service_apps a JOIN users u ON u.id=a.created_by WHERE a.id=?",
                identity["app_id"],
            )
            email = creator["email"] if creator else None
        return identity if email and await entitlement(accounts, email) else None
    row = await accounts.one(
        "SELECT hub_token FROM sso_sessions WHERE session_hash=?",
        identity["session_hash"],
    )
    if not row:
        return None
    claims = await hub(
        accounts,
        "/session/introspect",
        {"token": row["hub_token"], "audience": PRODUCT},
    )
    return (
        identity
        if allowed(claims)
        and claims.get("email", "").lower() == identity["email"].lower()
        else None
    )


async def create_session(accounts, exchange):
    user = exchange.get("user", {})
    email = user.get("email", "").lower()
    if (
        exchange.get("tenant_id") != TENANT
        or PRODUCT not in exchange.get("products", [])
        or not email
        or not exchange.get("token")
    ):
        raise Error(
            403,
            "product_access_required",
            "A Nate's Software account with Tasks access is required. Create one from the sign-in page.",
        )
    # Preserve all existing account IDs and workspaces by matching the hub email.
    # The hub refuses open sign-up for any address already on its member list, so
    # every existing Tasks member must have a hub member row before sign-up opens.
    uid = str(uuid.uuid4())
    await accounts.run(
        "INSERT OR IGNORE INTO users VALUES (?,?,?,?)",
        uid,
        email,
        user.get("name") or email.split("@")[0],
        timestamp(),
    )
    existing = await accounts.one("SELECT id,name FROM users WHERE email=?", email)
    uid = existing["id"]
    landing = "SELECT workspace_id FROM memberships WHERE user_id=? AND status='active' ORDER BY created_at,workspace_id LIMIT 1"
    membership = await accounts.one(landing, uid)
    if not membership and email == OWNER:
        owner_workspace = await accounts.one(
            "SELECT w.id FROM workspaces w JOIN users u ON u.id=w.created_by WHERE u.email=? ORDER BY w.created_at,w.id LIMIT 1",
            OWNER,
        )
        if not owner_workspace:
            wid = str(uuid.uuid4())
            await accounts.run(
                "INSERT INTO workspaces(id,name,created_by,created_at) VALUES (?,?,?,?)",
                wid,
                "Nate's Software",
                uid,
                timestamp(),
            )
        else:
            wid = owner_workspace["id"]
        await accounts.run(
            "INSERT OR IGNORE INTO memberships(workspace_id,user_id,role,created_at) VALUES (?,?,?,?)",
            wid,
            uid,
            "owner",
            timestamp(),
        )
        membership = {"workspace_id": wid}
    elif not membership:
        # Sign-up is open: a new person gets a workspace of their own and never
        # joins someone else's. Joining another workspace takes its invitation.
        await accounts.db.batch(
            accounts.own_workspace(
                str(uuid.uuid4()), uid, existing["name"], timestamp()
            )
        )
        membership = await accounts.one(landing, uid)
        if not membership:
            raise Error(
                503,
                "workspace_unavailable",
                "Your workspace could not be opened. Try signing in again.",
            )
    secret = token()
    await accounts.db.batch(
        [
            accounts.statement(
                "INSERT INTO sessions VALUES (?,?,?,?,?)",
                digest(secret),
                uid,
                membership["workspace_id"],
                token(),
                timestamp() + SESSION_AGE,
            ),
            accounts.statement(
                "INSERT INTO sso_sessions(session_hash,hub_token) VALUES (?,?)",
                digest(secret),
                exchange["token"],
            ),
        ]
    )
    return secret


async def route(request, accounts, path, query, body_data, html, redirect):
    from workers import Response

    if enabled(accounts) and path in {"/login", "/signup"} and request.method in {"GET", "HEAD"}:
        # The hub's pages are already branded Tasks; skip a second screen here.
        # /signup opens the hub on account creation, /login on sign-in.
        start = {"next": safe_next(query.get("next"))}
        if path == "/signup":
            start["intent"] = "signup"
        return redirect("/auth/sso/start?" + urlencode(start))
    if path == "/auth/sso/start" and request.method in {"GET", "HEAD"}:
        await accounts.limit(
            "sso-login:" + request.headers.get("CF-Connecting-IP", "local"), 60, 600
        )
        await accounts.run("DELETE FROM sso_states WHERE expires_at<=?", timestamp())
        state = token()
        await accounts.run(
            "INSERT INTO sso_states(state_hash,next_path,expires_at) VALUES (?,?,?)",
            digest(state),
            safe_next(query.get("next")),
            timestamp() + 600,
        )
        url = "https://auth.nates-software.com/authorize?" + urlencode(
            {
                "app": PRODUCT,
                "return": accounts.origin + "/auth/sso/callback",
                "state": state,
            }
            | ({"intent": "signup"} if query.get("intent") == "signup" else {})
        )
        return redirect(
            url,
            "__Host-tt_sso_state="
            + state
            + "; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600",
        )
    if path == "/auth/sso/callback":
        if request.method in {"GET", "HEAD"}:
            return html(
                page(
                    "Signing in",
                    '<main class="auth-card"><div class="sso-crosshair" aria-hidden="true"></div><h1>Opening Tasks</h1>'
                    '<p id="status" role="status">Checking your sign-in…</p>'
                    '<p><a class="button" id="retry" href="/login" hidden>Try signing in again</a></p></main>',
                    '<script src="/sso.js" defer></script>',
                )
            )
        if request.method == "POST":
            data = await body_data(request)
            state = data.get("state", "")
            expected = cookie_value(request, "__Host-tt_sso_state")
            if (
                not isinstance(state, str)
                or not expected
                or not secrets.compare_digest(state, expected)
            ):
                raise Error(403, "invalid_state", "Restart sign-in from Tasks.")
            saved = await accounts.one(
                "DELETE FROM sso_states WHERE state_hash=? AND expires_at>? RETURNING next_path",
                digest(state),
                timestamp(),
            )
            if not saved:
                raise Error(403, "expired_state", "Restart sign-in from Tasks.")
            exchange = await hub(
                accounts,
                "/session/exchange",
                {"token": data.get("token", ""), "audience": PRODUCT},
            )
            secret = await create_session(accounts, exchange)
            return Response.json(
                {"next": saved["next_path"]},
                headers={"Set-Cookie": accounts.cookie(secret)},
            )
    # Sign-in belongs to the hub, so passkey *login* stays closed here. Registering a
    # passkey, removing one and the approval password are step-up credentials for
    # approvals inside Tasktrack; they need the signed-in browser and stay available.
    if path.startswith("/auth/") and path != "/auth/logout" and path not in STEP_UP_ROUTES:
        raise Error(403, "sso_required", "Sign in with Nate's Software SSO.")
    return None


STEP_UP_ROUTES = frozenset(
    "/auth/passkeys/" + action
    for action in (
        "register/options",
        "register/verify",
        "remove",
        "password/set",
        "password/remove",
    )
)


async def logout(accounts, identity):
    row = await accounts.one(
        "SELECT hub_token FROM sso_sessions WHERE session_hash=?",
        identity["session_hash"],
    )
    if not row:
        raise Error(
            503,
            "logout_unavailable",
            "Could not sign out of all apps. Please try again.",
        )
    result = await hub(
        accounts, "/session/logout", {"token": row["hub_token"], "audience": PRODUCT}
    )
    if result.get("ok") is not True:
        raise Error(
            503,
            "logout_unavailable",
            "Could not sign out of all apps. Please try again.",
        )
    return "https://auth.nates-software.com/signed-out?app=" + PRODUCT
