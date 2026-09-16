"""Hosted membership administration and trusted project-grant resolution."""

import hashlib
import json
import uuid

from .accounts import SESSION_AGE, timestamp
from .db import Error
from .policy import MEMBERSHIP_GRANTABLE, PROJECT_GRANTABLE


async def members(accounts, identity):
    accounts.owner(identity)
    rows = await accounts.many(
        "SELECT m.id,m.user_id,u.name,u.email,m.kind,m.access_role AS role,m.status,m.revision,m.customer_id,m.capabilities "
        "FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.created_at,m.id",
        identity["workspace_id"],
    )

    for row in rows:
        row["capabilities"] = json.loads(row["capabilities"])
    return rows


async def patch(accounts, identity, member_id, body):
    accounts.owner(identity)
    if set(body) - {
        "expected_version",
        "role",
        "status",
        "kind",
        "customer_id",
        "reason",
        "capabilities",
    }:
        raise Error(422, "unknown_field", "Use supported membership fields.")
    old = await accounts.one(
        "SELECT * FROM memberships WHERE workspace_id=? AND id=?",
        identity["workspace_id"],
        member_id,
    )
    if not old:
        raise Error(404, "not_found", "This member is unavailable.")
    if (
        type(body.get("expected_version")) is not int
        or body["expected_version"] != old["revision"]
    ):
        raise Error(
            409, "version_conflict", "This member changed. Reload and try again."
        )
    role = body.get("role", old["access_role"])
    status = body.get("status", old["status"])
    kind = body.get("kind", old["kind"])
    customer = body.get("customer_id", old["customer_id"])
    if (
        role not in ("admin", "billing", "regular")
        or status not in ("active", "suspended")
        or kind not in ("internal", "external")
    ):
        raise Error(422, "invalid_membership", "Choose a valid role and status.")
    if kind == "external" and (
        role != "regular"
        or not isinstance(customer, str)
        or not 1 <= len(customer.strip()) <= 100
    ):
        raise Error(
            422,
            "invalid_membership",
            "Customers need the regular role and a customer organization.",
        )
    caps = body.get("capabilities", json.loads(old["capabilities"]))
    if (
        not isinstance(caps, list)
        or any(not isinstance(x, str) or x not in MEMBERSHIP_GRANTABLE for x in caps)
        or (kind == "external" and caps)
    ):
        raise Error(
            422, "invalid_capabilities", "Choose supported internal permissions."
        )
    caps = sorted(set(caps))
    if kind == "internal":
        customer = None
    if kind != old["kind"]:
        reason = body.get("reason")
        if identity["bearer"] or not isinstance(reason, str) or not reason.strip():
            raise Error(
                403,
                "confirmation_required",
                "Sign in again and give a reason before changing internal or customer access.",
            )
        session = await accounts.one(
            "SELECT expires_at FROM sessions WHERE token_hash=?",
            identity["session_hash"],
        )
        if not session or session["expires_at"] - SESSION_AGE < timestamp() - 600:
            raise Error(
                403,
                "recent_sign_in_required",
                "Sign in again before changing internal or customer access.",
            )
    # The trigger is the final race-safe guard, including simultaneous demotions.
    if (
        old["access_role"] == "admin"
        and old["status"] == "active"
        and (role != "admin" or status != "active" or kind != "internal")
    ):
        row = await accounts.one(
            "SELECT count(*) AS n FROM memberships WHERE workspace_id=? AND kind='internal' AND access_role='admin' AND status='active'",
            identity["workspace_id"],
        )
        if row["n"] <= 1:
            raise Error(
                409,
                "last_admin",
                "Add another active admin before changing this member.",
            )
    before = {
        k: old[k]
        for k in (
            "kind",
            "access_role",
            "status",
            "customer_id",
            "revision",
            "capabilities",
        )
    }
    after = {
        "kind": kind,
        "access_role": role,
        "status": status,
        "customer_id": customer,
        "revision": old["revision"] + 1,
        "capabilities": caps,
    }
    try:
        rows = await accounts.db.batch(
            [
                accounts.statement(
                    "UPDATE memberships SET kind=?,access_role=?,role=?,status=?,customer_id=?,capabilities=?,revision=revision+1 WHERE workspace_id=? AND id=? AND revision=? RETURNING id,revision",
                    kind,
                    role,
                    "owner" if role == "admin" else "member",
                    status,
                    customer,
                    json.dumps(caps),
                    identity["workspace_id"],
                    member_id,
                    old["revision"],
                ),
                accounts.statement(
                    "INSERT INTO membership_audit(workspace_id,membership_id,changed_by,before_json,after_json,created_at) SELECT ?,?,?,?,?,? WHERE changes()>0",
                    identity["workspace_id"],
                    member_id,
                    identity["actor"],
                    json.dumps(before),
                    json.dumps(after),
                    timestamp(),
                ),
            ]
        )
    except Exception as exc:
        if "last_admin" in str(exc):
            raise Error(
                409,
                "last_admin",
                "Add another active admin before changing this member.",
            ) from None
        raise
    if not rows[0]["results"]:
        raise Error(
            409, "version_conflict", "This member changed. Reload and try again."
        )
    return {"id": member_id, **after, "role": role}


async def resolve_grants(accounts, identity, body):
    accounts.owner(identity)
    if set(body) - {"expected_version", "internal_access", "customer_id", "grants"}:
        raise Error(422, "unknown_field", "Use supported project access fields.")
    grants = body.get("grants")
    if not isinstance(grants, list) or len(grants) > 500:
        raise Error(422, "invalid_grants", "Supply the complete participant list.")
    records = {m["id"]: m for m in await members(accounts, identity)}
    resolved = []
    for g in grants:
        if not isinstance(g, dict) or set(g) - {
            "membership_id",
            "access",
            "capabilities",
            "can_view_invoices",
            "can_approve_scope",
        }:
            raise Error(422, "invalid_grants", "Use supported participant fields.")
        mid = g.get("membership_id")
        m = records.get(mid) if isinstance(mid, str) else None
        if not m or m["status"] != "active":
            raise Error(
                422, "invalid_member", "Choose an active member of this workspace."
            )
        access = g.get("access", "participant")
        caps = g.get("capabilities", [])
        if (
            access not in ("participant", "manager")
            or not isinstance(caps, list)
            or any(not isinstance(x, str) or x not in PROJECT_GRANTABLE for x in caps)
        ):
            raise Error(422, "invalid_grants", "Choose supported project permissions.")
        billing = g.get("can_view_invoices", False)
        approve = g.get("can_approve_scope", False)
        if type(billing) is not bool or type(approve) is not bool:
            raise Error(
                422, "invalid_grants", "Permission flags must be true or false."
            )
        if m["kind"] == "external" and (access != "participant" or caps):
            raise Error(
                422,
                "invalid_grants",
                "Customer access cannot include internal permissions.",
            )
        resolved.append(
            {
                "membership_id": mid,
                "access": access,
                "capabilities": sorted(set(caps)),
                "customer_id": m["customer_id"] if m["kind"] == "external" else None,
                "can_view_invoices": billing,
                "can_approve_scope": approve,
            }
        )
    return {**body, "grants": resolved}


async def create_tenant(accounts, identity, body, request_key):
    if identity["bearer"]:
        raise Error(
            403,
            "browser_session_required",
            "Create a workspace from your signed-in browser.",
        )
    if set(body) - {"name", "timezone"}:
        raise Error(422, "unknown_field", "Use a name and timezone.")
    name = body.get("name")
    zone = body.get("timezone", "UTC")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise Error(422, "invalid_name", "Use a workspace name of 1–80 characters.")
    try:
        if not isinstance(zone, str) or len(zone) > 100:
            raise ValueError()
        from js import JSON, Intl

        Intl.DateTimeFormat.new("en-US", JSON.parse(json.dumps({"timeZone": zone})))
    except Exception:
        raise Error(422, "invalid_timezone", "Choose a recognized timezone.") from None
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise Error(
            400,
            "missing_idempotency_key",
            "Supply an Idempotency-Key for workspace creation.",
        )
    fingerprint = hashlib.sha256(json.dumps([name.strip(), zone]).encode()).hexdigest()
    await accounts.limit("tenant-create:" + identity["user_id"], 5, 3600)
    wid = str(uuid.uuid4())
    await accounts.db.batch(
        [
            accounts.statement(
                "INSERT OR IGNORE INTO tenant_creation_requests VALUES(?,?,?,?)",
                identity["user_id"],
                request_key,
                wid,
                fingerprint,
            ),
            accounts.statement(
                "INSERT INTO workspaces(id,name,created_by,created_at,timezone) SELECT workspace_id,?,?,?,? FROM tenant_creation_requests WHERE user_id=? AND request_key=? AND workspace_id=?",
                name.strip(),
                identity["user_id"],
                timestamp(),
                zone,
                identity["user_id"],
                request_key,
                wid,
            ),
            accounts.statement(
                "INSERT INTO memberships(workspace_id,user_id,role,created_at) SELECT id,?,'owner',? FROM workspaces WHERE id=?",
                identity["user_id"],
                timestamp(),
                wid,
            ),
        ]
    )
    receipt = await accounts.one(
        "SELECT workspace_id,fingerprint FROM tenant_creation_requests WHERE user_id=? AND request_key=?",
        identity["user_id"],
        request_key,
    )
    if receipt["fingerprint"] != fingerprint:
        raise Error(
            409,
            "idempotency_conflict",
            "This request key was already used for another workspace request.",
        )
    wid = receipt["workspace_id"]
    await accounts.switch(identity, wid)
    return {"id": wid, "name": name.strip(), "timezone": zone}
