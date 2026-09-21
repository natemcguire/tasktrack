"""Verified security notification channels; delivery never grants authority."""

import base64
import json
import re
import secrets
import uuid
from urllib.parse import urlencode

from .accounts import digest, timestamp, token
from .agent_auth import browser
from .db import Error, encode


async def enqueue(accounts, user, wid, event, channel, recipient, body):
    did = str(uuid.uuid4())
    await accounts.run(
        "INSERT OR IGNORE INTO security_deliveries(id,user_id,workspace_id,event_key,channel,recipient,body,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
        did,
        user,
        wid,
        event,
        channel,
        recipient,
        body,
        timestamp(),
        timestamp() + 900,
    )
    if channel in ("email", "sms"):
        await dispatch(accounts, did)


async def dispatch(accounts, did):
    row = await accounts.one(
        "UPDATE security_deliveries SET status='sending' WHERE id=? AND status='queued' AND expires_at>? RETURNING *",
        did,
        timestamp(),
    )
    if not row:
        return
    try:
        if accounts.local and getattr(accounts.env, "LOCAL_EMAIL", "") == "1":
            await accounts.run(
                "INSERT INTO local_mail(recipient,body,created_at) VALUES(?,?,?)",
                row["recipient"],
                row["body"],
                timestamp(),
            )
        elif row["channel"] == "email":
            await accounts.env.EMAIL.send(
                {
                    "from": {
                        "email": getattr(
                            accounts.env, "MAIL_FROM", "hello@tasks.eastbayprojects.com"
                        ),
                        "name": "Tasktrack",
                    },
                    "to": row["recipient"],
                    "subject": "Your agent is waiting for your decision",
                    "text": row["body"],
                }
            )
        else:
            from .integrations import module

            sid = getattr(accounts.env, "TWILIO_ACCOUNT_SID", "")
            secret = getattr(accounts.env, "TWILIO_AUTH_TOKEN", "")
            sender = getattr(accounts.env, "TWILIO_FROM", "")
            if not re.fullmatch("AC[a-fA-F0-9]{32}", sid) or not secret or not sender:
                raise ValueError("SMS not configured")
            result = json.loads(
                await module().request(
                    encode(
                        {
                            "url": "https://api.twilio.com/2010-04-01/Accounts/"
                            + sid
                            + "/Messages.json",
                            "method": "POST",
                            "headers": {
                                "Authorization": "Basic "
                                + base64.b64encode(
                                    (sid + ":" + secret).encode()
                                ).decode(),
                                "Content-Type": "application/x-www-form-urlencoded",
                            },
                            "body": urlencode(
                                {
                                    "To": row["recipient"],
                                    "From": sender,
                                    "Body": row["body"],
                                }
                            ),
                        }
                    )
                )
            )
            if result["status"] != 201:
                raise ValueError("Delivery not accepted")
        await accounts.run(
            "UPDATE security_deliveries SET status='sent',sent_at=? WHERE id=?",
            timestamp(),
            did,
        )
    except Exception:
        await accounts.run(
            "UPDATE security_deliveries SET status='uncertain' WHERE id=?", did
        )


async def notify(accounts, identity, event, body):
    await accounts.limit("security-notify:" + identity["user_id"], 10, 3600)
    preference = await accounts.one(
        "SELECT email_enabled FROM agent_notification_preferences WHERE user_id=?",
        identity["user_id"],
    )
    if preference and preference["email_enabled"]:
        await enqueue(
            accounts,
            identity["user_id"],
            identity["workspace_id"],
            event,
            "email",
            identity["email"],
            body,
        )
    for row in await accounts.many(
        "SELECT channel,recipient FROM agent_channels WHERE user_id=? AND verified_at IS NOT NULL AND enabled=1",
        identity["user_id"],
    ):
        await enqueue(
            accounts,
            identity["user_id"],
            identity["workspace_id"],
            event,
            row["channel"],
            row["recipient"],
            body,
        )


async def begin_channel(accounts, identity, data):
    browser(identity)
    channel, recipient = data.get("channel"), data.get("recipient")
    if (
        channel not in ("sms", "imessage")
        or not isinstance(recipient, str)
        or not re.fullmatch(r"\+[1-9][0-9]{7,14}", recipient)
    ):
        raise Error(
            422,
            "phone_required",
            "Use an international phone number, such as +14155550123.",
        )
    if (
        channel == "sms"
        and not accounts.local
        and not getattr(accounts.env, "TWILIO_ACCOUNT_SID", "")
    ):
        raise Error(503, "sms_unavailable", "SMS delivery is not configured.")
    await accounts.limit("phone-user:" + identity["user_id"], 3, 3600)
    await accounts.limit("phone-recipient:" + recipient, 3, 3600)
    cid = str(uuid.uuid4())
    code = f"{secrets.randbelow(1000000):06d}"
    existing = await accounts.one(
        "SELECT id FROM agent_channels WHERE user_id=? AND channel=?",
        identity["user_id"],
        channel,
    )
    if existing:
        # Keep existing verified settings until an explicit removal; changing a route must be deliberate.
        raise Error(
            409,
            "channel_exists",
            "Remove the existing channel before registering another number.",
        )
    await accounts.run(
        "INSERT INTO agent_channels(id,user_id,channel,recipient,code_hash,expires_at) VALUES(?,?,?,?,?,?)",
        cid,
        identity["user_id"],
        channel,
        recipient,
        digest(cid + ":" + code),
        timestamp() + 900,
    )
    await enqueue(
        accounts,
        identity["user_id"],
        identity["workspace_id"],
        "verify:" + cid,
        channel,
        recipient,
        "Your Tasktrack phone verification code is "
        + code
        + ". Enter it on the page where you requested it. This does not approve agent access.",
    )
    return {
        "id": cid,
        "channel": channel,
        "expires_in": 900,
        "bridge_required": channel == "imessage",
    }


async def channel(accounts, identity, cid):
    browser(identity)
    row = await accounts.one(
        "SELECT * FROM agent_channels WHERE id=? AND user_id=?",
        cid,
        identity["user_id"],
    )
    if not row:
        raise Error(404, "channel_missing", "Channel unavailable.")
    return row


def binding(row):
    return "channel:" + row["id"] + ":" + digest(row["recipient"])


async def verify_channel(accounts, identity, row, code):
    browser(identity)
    changed = await accounts.one(
        "UPDATE agent_channels SET attempts=attempts+1 WHERE id=? AND user_id=? AND attempts<5 AND expires_at>? AND verified_at IS NULL RETURNING code_hash",
        row["id"],
        identity["user_id"],
        timestamp(),
    )
    if (
        not changed
        or not isinstance(code, str)
        or not secrets.compare_digest(
            changed["code_hash"], digest(row["id"] + ":" + code)
        )
    ):
        raise Error(
            400, "invalid_phone_code", "Verification code is invalid or expired."
        )
    await accounts.run(
        "UPDATE agent_channels SET verified_at=?,code_hash=NULL,enabled=1 WHERE id=? AND user_id=?",
        timestamp(),
        row["id"],
        identity["user_id"],
    )
    return {"verified": True}


def bridge_authority(accounts, identity):
    accounts.owner(identity)
    if not identity.get("bearer") or "notifications.deliver" not in identity.get(
        "token_capabilities", ["notifications.deliver"]
    ):
        raise Error(
            403,
            "bridge_token",
            "Use an owner-issued agent credential with notifications.deliver for the personal message bridge.",
        )


async def claim(accounts, identity):
    bridge_authority(accounts, identity)
    await accounts.limit("bridge-poll:" + identity["user_id"], 30, 60)
    await accounts.run(
        "UPDATE security_deliveries SET status='uncertain' WHERE user_id=? AND channel='imessage' AND status='claimed' AND lease_expires<?",
        identity["user_id"],
        timestamp(),
    )
    secret = token()
    row = await accounts.one(
        "UPDATE security_deliveries SET status='claimed',lease_hash=?,lease_expires=? WHERE id=(SELECT id FROM security_deliveries WHERE user_id=? AND workspace_id=? AND channel='imessage' AND status='queued' AND expires_at>? ORDER BY created_at LIMIT 1) AND status='queued' RETURNING id,recipient,body,expires_at",
        digest(secret),
        timestamp() + 120,
        identity["user_id"],
        identity["workspace_id"],
        timestamp(),
    )
    return {"delivery": dict(row, lease_token=secret) if row else None}


async def acknowledge(accounts, identity, data):
    bridge_authority(accounts, identity)
    secret = data.get("lease_token")
    status = data.get("status")
    if not isinstance(secret, str) or status not in ("sent", "uncertain"):
        raise Error(
            422,
            "delivery_receipt",
            "Provide the delivery lease and actual send result.",
        )
    row = await accounts.one(
        "UPDATE security_deliveries SET status=?,sent_at=?,lease_hash=NULL WHERE id=? AND user_id=? AND workspace_id=? AND lease_hash=? AND lease_expires>? AND status='claimed' RETURNING id",
        status,
        timestamp() if status == "sent" else None,
        data.get("id", ""),
        identity["user_id"],
        identity["workspace_id"],
        digest(secret),
        timestamp(),
    )
    if not row:
        raise Error(
            409,
            "delivery_lease",
            "Delivery lease expired or was already acknowledged. Do not resend.",
        )
    return {"recorded": True}


async def check_delivery(accounts, identity, data):
    bridge_authority(accounts, identity)
    secret = data.get("lease_token")
    if not isinstance(secret, str):
        raise Error(422, "delivery_lease", "Provide the delivery lease.")
    row = await accounts.one(
        "SELECT d.id FROM security_deliveries d WHERE d.id=? AND d.user_id=? AND d.workspace_id=? AND d.lease_hash=? AND d.status='claimed' AND d.lease_expires>? AND d.expires_at>? AND EXISTS(SELECT 1 FROM agent_channels c WHERE c.user_id=d.user_id AND c.channel=d.channel AND c.recipient=d.recipient AND ((c.verified_at IS NOT NULL AND c.enabled=1) OR (d.event_key='verify:'||c.id AND c.expires_at>?)))",
        data.get("id", ""),
        identity["user_id"],
        identity["workspace_id"],
        digest(secret),
        timestamp(),
        timestamp(),
        timestamp(),
    )
    if not row:
        raise Error(
            409,
            "delivery_cancelled",
            "Delivery expired or its channel was removed. Do not send.",
        )
    return {"authorized": True}
