"""Membership audit persistence through the real SQL statements and triggers."""

import json
import sqlite3
import unittest
from pathlib import Path

from tasktrack.accounts import SESSION_AGE, timestamp
from tasktrack.db import Error
from tasktrack.membership_api import patch


class SQLiteAccounts:
    def __init__(self, connection):
        self.connection = connection
        self.db = self

    def owner(self, identity):
        if identity["access_role"] != "admin":
            raise Error(403, "forbidden", "Admin required.")

    async def one(self, sql, *args):
        if sql.startswith("SELECT expires_at FROM sessions"):
            return {"expires_at": timestamp() + SESSION_AGE}
        row = self.connection.execute(sql, args).fetchone()
        return dict(row) if row else None

    def statement(self, sql, *args):
        return sql, args

    async def batch(self, statements):
        with self.connection:
            return [
                {
                    "results": [
                        dict(row)
                        for row in self.connection.execute(sql, args).fetchall()
                    ]
                }
                for sql, args in statements
            ]


class MembershipAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        root = Path(__file__).resolve().parents[1] / "cloudflare/migrations"
        for migration in sorted(root.glob("*.sql")):
            self.c.executescript(migration.read_text())
        self.c.execute("INSERT INTO users VALUES('u1','admin@example.com','Admin',1)")
        self.c.execute("INSERT INTO users VALUES('u2','member@example.com','Member',1)")
        self.c.execute(
            "INSERT INTO workspaces(id,name,created_by,created_at) VALUES('w','Workspace','u1',1)"
        )
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u1','owner',1)"
        )
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u2','member',1)"
        )
        self.mid = self.c.execute(
            "SELECT id FROM memberships WHERE user_id='u2'"
        ).fetchone()[0]
        self.accounts = SQLiteAccounts(self.c)
        self.identity = {
            "workspace_id": "w",
            "access_role": "admin",
            "actor": "user:u1",
            "bearer": False,
            "session_hash": "test",
        }

    async def test_conversion_reason_is_persisted_with_actor_and_snapshots(self):
        result = await patch(
            self.accounts,
            self.identity,
            self.mid,
            {
                "expected_version": 1,
                "kind": "external",
                "customer_id": "customer-a",
                "reason": "  Moved to customer team  ",
            },
        )
        audit = self.c.execute("SELECT * FROM membership_audit").fetchone()
        self.assertEqual(audit["changed_by"], "user:u1")
        self.assertEqual(json.loads(audit["before_json"])["capabilities"], [])
        after = json.loads(audit["after_json"])
        self.assertEqual(after["reason"], "Moved to customer team")
        self.assertEqual(after["kind"], "external")
        self.assertEqual(after["revision"], result["revision"])
        with self.assertRaises(Error):
            await patch(
                self.accounts,
                self.identity,
                self.mid,
                {"expected_version": 1, "status": "suspended"},
            )
        self.assertEqual(
            self.c.execute("SELECT count(*) FROM membership_audit").fetchone()[0], 1
        )

    async def test_invalid_reasons_do_not_mutate_membership(self):
        for reason in (123, ["reason"], "x" * 2001):
            with self.assertRaises(Error):
                await patch(
                    self.accounts,
                    self.identity,
                    self.mid,
                    {"expected_version": 1, "status": "suspended", "reason": reason},
                )
        self.assertEqual(
            self.c.execute("SELECT count(*) FROM membership_audit").fetchone()[0], 0
        )
        self.assertEqual(
            self.c.execute(
                "SELECT revision FROM memberships WHERE id=?", (self.mid,)
            ).fetchone()[0],
            1,
        )
