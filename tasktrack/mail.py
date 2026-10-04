"""Outbound email: the send_email binding, or the East Bay Projects mail relay.

tasks.eastbayprojects.com lives in the East Bay Projects Cloudflare account and
Tasktrack does not, so its branded mail goes through ebp-mail-relay when
MAIL_RELAY_URL is set.
"""

import json

from .db import encode


async def send(env, message):
    relay = getattr(env, "MAIL_RELAY_URL", "")
    if not relay:
        await env.EMAIL.send(message)
        return
    from .integrations import module

    result = json.loads(
        await module().request(
            encode(
                {
                    "url": relay,
                    "method": "POST",
                    "headers": {
                        "Authorization": "Bearer " + env.MAIL_RELAY_SECRET,
                        "Content-Type": "application/json",
                    },
                    "body": encode(message),
                }
            )
        )
    )
    if result["status"] != 202:
        raise ValueError("Mail relay did not accept the message")
