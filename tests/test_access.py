"""Real service/SQLite authorization checks; hidden UI is not the boundary."""

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from tasktrack.access import Access
from tasktrack.db import Error, Store
from tasktrack.service import Service


def identity(role="regular", kind="internal", **extra):
    return {
        "workspace_id": "tenant-a",
        "user_id": "u1",
        "membership_id": "m1",
        "kind": kind,
        "access_role": role,
        "status": "active",
        "membership_revision": 1,
        "bearer": False,
        "customer_id": "customer-a" if kind == "external" else None,
        **extra,
    }


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.admin = Service(self.store, access=Access(identity("admin")))
        self.regular = Service(self.store, access=Access(identity()))
        self.external = Service(self.store, access=Access(identity(kind="external")))
        self.project = self.write(
            self.admin, "/projects", {"key": "OPEN", "name": "Open project"}
        )
        self.hidden = self.write(
            self.admin, "/projects", {"key": "SECRET", "name": "Private name"}
        )
        self.task = self.write(
            self.admin,
            "/tasks",
            {
                "project_id": self.hidden["id"],
                "title": "Private title",
                "description_markdown": "Private content",
                "acceptance_criteria": ["Works"],
            },
        )
        self.open_task = self.write(
            self.admin,
            "/tasks",
            {"project_id": self.project["id"], "title": "Open task"},
        )
        with self.store.connection(write=True) as c:
            c.execute(
                "UPDATE project_access SET internal_access='restricted' WHERE project_id=?",
                (self.hidden["id"],),
            )

    def write(self, service, path, body, method="POST", key=None):
        return service.mutate(
            method, "/api/v1" + path, body, "actor", key or str(uuid.uuid4()), "session"
        )[1]

    def denied(self, fn, status=404):
        with self.assertRaises(Error) as caught:
            fn()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_project_lists_and_direct_routes_hide_restricted(self):
        projects = self.regular.read("/api/v1/projects")
        self.assertEqual(projects["total"], 1)
        self.assertEqual(projects["items"][0]["id"], self.project["id"])
        self.denied(lambda: self.regular.read("/api/v1/projects/SECRET"))
        self.denied(lambda: self.regular.read("/api/v1/projects/by-key/SECRET"))
        self.assertNotIn("Private", json.dumps(projects))

    def test_lists_counts_search_board_brief_and_tasks_obey_scope(self):
        for query in [{}, {"q": "Private"}, {"view": "summary"}, {"limit": "1"}]:
            result = self.regular.read("/api/v1/tasks", query)
            self.assertEqual(result["project_total"], 1)
            self.assertNotIn("Private", json.dumps(result))
        self.denied(
            lambda: self.regular.read(
                "/api/v1/tasks", {"project_id": self.hidden["id"]}
            )
        )
        self.denied(
            lambda: self.regular.read(
                "/api/v1/board", {"project_id": self.hidden["id"]}
            )
        )
        self.assertNotIn(
            "Private", json.dumps(self.regular.read("/api/v1/brief", {}, "actor"))
        )
        for suffix in ["", "/comments", "/attachments", "/history"]:
            self.denied(
                lambda: self.regular.read(
                    "/api/v1/tasks/" + str(self.task["id"]) + suffix
                )
            )

    def test_events_skip_hidden_content_but_advance_cursor(self):
        result = self.regular.read("/api/v1/events", {"source": self.admin.instance_id})
        self.assertGreater(result["after"], 0)
        self.assertTrue(
            all(r["project_id"] == self.project["id"] for r in result["items"])
        )
        self.assertNotIn("Private", json.dumps(result))
        self.assertEqual(
            self.external.read("/api/v1/events", {"source": self.admin.instance_id})[
                "items"
            ],
            [],
        )

    def test_attachment_metadata_cannot_bypass_task_scope(self):
        _, attachment = self.admin.mutate(
            "POST",
            f"/api/v1/tasks/{self.task['id']}/attachments",
            {"filename": "secret.txt", "media_type": "text/plain"},
            "actor",
            "upload",
            "session",
            upload=b"private",
        )
        self.denied(lambda: self.regular.attachment_metadata(attachment["id"]))

    def test_write_permission_precedes_idempotency_replay(self):
        body = {"expected_version": self.task["version"], "title": "Hidden updated"}
        self.write(self.admin, f"/tasks/{self.task['id']}", body, "PATCH", "same-key")
        self.denied(
            lambda: self.write(
                self.regular, f"/tasks/{self.task['id']}", body, "PATCH", "same-key"
            )
        )
        # Even a delegated settings editor cannot replay a prior admin grant response.
        grant = {
            "membership_id": "m1",
            "access": "manager",
            "customer_id": None,
            "capabilities": [],
            "can_view_invoices": False,
            "can_approve_scope": False,
        }
        access = {"expected_version": 1, "internal_access": "all", "grants": [grant]}
        self.write(
            self.admin,
            f"/projects/{self.project['id']}/access",
            access,
            "PATCH",
            "grant-key",
        )
        self.denied(
            lambda: self.write(
                self.regular,
                f"/projects/{self.project['id']}/access",
                access,
                "PATCH",
                "grant-key",
            ),
            403,
        )

    def test_regular_can_edit_work_but_not_project_create_or_accept(self):
        self.denied(
            lambda: self.write(self.regular, "/projects", {"key": "NO", "name": "No"}),
            403,
        )
        self.write(
            self.regular,
            f"/tasks/{self.open_task['id']}",
            {"expected_version": self.open_task["version"], "title": "Updated"},
            "PATCH",
        )
        self.denied(
            lambda: self.write(
                self.regular,
                f"/tasks/{self.open_task['id']}/complete",
                {"expected_version": 2, "acceptance_note": "Done"},
            ),
            403,
        )

    def test_project_grant_revocation_takes_effect_on_next_read(self):
        with self.store.connection(write=True) as c:
            c.execute(
                "INSERT INTO project_grants(project_id,membership_id) VALUES(?,?)",
                (self.hidden["id"], "m1"),
            )
        self.assertEqual(
            self.regular.read("/api/v1/tasks/" + str(self.task["id"]))["id"],
            self.task["id"],
        )
        with self.store.connection(write=True) as c:
            c.execute("DELETE FROM project_grants")
        self.denied(lambda: self.regular.read("/api/v1/tasks/" + str(self.task["id"])))

    def test_external_project_shell_excludes_internal_content(self):
        with self.store.connection(write=True) as c:
            c.execute(
                "UPDATE project_access SET customer_id='customer-a' WHERE project_id=?",
                (self.project["id"],),
            )
            c.execute(
                "INSERT INTO project_grants(project_id,membership_id,customer_id) VALUES(?,'m1','customer-a')",
                (self.project["id"],),
            )
        result = self.external.read("/api/v1/projects")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["brief_markdown"], "")
        self.assertNotIn("created_by", result["items"][0])
        self.assertEqual(self.external.read("/api/v1/tasks")["project_total"], 0)
        self.denied(
            lambda: self.external.read("/api/v1/tasks/" + str(self.open_task["id"]))
        )
        self.denied(
            lambda: self.write(
                self.external,
                "/tasks",
                {"project_id": self.project["id"], "title": "No"},
            ),
            403,
        )

    def test_agent_scope_filters_lists_and_mutations(self):
        s = Service(
            self.store,
            access=Access(
                identity(
                    "admin",
                    bearer=True,
                    token_project_ids=[str(self.project["id"])],
                    token_capabilities=["project.read", "work.read"],
                )
            ),
        )
        self.assertEqual(s.read("/api/v1/projects")["total"], 1)
        self.assertEqual(s.read("/api/v1/tasks")["total"], 1)
        self.denied(
            lambda: self.write(
                s, "/tasks", {"project_id": self.project["id"], "title": "No"}
            ),
            403,
        )

    def test_access_updates_conflict_and_audit(self):
        p = self.project["id"]
        path = f"/projects/{p}/access"
        self.write(
            self.admin,
            path,
            {"expected_version": 1, "internal_access": "restricted", "grants": []},
            "PATCH",
        )
        self.denied(
            lambda: self.write(
                self.admin,
                path,
                {"expected_version": 1, "internal_access": "all", "grants": []},
                "PATCH",
            ),
            409,
        )
        state = self.admin.read("/api/v1" + path)
        self.assertEqual(state["revision"], 2)
        self.assertEqual(state["internal_access"], "restricted")
        result = self.admin.read("/api/v1/events", {"source": self.admin.instance_id})
        self.assertIn(
            "project.access_changed", [e["operation"] for e in result["items"]]
        )

    def test_capabilities_are_computed_not_caller_claims(self):
        result = self.regular.read(
            "/api/v1/me/capabilities", {"project_id": self.project["id"]}
        )
        self.assertTrue(result["capabilities"]["work.edit"])
        self.assertFalse(result["capabilities"]["identity.manage"])
        self.assertFalse(result["capabilities"]["project.create"])


class MembershipMigrationTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.addCleanup(self.c.close)
        self.c.execute("PRAGMA foreign_keys=ON")
        root = Path(__file__).resolve().parents[1] / "cloudflare/migrations"
        for p in sorted(root.glob("*.sql")):
            if p.name.startswith("0007"):
                break
            self.c.executescript(p.read_text())
        self.c.execute("INSERT INTO users VALUES('u1','admin@example.com','Admin',1)")
        self.c.execute("INSERT INTO users VALUES('u2','member@example.com','Member',1)")
        self.c.execute("INSERT INTO workspaces VALUES('w','Workspace','u1',1)")
        self.c.execute("INSERT INTO memberships VALUES('w','u1','owner',1)")
        self.c.execute("INSERT INTO memberships VALUES('w','u2','member',1)")
        self.c.executescript((root / "0007_membership_roles.sql").read_text())

    def test_backfill_preserves_roles_and_assigns_unique_memberships(self):
        rows = self.c.execute(
            "SELECT id,user_id,kind,access_role,status,revision FROM memberships ORDER BY user_id"
        ).fetchall()
        self.assertEqual(len({r[0] for r in rows}), 2)
        self.assertEqual(rows[0][1:], ("u1", "internal", "admin", "active", 1))
        self.assertEqual(rows[1][1:], ("u2", "internal", "regular", "active", 1))
        self.c.execute("INSERT INTO users VALUES('u3','new@example.com','New',1)")
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u3','member',1)"
        )
        self.assertEqual(
            self.c.execute(
                "SELECT length(id) FROM memberships WHERE user_id='u3'"
            ).fetchone()[0],
            32,
        )

    def test_last_admin_suspension_demotion_delete_fail_atomically(self):
        for sql in [
            "UPDATE memberships SET status='suspended' WHERE user_id='u1'",
            "UPDATE memberships SET access_role='regular' WHERE user_id='u1'",
            "DELETE FROM memberships WHERE user_id='u1'",
        ]:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "last_admin"):
                self.c.execute(sql)
        self.c.execute(
            "UPDATE memberships SET access_role='admin',role='owner' WHERE user_id='u2'"
        )
        self.c.execute("UPDATE memberships SET status='suspended' WHERE user_id='u1'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "last_admin"):
            self.c.execute(
                "UPDATE memberships SET status='suspended' WHERE user_id='u2'"
            )

    def test_role_change_revokes_sessions_and_tokens_same_transaction(self):
        self.c.execute(
            "INSERT INTO sessions VALUES('session','u2','w','csrf',9999999999)"
        )
        self.c.execute(
            "INSERT INTO api_tokens VALUES('token','hash','u2','w','agent','agent:x',1,9999999999)"
        )
        self.c.execute(
            "UPDATE memberships SET access_role='billing' WHERE user_id='u2'"
        )
        self.assertEqual(
            self.c.execute("SELECT count(*) FROM sessions").fetchone()[0], 0
        )
        self.assertEqual(
            self.c.execute("SELECT count(*) FROM api_tokens").fetchone()[0], 0
        )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "invalid_external_membership"
        ):
            self.c.execute("UPDATE memberships SET kind='external' WHERE user_id='u2'")


if __name__ == "__main__":
    unittest.main()
