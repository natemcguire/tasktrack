import tempfile
import unittest
import uuid

from tasktrack.access import Access
from tasktrack.db import Error, Store
from tasktrack.service import Service


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.who = {
            "workspace_id": "w",
            "user_id": "u",
            "membership_id": "m",
            "kind": "internal",
            "access_role": "admin",
            "status": "active",
            "membership_revision": 1,
            "membership_capabilities": "[]",
            "actor": "owner",
            "bearer": False,
        }
        self.s = Service(self.store, access=Access(self.who))
        self.project = self.write("/projects", {"key": "TEST", "name": "Test"})
        self.task = self.write(
            "/tasks", {"project_id": self.project["id"], "title": "An important task"}
        )
        self.agent = self.who | {
            "bearer": True,
            "actor": "agent:test",
            "agent_id": "g",
            "token_id": "t",
            "token_project_ids": [str(self.project["id"])],
            "token_capabilities": [
                "project.read",
                "work.read",
                "work.edit",
                "work.accept",
            ],
        }
        self.s.access = Access(self.agent)

    def write(self, path, body):
        return self.s.mutate(
            "POST",
            "/api/v1" + path,
            body,
            self.s.access.identity["actor"],
            str(uuid.uuid4()),
        )[1]

    def request(self):
        return self.write(
            "/approval-requests",
            {
                "operation": "task.archive",
                "task_id": self.task["id"],
                "expected_version": self.task["version"],
                "reason": "Confirmed cleanup",
            },
        )

    def approve(self, row):
        self.s.access = Access(self.who | {"human_approval_digest": row["digest"]})
        self.write(
            "/approval-requests/" + row["id"] + "/decision", {"decision": "approve"}
        )
        self.s.access = Access(self.agent)

    def test_agent_cannot_bypass_or_approve_own_request(self):
        with self.assertRaises(Error):
            self.write(
                "/tasks/" + str(self.task["id"]) + "/archive",
                {"expected_version": 1, "reason": "bypass"},
            )
        row = self.request()
        with self.assertRaises(Error):
            self.write(
                "/approval-requests/" + row["id"] + "/decision", {"decision": "approve"}
            )
        self.s.access = Access(self.who)
        with self.assertRaises(Error):
            self.write(
                "/approval-requests/" + row["id"] + "/decision", {"decision": "approve"}
            )

    def test_approved_execution_is_atomic_and_replay_returns_same_effect(self):
        row = self.request()
        self.approve(row)
        first = self.write("/approval-requests/" + row["id"] + "/execute", {})
        second = self.write("/approval-requests/" + row["id"] + "/execute", {})
        self.assertEqual(first, second)
        self.assertIsNotNone(first["archived_at"])
        with self.store.connection() as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM events WHERE operation='archive'"
                ).fetchone()[0],
                1,
            )

    def test_stale_target_and_wrong_agent_do_not_execute(self):
        row = self.request()
        self.approve(row)
        self.s.mutate(
            "PATCH",
            "/api/v1/tasks/" + str(self.task["id"]),
            {"expected_version": 1, "title": "Changed"},
            "agent:test",
            str(uuid.uuid4()),
        )
        with self.assertRaises(Error):
            self.write("/approval-requests/" + row["id"] + "/execute", {})
        with self.store.connection() as c:
            self.assertEqual(
                c.execute("SELECT state FROM approval_requests").fetchone()[0],
                "approved",
            )
        self.s.access = Access(self.agent | {"agent_id": "other"})
        with self.assertRaises(Error):
            self.write("/approval-requests/" + row["id"] + "/execute", {})

    def test_expired_receipt_and_different_body_cannot_execute(self):
        row = self.request()
        self.approve(row)
        with self.assertRaises(Error):
            self.write("/approval-requests/" + row["id"] + "/execute", {"task_id": 900})
        with self.store.connection(write=True) as c:
            c.execute("UPDATE approval_requests SET expires_at=0")
        with self.assertRaises(Error):
            self.write("/approval-requests/" + row["id"] + "/execute", {})
