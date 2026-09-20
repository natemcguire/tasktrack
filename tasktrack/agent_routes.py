"""Hosted human UI and device grant endpoints."""

import json
import re
from html import escape

from workers import Response

from . import agent_auth, notifications, passkeys
from .accounts import timestamp
from .db import Error
from .hosted_pages import page


def agent_page(identity):
    return page(
        "Agent access",
        f"""<main class="account-content"><a href="/account">Account settings</a>
<h1>Agent access</h1><p>Review requests, approve with your passkey, and revoke access whenever you need.</p>
<p>Workspace: <strong>{escape(identity["workspace_name"])}</strong></p><p>Workspace ID: <code>{escape(identity["workspace_id"])}</code></p>
<form id="agent-code-form"><label for="agent-code">Code shown by your agent</label><input id="agent-code" maxlength="12" autocomplete="off" required placeholder="ABCD-EFGH"><button class="button primary">Review request</button></form>
<p id="agent-message" role="status" aria-live="polite"></p><section id="agent-review" hidden><h2>Review access</h2><div id="agent-details"></div><p>Approve only if you initiated this connection. The agent name is supplied by the requesting device.</p><button class="button primary" id="agent-approve">Approve with passkey</button> <button class="button" id="agent-deny">Deny</button></section>
<section class="account-section"><h2>Notifications</h2><label><input type="checkbox" id="agent-email"> Email me when an agent requests access</label><p>Messages contain a review link. Credentials are never sent in messages.</p><div id="channel-list"></div><form id="channel-form"><label for="channel-kind">Phone notification channel</label><select id="channel-kind"><option value="imessage">iMessage (personal Mac bridge)</option><option value="sms">SMS</option></select><label for="channel-phone">Phone number</label><input id="channel-phone" type="tel" placeholder="+14155550123" required><button class="button">Send verification code</button></form><p>iMessage requires your signed-in Mac running the personal Tasktrack message bridge. SMS requires a configured sender.</p><form id="channel-verify" hidden><label for="channel-code">Phone verification code</label><input id="channel-code" inputmode="numeric" maxlength="6" required><button class="button">Verify phone with passkey</button></form></section>
<section><h2>Your agents</h2><div id="agent-list"></div></section><section><h2>Action approvals</h2><div id="approval-list"></div></section>
</main>""",
        '<meta name="tt-csrf" content="'
        + escape(identity["csrf"])
        + '"><meta name="tt-workspace" content="'
        + escape(identity["workspace_id"])
        + '"><script src="/agent-access.js" defer></script>',
    )


async def validate_scope(worker, identity, row):
    requested = json.loads(row["capabilities"])
    for pid in json.loads(row["projects"]):
        _, effective = await worker.task_call(
            identity, "/api/v1/me/capabilities", query={"project_id": str(pid)}
        )
        for cap in requested:
            if not effective["capabilities"].get(cap):
                raise Error(
                    403,
                    "scope_not_allowed",
                    "Your current permissions do not allow "
                    + cap
                    + " for project "
                    + str(pid)
                    + ".",
                )


async def route(worker, request, accounts, identity, path, method, body_data):
    if path.startswith("/api/v1/agent-deliveries/") and method == "POST":
        data = await body_data(request)
        if path.endswith("/claim"):
            return Response.json(await notifications.claim(accounts, identity))
        if path.endswith("/check"):
            return Response.json(
                await notifications.check_delivery(accounts, identity, data)
            )
        if path.endswith("/ack"):
            return Response.json(
                await notifications.acknowledge(accounts, identity, data)
            )
    if path == "/api/v1/agent-channels":
        agent_auth.browser(identity)
        if method == "GET":
            return Response.json(
                {
                    "items": await accounts.many(
                        "SELECT id,channel,recipient,verified_at,enabled FROM agent_channels WHERE user_id=?",
                        identity["user_id"],
                    )
                }
            )
        if method == "POST":
            accounts.csrf(request, identity)
            return Response.json(
                await notifications.begin_channel(
                    accounts, identity, await body_data(request)
                )
            )
    channel = re.fullmatch(
        r"/api/v1/agent-channels/([a-f0-9-]+)/(challenge|verify|remove)", path
    )
    if channel and method == "POST":
        accounts.csrf(request, identity)
        row = await notifications.channel(accounts, identity, channel[1])
        if channel[2] == "remove":
            await accounts.run(
                "DELETE FROM agent_channels WHERE id=? AND user_id=?",
                row["id"],
                identity["user_id"],
            )
            await accounts.run(
                "UPDATE security_deliveries SET status='cancelled' WHERE user_id=? AND channel=? AND recipient=? AND status='queued'",
                identity["user_id"],
                row["channel"],
                row["recipient"],
            )
            return Response.json({"removed": True})
        if channel[2] == "challenge":
            return Response.json(
                await passkeys.step_up_options(
                    accounts, identity, notifications.binding(row)
                )
            )
        data = await body_data(request)
        await passkeys.step_up_verify(
            accounts, identity, notifications.binding(row), data
        )
        return Response.json(
            await notifications.verify_channel(
                accounts, identity, row, data.get("code")
            )
        )
    approval = re.fullmatch(
        r"/api/v1/approval-requests/([a-f0-9-]+)/(challenge|decision)", path
    )
    if approval and method == "POST":
        agent_auth.browser(identity)
        accounts.csrf(request, identity)
        _, row = await worker.task_call(
            identity, "/api/v1/approval-requests/" + approval[1]
        )
        if row["state"] != "pending":
            raise Error(409, "approval_changed", "Request is no longer pending.")
        data = await body_data(request)
        binding = "approval:" + approval[1] + ":" + row["digest"]
        if approval[2] == "challenge":
            return Response.json(
                await passkeys.step_up_options(accounts, identity, binding)
            )
        if data.get("decision") == "approve":
            await passkeys.step_up_verify(accounts, identity, binding, data)
            identity = dict(identity, human_approval_digest=row["digest"])
        status, result = await worker.task_call(
            identity,
            path,
            "POST",
            {"decision": data.get("decision")},
            request_id=request.headers.get("Idempotency-Key"),
            via="ui",
        )
        return Response.json(result, status=status)
    if path == "/agents" and method == "GET":
        agent_auth.browser(identity)
        return Response(
            agent_page(identity), headers={"Content-Type": "text/html; charset=utf-8"}
        )
    if path == "/api/v1/agents" and method == "GET":
        return Response.json(await agent_auth.list_agents(accounts, identity))
    if path == "/api/v1/agent-notifications":
        agent_auth.browser(identity)
        if method == "GET":
            row = await accounts.one(
                "SELECT email_enabled FROM agent_notification_preferences WHERE user_id=?",
                identity["user_id"],
            )
            return Response.json({"email_enabled": bool(row and row["email_enabled"])})
        if method == "POST":
            accounts.csrf(request, identity)
            data = await body_data(request)
            if type(data.get("email_enabled")) is not bool:
                raise Error(
                    422,
                    "invalid_preference",
                    "Choose whether to receive email notifications.",
                )
            await accounts.run(
                "INSERT INTO agent_notification_preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET email_enabled=excluded.email_enabled",
                identity["user_id"],
                int(data["email_enabled"]),
            )
            return Response.json({"saved": True})
    match = re.fullmatch(r"/api/v1/agents/([a-f0-9-]+)/revoke", path)
    if match and method == "POST":
        accounts.csrf(request, identity)
        return Response.json(await agent_auth.revoke(accounts, identity, match[1]))
    if path == "/api/v1/agent-enrollments/lookup" and method == "POST":
        accounts.csrf(request, identity)
        data = await body_data(request)
        row = await agent_auth.lookup(accounts, identity, data.get("code"))
        await validate_scope(worker, identity, row)
        return Response.json(agent_auth.public_enrollment(row))
    match = re.fullmatch(
        r"/api/v1/agent-enrollments/([a-f0-9-]+)/(challenge|decision)", path
    )
    if match and method == "POST":
        agent_auth.browser(identity)
        accounts.csrf(request, identity)
        row = await accounts.one(
            "SELECT * FROM agent_enrollments WHERE id=? AND workspace_id=? AND expires_at>? AND status='pending'",
            match[1],
            identity["workspace_id"],
            timestamp(),
        )
        if not row or (row["owner_id"] and row["owner_id"] != identity["user_id"]):
            raise Error(404, "enrollment_missing", "Request unavailable.")
        data = await body_data(request)
        binding = agent_auth.enrollment_binding(row)
        if match[2] == "challenge":
            await validate_scope(worker, identity, row)
            return Response.json(
                await passkeys.step_up_options(accounts, identity, binding)
            )
        if data.get("decision") not in ("approve", "deny"):
            raise Error(422, "decision_required", "Choose approve or deny.")
        if data["decision"] == "approve":
            await validate_scope(worker, identity, row)
            await passkeys.step_up_verify(accounts, identity, binding, data)
        return Response.json(
            await agent_auth.decision(
                accounts, identity, row, data["decision"] == "approve"
            )
        )
    return None
