"""Workspace-owned read-only application identities and client credentials."""

import json
import secrets
import uuid

from .accounts import digest, timestamp, token
from .agent_auth import browser, scope
from .db import Error, encode

READ_CAPABILITIES = frozenset({"project.read", "work.read", "notes.read"})


def administrator(accounts, identity):
    browser(identity)
    accounts.owner(identity)


def definition(data):
    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise Error(422, "app_name", "Name the internal app (1–80 characters).")
    projects, capabilities = scope(data)
    if not set(capabilities) <= READ_CAPABILITIES or not {
        "project.read",
        "work.read",
    } <= set(capabilities):
        raise Error(
            422,
            "app_scope",
            "Apps require project.read and work.read; notes.read is optional. Other capabilities are not supported.",
        )
    return {"name": name.strip(), "project_ids": projects, "capabilities": capabilities}


def binding(operation, data):
    return "service-app:" + operation + ":" + digest(encode(data))


def public(row):
    return {
        k: row[k]
        for k in (
            "id",
            "name",
            "revision",
            "created_at",
            "rotated_at",
            "revoked_at",
            "last_used_at",
        )
    } | {
        "project_ids": json.loads(row["projects"]),
        "capabilities": json.loads(row["capabilities"]),
    }


async def get(accounts, identity, app_id):
    administrator(accounts, identity)
    row = await accounts.one(
        "SELECT * FROM service_apps WHERE id=? AND workspace_id=?",
        app_id,
        identity["workspace_id"],
    )
    if not row:
        raise Error(404, "app_missing", "Application unavailable.")
    return row


async def create(accounts, identity, data):
    administrator(accounts, identity)
    data = definition(data)
    aid, secret = str(uuid.uuid4()), "tts_" + token()
    await accounts.db.batch(
        [
            accounts.statement(
                "INSERT INTO service_apps(id,workspace_id,name,projects,capabilities,secret_hash,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                aid,
                identity["workspace_id"],
                data["name"],
                encode(data["project_ids"]),
                encode(data["capabilities"]),
                digest(secret),
                identity["user_id"],
                timestamp(),
            ),
            accounts.statement(
                "INSERT INTO service_app_events(app_id,actor,operation,created_at) VALUES(?,?,?,?)",
                aid,
                identity["user_id"],
                "create",
                timestamp(),
            ),
        ]
    )
    return {
        "app": public(await get(accounts, identity, aid)),
        "client_id": aid,
        "client_secret": secret,
        "token_endpoint": accounts.origin + "/oauth/token",
    }


async def change(accounts, identity, row, operation):
    administrator(accounts, identity)
    if operation not in ("rotate", "revoke"):
        raise Error(422, "app_operation", "Choose rotate or revoke.")
    secret = "tts_" + token() if operation == "rotate" else ""
    changed = await accounts.one(
        "UPDATE service_apps SET secret_hash=?,revision=revision+1,rotated_at=?,revoked_at=? WHERE id=? AND workspace_id=? AND revision=? AND revoked_at IS NULL RETURNING id",
        digest(secret) if secret else "",
        timestamp(),
        timestamp() if operation == "revoke" else None,
        row["id"],
        identity["workspace_id"],
        row["revision"],
    )
    if not changed:
        raise Error(
            409,
            "app_changed",
            "Application changed or was revoked. Reload before trying again.",
        )
    await accounts.run(
        "INSERT INTO service_app_events(app_id,actor,operation,created_at) VALUES(?,?,?,?)",
        row["id"],
        identity["user_id"],
        operation,
        timestamp(),
    )
    result = {"app": public(await get(accounts, identity, row["id"]))}
    if secret:
        result.update(
            client_id=row["id"],
            client_secret=secret,
            token_endpoint=accounts.origin + "/oauth/token",
        )
    return result


async def exchange(accounts, data, ip):
    await accounts.limit("app-token-ip:" + ip, 120, 60)
    aid, secret = data.get("client_id"), data.get("client_secret")
    if (
        not isinstance(aid, str)
        or len(aid) > 80
        or not isinstance(secret, str)
        or len(secret) > 200
    ):
        return {"error": "invalid_client"}
    row = await accounts.one(
        "SELECT * FROM service_apps WHERE id=? AND revoked_at IS NULL", aid
    )
    if not row or not secrets.compare_digest(row["secret_hash"], digest(secret)):
        return {"error": "invalid_client"}
    if data.get("scope"):
        return {"error": "invalid_scope"}
    access = "tta_" + token()
    # Conditional insert prevents a rotation/revocation racing credential exchange.
    changed = await accounts.one(
        "INSERT INTO service_app_tokens(token_hash,app_id,revision,expires_at) SELECT ?,id,revision,? FROM service_apps WHERE id=? AND revision=? AND secret_hash=? AND revoked_at IS NULL RETURNING token_hash",
        digest(access),
        timestamp() + 900,
        aid,
        row["revision"],
        row["secret_hash"],
    )
    if not changed:
        return {"error": "invalid_client"}
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": " ".join(json.loads(row["capabilities"])),
    }


async def authenticate(accounts, secret):
    row = await accounts.one(
        "SELECT a.*,w.name AS workspace_name FROM service_app_tokens t JOIN service_apps a ON a.id=t.app_id JOIN workspaces w ON w.id=a.workspace_id WHERE t.token_hash=? AND t.expires_at>? AND t.revision=a.revision AND a.revoked_at IS NULL",
        digest(secret),
        timestamp(),
    )
    if not row:
        return None
    await accounts.run(
        "UPDATE service_apps SET last_used_at=? WHERE id=?", timestamp(), row["id"]
    )
    principal = "app:" + row["id"]
    return {
        "app_id": row["id"],
        "user_id": principal,
        "membership_id": principal,
        "membership_revision": row["revision"],
        "workspace_id": row["workspace_id"],
        "workspace_name": row["workspace_name"],
        "name": row["name"],
        "email": "",
        "actor": principal,
        "role": "member",
        "access_role": "regular",
        "kind": "internal",
        "status": "active",
        "customer_id": None,
        "membership_capabilities": "[]",
        "bearer": True,
        "session_hash": digest(secret),
        "token_id": digest(secret),
        "token_project_ids": [str(x) for x in json.loads(row["projects"])],
        "token_capabilities": json.loads(row["capabilities"]),
    }
