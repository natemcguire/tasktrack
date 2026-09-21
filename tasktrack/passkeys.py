"""Browser-bound, single-use passkey ceremonies; verified by SimpleWebAuthn."""

import base64
import json
import re
from urllib.parse import urlsplit

from workers import Response, import_from_javascript

from .accounts import SESSION_AGE, cookie_value, digest, safe_next, timestamp, token
from .db import Error


def cookie_name(accounts):
    return "tt_passkey" if accounts.local else "__Host-tt_passkey"


def cookie(accounts, secret, clear=False):
    return (
        f"{cookie_name(accounts)}={secret}; Path=/; HttpOnly; SameSite=Lax; Max-Age={0 if clear else 300}"
        + ("" if accounts.local else "; Secure")
    )


def user_handle(uid):
    return base64.urlsafe_b64encode(uid.encode()).decode().rstrip("=")


async def step_up_options(accounts, identity, binding):
    from .agent_auth import browser

    browser(identity)
    await accounts.limit("human-challenge:" + identity["user_id"], 30)
    keys = await accounts.many(
        "SELECT id FROM passkeys WHERE user_id=?", identity["user_id"]
    )
    if not keys:
        raise Error(
            403,
            "passkey_required",
            "Add a passkey in Account settings before approving agent access.",
        )
    secret, challenge = token(), token()
    await accounts.run(
        "INSERT INTO human_challenges VALUES(?,?,?,?,?,?)",
        digest(secret),
        challenge,
        identity["user_id"],
        identity["session_hash"],
        binding,
        timestamp() + 300,
    )
    return {
        "challenge_id": secret,
        "options": {
            "challenge": challenge,
            "rpId": urlsplit(accounts.origin).hostname,
            "timeout": 60000,
            "userVerification": "required",
            "allowCredentials": [
                {"id": key["id"], "type": "public-key"} for key in keys
            ],
        },
    }


async def step_up_verify(accounts, identity, binding, data):
    from .agent_auth import browser

    browser(identity)
    cid = data.get("challenge_id")
    if not isinstance(cid, str) or len(cid) > 200:
        raise Error(422, "challenge_required", "Begin a new approval confirmation.")
    row = await accounts.one(
        "DELETE FROM human_challenges WHERE id_hash=? AND user_id=? AND session_hash=? AND binding=? AND expires_at>? RETURNING *",
        digest(cid),
        identity["user_id"],
        identity["session_hash"],
        binding,
        timestamp(),
    )
    if not row:
        raise Error(
            400,
            "challenge_expired",
            "Approval confirmation expired or changed. Review it again.",
        )
    response = data.get("credential")
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("id"), str)
        or len(json.dumps(response)) > 100000
    ):
        raise Error(422, "passkey_invalid", "Invalid passkey response.")
    key = await accounts.one(
        "SELECT * FROM passkeys WHERE id=? AND user_id=?",
        response["id"],
        identity["user_id"],
    )
    if not key:
        raise Error(403, "passkey_invalid", "Use a passkey belonging to this account.")
    verifier = import_from_javascript("passkey-server.mjs")
    result = json.loads(
        await verifier.verify(
            json.dumps(
                {
                    "kind": "login",
                    "response": response,
                    "challenge": row["challenge"],
                    "origin": accounts.origin,
                    "rpID": urlsplit(accounts.origin).hostname,
                    "credential": key,
                }
            )
        )
    )
    if not result.get("verified"):
        raise Error(403, "passkey_invalid", "Passkey verification failed.")
    updated = await accounts.one(
        "UPDATE passkeys SET counter=?,last_used_at=? WHERE id=? AND user_id=? AND counter=? RETURNING id",
        result["counter"],
        timestamp(),
        key["id"],
        identity["user_id"],
        key["counter"],
    )
    if not updated:
        raise Error(
            409, "passkey_changed", "Passkey changed. Review this request again."
        )


async def browser_identity(accounts, request):
    identity = await accounts.authenticate(request)
    if not identity:
        raise Error(401, "sign_in_required", "Sign in to manage passkeys.")
    if identity["bearer"]:
        raise Error(
            403,
            "browser_session_required",
            "Manage passkeys from your signed-in browser.",
        )
    accounts.csrf(request, identity)
    # Existing sessions have a fixed 30-day expiry; switching workspaces does not extend it.
    row = await accounts.one(
        "SELECT expires_at FROM sessions WHERE token_hash=?", identity["session_hash"]
    )
    if not row or row["expires_at"] - SESSION_AGE < timestamp() - 600:
        raise Error(403, "recent_sign_in_required", "Sign in again to manage passkeys.")
    return identity


async def route(accounts, request, path, data):
    if request.method != "POST":
        raise Error(405, "method", "Use POST.")
    if request.headers.get("Origin") != accounts.origin:
        raise Error(
            403, "foreign_origin", "Use this Tasktrack site to manage passkeys."
        )
    await accounts.limit(
        "passkey-ip:" + request.headers.get("CF-Connecting-IP", "local"), 60
    )
    action = path.removeprefix("/auth/passkeys/")
    if action not in {
        "register/options",
        "register/verify",
        "login/options",
        "login/verify",
        "remove",
    }:
        raise Error(404, "not_found", "Passkey route not found.")
    identity = None
    if action.startswith("register") or action == "remove":
        identity = await browser_identity(accounts, request)
    if action == "remove":
        row = await accounts.one(
            "SELECT id FROM passkeys WHERE id=? AND user_id=?",
            data.get("id", ""),
            identity["user_id"],
        )
        if not row:
            raise Error(404, "passkey_missing", "Passkey not found.")
        # Removal also invalidates previously authenticated passkey sessions using this key.
        await accounts.db.batch(
            [
                accounts.statement(
                    "DELETE FROM sessions WHERE token_hash IN (SELECT session_hash FROM passkey_sessions WHERE credential_id=?)",
                    row["id"],
                ),
                accounts.statement(
                    "DELETE FROM passkeys WHERE id=? AND user_id=?",
                    row["id"],
                    identity["user_id"],
                ),
            ]
        )
        return Response.json({"removed": True})
    kind = action.split("/")[0]
    rp_id = urlsplit(accounts.origin).hostname
    if action.endswith("/options"):
        secret, challenge = token(), token()
        options = {
            "challenge": challenge,
            "timeout": 60000,
            "userVerification": "required",
        }
        if kind == "register":
            keys = await accounts.many(
                "SELECT id FROM passkeys WHERE user_id=?", identity["user_id"]
            )
            if len(keys) >= 10:
                raise Error(
                    422, "passkey_limit", "Remove an old passkey before adding another."
                )
            options = {
                "challenge": challenge,
                "timeout": 60000,
                "rp": {"id": rp_id, "name": "Tasktrack"},
                "user": {
                    "id": user_handle(identity["user_id"]),
                    "name": identity["email"],
                    "displayName": identity["name"],
                },
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
                "attestation": "none",
                "authenticatorSelection": {
                    "residentKey": "required",
                    "userVerification": "required",
                },
                "excludeCredentials": [
                    {"id": k["id"], "type": "public-key"} for k in keys
                ],
            }
        else:
            options["rpId"] = rp_id
        await accounts.run(
            "INSERT INTO passkey_challenges VALUES(?,?,?,?,?,?,?)",
            digest(secret),
            challenge,
            kind,
            identity["user_id"] if identity else None,
            identity["session_hash"] if identity else None,
            safe_next(data.get("next")),
            timestamp() + 300,
        )
        return Response.json(options, headers={"Set-Cookie": cookie(accounts, secret)})
    secret = cookie_value(request, cookie_name(accounts))
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
        raise Error(400, "passkey_expired", "Start passkey sign-in again.")
    # Consume before verification, so success, failure and concurrent retries all use it once.
    row = await accounts.one(
        "DELETE FROM passkey_challenges WHERE id_hash=? AND kind=? AND expires_at>? RETURNING *",
        digest(secret),
        kind,
        timestamp(),
    )
    if not row or (
        identity
        and (
            row["user_id"] != identity["user_id"]
            or row["session_hash"] != identity["session_hash"]
        )
    ):
        raise Error(
            400,
            "passkey_expired",
            "This request expired or was already used. Try again.",
        )
    response = data.get("credential")
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("response"), dict)
        or not isinstance(response.get("id"), str)
        or len(json.dumps(response)) > 100000
    ):
        raise Error(422, "passkey_invalid", "Invalid passkey response.")
    credential = None
    if kind == "login":
        credential = await accounts.one(
            "SELECT * FROM passkeys WHERE id=?", response.get("id", "")
        )
        handle = (response.get("response") or {}).get("userHandle")
        if not credential or handle != user_handle(credential["user_id"]):
            raise Error(
                400,
                "passkey_invalid",
                "Passkey sign-in failed. Try again or use an email code.",
            )
    verifier = import_from_javascript("passkey-server.mjs")
    result = json.loads(
        await verifier.verify(
            json.dumps(
                {
                    "kind": kind,
                    "response": response,
                    "challenge": row["challenge"],
                    "origin": accounts.origin,
                    "rpID": rp_id,
                    "credential": credential,
                }
            )
        )
    )
    if not result.get("verified"):
        raise Error(
            400,
            "passkey_invalid",
            "Passkey verification failed. Try again or use an email code.",
        )
    if kind == "register":
        name = data.get("name", "Passkey")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise Error(422, "passkey_name", "Use a passkey name of 1–80 characters.")
        if await accounts.one("SELECT id FROM passkeys WHERE id=?", result["id"]):
            raise Error(409, "passkey_exists", "That passkey is already registered.")
        saved = await accounts.one(
            "INSERT INTO passkeys SELECT ?,?,?,?,?,?,NULL,?,? WHERE EXISTS(SELECT 1 FROM sessions s JOIN memberships m ON m.user_id=s.user_id AND m.workspace_id=s.workspace_id AND m.status='active' WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>?) AND (SELECT COUNT(*) FROM passkeys WHERE user_id=?)<10 RETURNING id",
            result["id"],
            identity["user_id"],
            result["public_key"],
            result["counter"],
            name.strip(),
            timestamp(),
            int(result["backed_up"]),
            result["device_type"],
            identity["session_hash"],
            identity["user_id"],
            timestamp(),
            identity["user_id"],
        )
        if not saved:
            raise Error(
                409,
                "passkey_registration_changed",
                "Your session or passkeys changed. Sign in again and retry.",
            )
        return Response.json(
            {"registered": True}, headers={"Set-Cookie": cookie(accounts, "", True)}
        )
    # Counter compare-and-swap rejects concurrent stale assertions and deleted credentials.
    updated = await accounts.one(
        "UPDATE passkeys SET counter=?,last_used_at=? WHERE id=? AND counter=? RETURNING user_id",
        result["counter"],
        timestamp(),
        credential["id"],
        credential["counter"],
    )
    if not updated:
        raise Error(400, "passkey_invalid", "Passkey changed. Start sign-in again.")
    session, csrf = token(), token()
    await accounts.db.batch(
        [
            accounts.statement(
                "INSERT INTO sessions SELECT ?,p.user_id,(SELECT workspace_id FROM memberships WHERE user_id=p.user_id AND status='active' ORDER BY created_at,workspace_id LIMIT 1),?,? FROM passkeys p WHERE p.id=?",
                digest(session),
                csrf,
                timestamp() + SESSION_AGE,
                credential["id"],
            ),
            accounts.statement(
                "INSERT INTO passkey_sessions SELECT ?,id FROM passkeys WHERE id=?",
                digest(session),
                credential["id"],
            ),
        ]
    )
    if not await accounts.one(
        "SELECT token_hash FROM sessions WHERE token_hash=?", digest(session)
    ):
        raise Error(400, "passkey_invalid", "Passkey changed. Start sign-in again.")
    return Response.json(
        {"next": safe_next(row["next_path"])},
        headers={"Set-Cookie": accounts.cookie(session)},
    )
