"""Device enrollment and scoped credentials. No bearer may approve itself."""

import json
import re
import secrets
import uuid

from .accounts import digest, timestamp, token
from .db import Error, encode

GRANT_AGE = 30 * 86400
ACCESS_AGE = 900
CAPABILITIES = frozenset(
    {
        "project.read",
        "work.read",
        "work.edit",
        "work.execute",
        "work.accept",
        "notes.read",
        "notes.write",
        "workflow.manage",
        "project.settings",
        "project.create",
        "integrations.manage",
        "notifications.deliver",
    }
)


def browser(identity):
    if not identity or identity.get("bearer") or identity.get("kind") != "internal":
        raise Error(
            403, "human_required", "Use your signed-in internal member browser."
        )


def scope(data):
    projects, caps = data.get("project_ids"), data.get("capabilities")
    if (
        not isinstance(projects, list)
        or not 1 <= len(projects) <= 200
        or any(type(x) is not int or x < 1 for x in projects)
    ):
        raise Error(422, "projects_required", "Choose 1–200 explicit project IDs.")
    if (
        not isinstance(caps, list)
        or not caps
        or len(caps) > len(CAPABILITIES)
        or any(not isinstance(x, str) or x not in CAPABILITIES for x in caps)
    ):
        raise Error(422, "invalid_scope", "Choose supported capabilities.")
    return sorted(set(projects)), sorted(set(caps))


def enrollment_binding(row):
    return digest(
        encode(
            {
                k: row[k]
                for k in ("id", "workspace_id", "name", "projects", "capabilities")
            }
        )
    )


def public_enrollment(row):
    return {
        k: row[k] for k in ("id", "workspace_id", "name", "status", "expires_at")
    } | {
        "project_ids": json.loads(row["projects"]),
        "capabilities": json.loads(row["capabilities"]),
        "binding": enrollment_binding(row),
    }


async def start(accounts, data, ip):
    await accounts.limit("device-start:" + ip, 10, 3600)
    name, wid = data.get("name"), data.get("workspace_id")
    if not isinstance(name, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,59}", name
    ):
        raise Error(
            422,
            "agent_name",
            "Use an agent name of 1–60 letters, numbers, spaces, dots or hyphens.",
        )
    if not isinstance(wid, str) or len(wid) > 80 or not wid:
        raise Error(
            422, "workspace_required", "Supply your workspace ID from account settings."
        )
    projects, caps = scope(data)
    device, code, eid = (
        token(),
        "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)),
        str(uuid.uuid4()),
    )
    owner = None
    email = data.get("owner_email")
    if isinstance(email, str) and len(email) <= 254:
        # Never disclose whether the requested owner or notification preference exists.
        row = await accounts.one(
            "SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.email=? AND m.workspace_id=? AND m.status='active' AND m.kind='internal'",
            email.strip().lower(),
            wid,
        )
        owner = row["id"] if row else "unavailable:" + digest(email.strip().lower())
    await accounts.run(
        "INSERT INTO agent_enrollments(id,device_hash,code_hash,workspace_id,name,projects,capabilities,owner_id,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        eid,
        digest(device),
        digest(code),
        wid,
        name,
        encode(projects),
        encode(caps),
        owner,
        timestamp(),
        timestamp() + 900,
    )
    if owner:
        try:
            await accounts.limit("device-notify:" + owner, 3, 3600)
            from .notifications import notify

            who = await accounts.member_identity(owner, wid)
            if who:
                await notify(
                    accounts,
                    who,
                    "enrollment:" + eid,
                    "Your agent wants to authenticate with Tasktrack. Review "
                    + accounts.origin
                    + "/agents and enter the code shown by your agent. Approve only if you initiated the request.",
                )
        except Error:
            pass  # Enrollment remains usable via the local URL; don't enumerate accounts.
    return {
        "device_code": device,
        "user_code": code[:4] + "-" + code[4:],
        "verification_uri": accounts.origin + "/agents",
        "expires_in": 900,
        "interval": 5,
    }


async def deliver(accounts, eid):
    row = await accounts.one(
        "UPDATE agent_notifications SET status='sending' WHERE enrollment_id=? AND status='queued' RETURNING *",
        eid,
    )
    if not row:
        return
    user = await accounts.one("SELECT email FROM users WHERE id=?", row["user_id"])
    body = (
        "Your agent wants to authenticate with Tasktrack. Open "
        + accounts.origin
        + "/agents and enter the code shown by your agent. Review the requested access before approving. If you did not initiate this, ignore it."
    )
    try:
        if accounts.local and getattr(accounts.env, "LOCAL_EMAIL", "") == "1":
            await accounts.run(
                "INSERT INTO local_mail(recipient,body,created_at) VALUES(?,?,?)",
                user["email"],
                body,
                timestamp(),
            )
        else:
            await accounts.env.EMAIL.send(
                {
                    "from": {
                        "email": getattr(
                            accounts.env, "MAIL_FROM", "hello@tasks.eastbayprojects.com"
                        ),
                        "name": "Tasktrack",
                    },
                    "to": user["email"],
                    "subject": "Your agent wants to authenticate",
                    "text": body,
                }
            )
        await accounts.run(
            "UPDATE agent_notifications SET status='sent',sent_at=? WHERE id=?",
            timestamp(),
            row["id"],
        )
    except Exception:
        # Ambiguous delivery must not be blindly retried.
        await accounts.run(
            "UPDATE agent_notifications SET status='uncertain' WHERE id=?", row["id"]
        )


async def lookup(accounts, identity, code):
    browser(identity)
    await accounts.limit("device-code:" + identity["user_id"], 5, 900)
    code = re.sub(r"[-\s]", "", code.upper()) if isinstance(code, str) else ""
    row = await accounts.one(
        "SELECT * FROM agent_enrollments WHERE code_hash=? AND workspace_id=? AND expires_at>? AND status=?",
        digest(code),
        identity["workspace_id"],
        timestamp(),
        "pending",
    )
    if not row:
        # Only route to a workspace this user can access, respecting any named
        # owner. A code alone never grants workspace membership.
        elsewhere = await accounts.one(
            "SELECT e.workspace_id,w.name FROM agent_enrollments e JOIN workspaces w ON w.id=e.workspace_id JOIN memberships m ON m.workspace_id=e.workspace_id AND m.user_id=? AND m.status='active' AND m.kind='internal' WHERE e.code_hash=? AND (e.owner_id IS NULL OR e.owner_id=?) AND e.expires_at>? AND e.status='pending'",
            identity["user_id"],
            digest(code),
            identity["user_id"],
            timestamp(),
        )
        if elsewhere and elsewhere["workspace_id"] != identity["workspace_id"]:
            raise Error(
                409,
                "enrollment_workspace",
                "This code belongs to "
                + elsewhere["name"]
                + ". Switch workspace to review it.",
                workspace_id=elsewhere["workspace_id"],
                workspace_name=elsewhere["name"],
            )
    if not row or (row["owner_id"] and row["owner_id"] != identity["user_id"]):
        raise Error(
            404,
            "enrollment_missing",
            "Code unavailable. Check the code, sign-in email and workspace, or request a new code if it expired.",
        )
    return row


async def decision(accounts, identity, row, approved):
    browser(identity)
    # Caller verifies fresh passkey binding before entering here.
    if row["workspace_id"] != identity["workspace_id"] or (
        row["owner_id"] and row["owner_id"] != identity["user_id"]
    ):
        raise Error(403, "not_allowed", "This request belongs to another owner.")
    gid = str(uuid.uuid4())
    changed = await accounts.one(
        "UPDATE agent_enrollments SET owner_id=?,status=?,grant_id=? WHERE id=? AND status='pending' AND expires_at>? AND (owner_id IS NULL OR owner_id=?) RETURNING id",
        identity["user_id"],
        "approved" if approved else "denied",
        gid if approved else None,
        row["id"],
        timestamp(),
        identity["user_id"],
    )
    if not changed:
        raise Error(
            409, "enrollment_changed", "Request expired or was already decided."
        )
    await accounts.run(
        "INSERT INTO agent_security_events(workspace_id,user_id,subject_id,operation,created_at) VALUES(?,?,?,?,?)",
        identity["workspace_id"],
        identity["user_id"],
        row["id"],
        "enrollment.approved" if approved else "enrollment.denied",
        timestamp(),
    )
    return {"status": "approved" if approved else "denied"}


def oauth_error(code):
    return {"error": code}


async def poll(accounts, secret):
    if not isinstance(secret, str) or len(secret) > 200:
        return oauth_error("invalid_grant")
    row = await accounts.one(
        "SELECT * FROM agent_enrollments WHERE device_hash=?", digest(secret)
    )
    if not row or row["expires_at"] <= timestamp():
        return oauth_error("expired_token")
    if row["status"] in ("denied", "cancelled", "consumed"):
        return oauth_error("access_denied")
    if timestamp() < row["last_poll"] + row["poll_interval"]:
        await accounts.run(
            "UPDATE agent_enrollments SET poll_interval=MIN(poll_interval+5,60),last_poll=? WHERE id=?",
            timestamp(),
            row["id"],
        )
        return oauth_error("slow_down")
    await accounts.run(
        "UPDATE agent_enrollments SET last_poll=? WHERE id=?", timestamp(), row["id"]
    )
    if row["status"] == "pending":
        return oauth_error("authorization_pending")
    access, refresh, tid = "tt_" + token(), token(), str(uuid.uuid4())
    # One D1 transaction: consume only when owner remains active; all inserts conditional.
    gid = row["grant_id"]
    await accounts.db.batch(
        [
            accounts.statement(
                "INSERT OR IGNORE INTO agent_grants(id,user_id,workspace_id,name,projects,capabilities,created_at,expires_at,refresh_hash) SELECT grant_id,owner_id,workspace_id,name,projects,capabilities,?,?,? FROM agent_enrollments e WHERE id=? AND status='approved' AND expires_at>? AND EXISTS(SELECT 1 FROM memberships m WHERE m.user_id=e.owner_id AND m.workspace_id=e.workspace_id AND m.status='active' AND m.kind='internal')",
                timestamp(),
                timestamp() + GRANT_AGE,
                digest(refresh),
                row["id"],
                timestamp(),
            ),
            accounts.statement(
                "INSERT INTO api_tokens(id,token_hash,user_id,workspace_id,name,actor,created_at,expires_at) SELECT ?,?,user_id,workspace_id,name,?, ?,? FROM agent_grants WHERE id=? AND refresh_hash=? AND revoked_at IS NULL",
                tid,
                digest(access),
                "agent:" + gid,
                timestamp(),
                timestamp() + ACCESS_AGE,
                gid,
                digest(refresh),
            ),
            accounts.statement(
                "INSERT INTO agent_token_scopes SELECT ?,? WHERE EXISTS(SELECT 1 FROM api_tokens WHERE id=?)",
                tid,
                gid,
                tid,
            ),
            accounts.statement(
                "UPDATE agent_enrollments SET status='consumed' WHERE id=? AND EXISTS(SELECT 1 FROM api_tokens WHERE id=?)",
                row["id"],
                tid,
            ),
        ]
    )
    if not await accounts.one("SELECT id FROM api_tokens WHERE id=?", tid):
        return oauth_error("access_denied")
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": ACCESS_AGE,
        "agent_id": gid,
    }


async def refresh(accounts, secret):
    if not isinstance(secret, str) or len(secret) > 200:
        return oauth_error("invalid_grant")
    hashed = digest(secret)
    used = await accounts.one(
        "SELECT grant_id FROM agent_refresh_history WHERE token_hash=?", hashed
    )
    if used:
        await accounts.run(
            "UPDATE agent_grants SET revoked_at=?,refresh_hash=NULL WHERE id=?",
            timestamp(),
            used["grant_id"],
        )
        return oauth_error("invalid_grant")
    row = await accounts.one(
        "SELECT * FROM agent_grants WHERE refresh_hash=? AND revoked_at IS NULL AND expires_at>?",
        hashed,
        timestamp(),
    )
    if not row:
        return oauth_error("invalid_grant")
    access, new_refresh, tid = "tt_" + token(), token(), str(uuid.uuid4())
    await accounts.db.batch(
        [
            accounts.statement(
                "UPDATE agent_grants SET refresh_hash=?,generation=generation+1 WHERE id=? AND refresh_hash=? AND revoked_at IS NULL AND expires_at>? AND EXISTS(SELECT 1 FROM memberships m WHERE m.user_id=agent_grants.user_id AND m.workspace_id=agent_grants.workspace_id AND m.status='active' AND m.kind='internal')",
                digest(new_refresh),
                row["id"],
                hashed,
                timestamp(),
            ),
            accounts.statement(
                "INSERT OR IGNORE INTO agent_refresh_history SELECT ?,id,? FROM agent_grants WHERE id=? AND refresh_hash=?",
                hashed,
                timestamp(),
                row["id"],
                digest(new_refresh),
            ),
            accounts.statement(
                "INSERT INTO api_tokens SELECT ?,?,user_id,workspace_id,name,?,?,? FROM agent_grants WHERE id=? AND refresh_hash=? AND revoked_at IS NULL",
                tid,
                digest(access),
                "agent:" + row["id"],
                timestamp(),
                min(timestamp() + ACCESS_AGE, row["expires_at"]),
                row["id"],
                digest(new_refresh),
            ),
            accounts.statement(
                "INSERT INTO agent_token_scopes SELECT ?,? WHERE EXISTS(SELECT 1 FROM api_tokens WHERE id=?)",
                tid,
                row["id"],
                tid,
            ),
        ]
    )
    if not await accounts.one("SELECT id FROM api_tokens WHERE id=?", tid):
        return oauth_error("invalid_grant")
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
        "expires_in": min(ACCESS_AGE, row["expires_at"] - timestamp()),
        "agent_id": row["id"],
    }


async def list_agents(accounts, identity):
    browser(identity)
    return {
        "items": await accounts.many(
            "SELECT id,name,projects,capabilities,created_at,expires_at,revoked_at,last_used_at FROM agent_grants WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC",
            identity["user_id"],
            identity["workspace_id"],
        )
    }


async def revoke(accounts, identity, gid):
    browser(identity)
    row = await accounts.one(
        "UPDATE agent_grants SET revoked_at=?,refresh_hash=NULL WHERE id=? AND user_id=? AND workspace_id=? RETURNING id",
        timestamp(),
        gid,
        identity["user_id"],
        identity["workspace_id"],
    )
    if not row:
        raise Error(404, "agent_missing", "Agent not found.")
    await accounts.run(
        "INSERT INTO agent_security_events(workspace_id,user_id,subject_id,operation,created_at) VALUES(?,?,?,?,?)",
        identity["workspace_id"],
        identity["user_id"],
        gid,
        "agent.revoked",
        timestamp(),
    )
    return {"revoked": True}
