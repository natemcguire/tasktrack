import re
import unittest

from tasktrack import notifications
from tasktrack.db import Error


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_agent_auth import AgentTests

        AgentTests.setUp(self)

    async def test_phone_code_and_bridge_lease_are_owner_bound(self):
        channel = await notifications.begin_channel(
            self.accounts,
            self.identity,
            {"channel": "imessage", "recipient": "+14155550123"},
        )
        bridge = self.identity | {
            "bearer": True,
            "token_capabilities": ["notifications.deliver"],
        }
        delivery = (await notifications.claim(self.accounts, bridge))["delivery"]
        self.assertIsNotNone(delivery)
        self.assertIsNone(
            (await notifications.claim(self.accounts, bridge))["delivery"]
        )
        with self.assertRaises(Error):
            await notifications.acknowledge(
                self.accounts,
                bridge,
                {"id": delivery["id"], "lease_token": "wrong", "status": "sent"},
            )
        await notifications.acknowledge(
            self.accounts,
            bridge,
            {
                "id": delivery["id"],
                "lease_token": delivery["lease_token"],
                "status": "sent",
            },
        )
        with self.assertRaises(Error):
            await notifications.acknowledge(
                self.accounts,
                bridge,
                {
                    "id": delivery["id"],
                    "lease_token": delivery["lease_token"],
                    "status": "sent",
                },
            )
        row = await notifications.channel(self.accounts, self.identity, channel["id"])
        with self.assertRaises(Error):
            await notifications.verify_channel(
                self.accounts, self.identity, row, "0000000"
            )
        code = re.search(r"code is (\d{6})", delivery["body"])[1]
        self.assertTrue(
            (
                await notifications.verify_channel(
                    self.accounts, self.identity, row, code
                )
            )["verified"]
        )

    async def test_bridge_requires_dedicated_scope_and_unknown_send_not_retried(self):
        await notifications.begin_channel(
            self.accounts,
            self.identity,
            {"channel": "imessage", "recipient": "+14155550124"},
        )
        with self.assertRaises(Error):
            await notifications.claim(
                self.accounts,
                self.identity | {"bearer": True, "token_capabilities": ["work.read"]},
            )
        bridge = self.identity | {
            "bearer": True,
            "token_capabilities": ["notifications.deliver"],
        }
        delivery = (await notifications.claim(self.accounts, bridge))["delivery"]
        self.c.execute("UPDATE security_deliveries SET lease_expires=0")
        self.assertIsNone(
            (await notifications.claim(self.accounts, bridge))["delivery"]
        )
        self.assertEqual(
            self.c.execute(
                "SELECT status FROM security_deliveries WHERE id=?", (delivery["id"],)
            ).fetchone()[0],
            "uncertain",
        )

    async def test_removed_channel_invalidates_claim_before_send(self):
        await notifications.begin_channel(
            self.accounts,
            self.identity,
            {"channel": "imessage", "recipient": "+14155550125"},
        )
        bridge = self.identity | {
            "bearer": True,
            "token_capabilities": ["notifications.deliver"],
        }
        delivery = (await notifications.claim(self.accounts, bridge))["delivery"]
        lease = {"id": delivery["id"], "lease_token": delivery["lease_token"]}
        self.assertTrue(
            (await notifications.check_delivery(self.accounts, bridge, lease))[
                "authorized"
            ]
        )
        self.c.execute("DELETE FROM agent_channels")
        with self.assertRaises(Error):
            await notifications.check_delivery(self.accounts, bridge, lease)
