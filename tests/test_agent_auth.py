import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from tasktrack import agent_auth as auth
from tasktrack.accounts import Accounts
from tasktrack.db import Error


class TestAccounts(Accounts):
    def __init__(self, c):
        self.c = c
        self.db = self
        self.origin = "http://localhost:8787"
        self.local = True
        self.env = SimpleNamespace(LOCAL_EMAIL="1", CUSTOMER_ACCESS_V2="1")
        self.cookie_name = "tt_session"

    async def one(self, sql, *args):
        row = self.c.execute(sql, args).fetchone()
        return dict(row) if row else None

    async def many(self, sql, *args):
        return [dict(r) for r in self.c.execute(sql, args)]

    async def run(self, sql, *args):
        self.c.execute(sql, args)

    def statement(self, sql, *args):
        return sql, args

    async def batch(self, statements):
        with self.c:
            for sql, args in statements:
                self.c.execute(sql, args)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.c.close)
        for p in sorted(Path("cloudflare/migrations").glob("*.sql")):
            self.c.executescript(p.read_text())
        self.c.execute("INSERT INTO users VALUES('u','u@example.com','User',1)")
        self.c.execute(
            "INSERT INTO workspaces(id,name,created_by,created_at) VALUES('w','Workspace','u',1)"
        )
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u','owner',1)"
        )
        self.accounts = TestAccounts(self.c)
        self.identity = {
            "workspace_id": "w",
            "user_id": "u",
            "actor": "u@example.com",
            "bearer": False,
            "kind": "internal",
            "access_role": "admin",
        }

    async def enroll(self):
        result = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "name": "Codex",
                "project_ids": [1],
                "capabilities": ["project.read", "work.read", "work.edit"],
            },
            "ip",
        )
        row = await auth.lookup(self.accounts, self.identity, result["user_code"])
        await auth.decision(self.accounts, self.identity, row, True)
        return result, row

    async def test_device_secret_only_redeems_once_and_scope_is_persisted(self):
        started, row = await self.enroll()
        self.assertEqual(
            (await auth.poll(self.accounts, started["user_code"]))["error"],
            "expired_token",
        )
        result = await auth.poll(self.accounts, started["device_code"])
        self.assertIn("access_token", result)
        replay = await auth.poll(self.accounts, started["device_code"])
        self.assertEqual(replay["error"], "access_denied")
        who = await self.accounts.authenticate(
            SimpleNamespace(
                headers={"Authorization": "Bearer " + result["access_token"]}
            )
        )
        self.assertEqual(who["token_project_ids"], ["1"])
        self.assertEqual(who["agent_id"], result["agent_id"])
        self.assertNotIn("tenant.delete", who["token_capabilities"])

    async def test_refresh_replay_revokes_all_access_and_revoke_is_immediate(self):
        started, _ = await self.enroll()
        result = await auth.poll(self.accounts, started["device_code"])
        rotated = await auth.refresh(self.accounts, result["refresh_token"])
        self.assertIn("access_token", rotated)
        self.assertEqual(
            (await auth.refresh(self.accounts, result["refresh_token"]))["error"],
            "invalid_grant",
        )
        for token in [result["access_token"], rotated["access_token"]]:
            self.assertIsNone(
                await self.accounts.authenticate(
                    SimpleNamespace(headers={"Authorization": "Bearer " + token})
                )
            )

    async def test_wrong_workspace_bearer_and_owner_cannot_review(self):
        start = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "name": "A",
                "project_ids": [1],
                "capabilities": ["work.read"],
            },
            "ip",
        )
        for changes in [{"workspace_id": "other"}, {"bearer": True}]:
            with self.assertRaises(Error):
                await auth.lookup(
                    self.accounts, self.identity | changes, start["user_code"]
                )
        with self.assertRaises(Error):
            auth.scope({"project_ids": [True], "capabilities": ["tenant.delete"]})

    async def test_denial_expiry_pending_and_slow_poll(self):
        start = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "name": "A",
                "project_ids": [1],
                "capabilities": ["work.read"],
            },
            "ip",
        )
        self.assertEqual(
            (await auth.poll(self.accounts, start["device_code"]))["error"],
            "authorization_pending",
        )
        self.assertEqual(
            (await auth.poll(self.accounts, start["device_code"]))["error"], "slow_down"
        )
        row = await auth.lookup(self.accounts, self.identity, start["user_code"])
        await auth.decision(self.accounts, self.identity, row, False)
        self.assertEqual(
            (await auth.poll(self.accounts, start["device_code"]))["error"],
            "access_denied",
        )
        self.c.execute("UPDATE agent_enrollments SET expires_at=0")
        self.assertEqual(
            (await auth.poll(self.accounts, start["device_code"]))["error"],
            "expired_token",
        )

    async def test_membership_change_revokes_refresh_family(self):
        start, _ = await self.enroll()
        result = await auth.poll(self.accounts, start["device_code"])
        self.c.execute(
            "UPDATE memberships SET capabilities='[\"project.create\"]' WHERE user_id='u'"
        )
        self.assertEqual(
            (await auth.refresh(self.accounts, result["refresh_token"]))["error"],
            "invalid_grant",
        )

    async def test_notification_opt_in_does_not_leak_owner_or_secret(self):
        self.c.execute("INSERT INTO agent_notification_preferences VALUES('u',1)")
        start = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "owner_email": "u@example.com",
                "name": "A",
                "project_ids": [1],
                "capabilities": ["work.read"],
            },
            "ip",
        )
        row = self.c.execute("SELECT body FROM local_mail").fetchone()
        self.assertNotIn(start["device_code"], row["body"])
        self.assertNotIn(start["user_code"], row["body"])
        self.assertIn("/agents", row["body"])

    async def test_explicit_unknown_owner_cannot_be_claimed_by_another_member(self):
        result = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "name": "Bound agent",
                "project_ids": [1],
                "capabilities": ["work.read"],
                "owner_email": "other@example.com",
            },
            "owner-bound-ip",
        )
        with self.assertRaises(Error):
            await auth.lookup(self.accounts, self.identity, result["user_code"])

    async def test_owner_can_find_correct_workspace_without_disclosing_to_others(self):
        result = await auth.start(
            self.accounts,
            {
                "workspace_id": "w",
                "name": "Bridge",
                "project_ids": [1],
                "capabilities": ["notifications.deliver"],
                "owner_email": "u@example.com",
            },
            "workspace-hint",
        )
        with self.assertRaises(Error) as hint:
            await auth.lookup(
                self.accounts,
                self.identity | {"workspace_id": "other"},
                result["user_code"],
            )
        self.assertEqual(
            hint.exception.payload["error"]["code"], "enrollment_workspace"
        )
        self.assertEqual(hint.exception.payload["error"]["workspace_id"], "w")
        with self.assertRaises(Error) as hidden:
            await auth.lookup(
                self.accounts,
                self.identity | {"user_id": "outsider", "workspace_id": "other"},
                result["user_code"],
            )
        self.assertEqual(
            hidden.exception.payload["error"]["code"], "enrollment_missing"
        )
        self.assertNotIn("workspace_id", hidden.exception.payload["error"])
        self.c.execute("INSERT INTO users VALUES('u2','second@example.com','Second',1)")
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u2','owner',1)"
        )
        self.c.execute("UPDATE memberships SET status='suspended' WHERE user_id='u'")
        with self.assertRaises(Error) as removed:
            await auth.lookup(
                self.accounts,
                self.identity | {"workspace_id": "other"},
                result["user_code"],
            )
        self.assertEqual(
            removed.exception.payload["error"]["code"], "enrollment_missing"
        )

    async def test_connection_status_requires_owner_and_tracks_credential_collection(
        self,
    ):
        started, row = await self.enroll()
        status = await auth.enrollment_status(self.accounts, self.identity, row["id"])
        self.assertEqual(status["status"], "approved")
        self.assertNotIn("device_hash", status)
        for changes in (
            {"workspace_id": "other"},
            {"user_id": "other"},
            {"bearer": True},
        ):
            with self.assertRaises(Error):
                await auth.enrollment_status(
                    self.accounts, self.identity | changes, row["id"]
                )
        grant = await auth.poll(self.accounts, started["device_code"])
        self.assertIn("access_token", grant)
        self.assertEqual(
            (await auth.enrollment_status(self.accounts, self.identity, row["id"]))[
                "status"
            ],
            "connected",
        )
        await auth.revoke(self.accounts, self.identity, grant["agent_id"])
        self.assertEqual(
            (await auth.enrollment_status(self.accounts, self.identity, row["id"]))[
                "status"
            ],
            "inactive",
        )

    async def test_expired_approval_is_not_reported_as_connected(self):
        _, row = await self.enroll()
        self.c.execute(
            "UPDATE agent_enrollments SET expires_at=0 WHERE id=?", (row["id"],)
        )
        self.assertEqual(
            (await auth.enrollment_status(self.accounts, self.identity, row["id"]))[
                "status"
            ],
            "expired",
        )
