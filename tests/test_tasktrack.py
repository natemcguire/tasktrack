"""Behavior checks against disposable real SQLite, HTTP and CLI processes."""

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tasktrack.db import MIGRATIONS, SCHEMA_VERSION, Error, Store
from tasktrack.service import Service

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = {
    "summary": "Retry handling implemented.",
    "next_action": "Check a disconnected retry.",
    "workspace": "/work/harbor",
    "branch": "fix/retry",
    "acceptance_remaining": ["One receipt after a disconnect"],
    "evidence": [{"label": "Test log", "uri": "file:///work/harbor/tests.txt"}],
}
RESULT = {
    "summary": "Retries preserve one receipt.",
    "evidence": [{"label": "Regression run", "uri": "https://example.org/build/1"}],
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "data"
        self.store = Store(self.directory)
        self.s = Service(self.store)
        self.project = self.write(
            "projects",
            {
                "key": "HBR",
                "name": "Harbor",
                "brief_markdown": "# PRD\nOne safe order per checkout.",
            },
        )

    def write(
        self,
        path,
        body,
        actor="nate",
        session=None,
        key=None,
        method="POST",
        upload=None,
    ):
        return self.s.mutate(
            method,
            "/api/v1/" + path,
            body,
            actor,
            key or str(uuid.uuid4()),
            session,
            "api",
            upload,
        )[1]

    def task(self, **extra):
        return self.write(
            "tasks",
            {
                "project_id": self.project["id"],
                "title": "Retry checkout",
                "description_markdown": "Keep one order.",
                "acceptance_criteria": ["One receipt."],
                **extra,
            },
        )

    def action(self, task, action, actor="nate", session=None, **body):
        return self.write(
            f"tasks/{task['id']}/{action}",
            {"expected_version": task["version"], **body},
            actor,
            session,
        )

    def get(self, task):
        return self.s.read(f"/api/v1/tasks/{task['id']}")

    def history(self, task):
        return self.s.read(f"/api/v1/tasks/{task['id']}/history")["items"]

    def fails(self, status, call, code=None):
        with self.assertRaises(Error) as captured:
            call()
        self.assertEqual(captured.exception.status, status, captured.exception.payload)
        if code:
            self.assertEqual(captured.exception.payload["error"]["code"], code)

    def finish(self, task):
        if task["status"] == "backlog":
            if task["kind"] == "epic":
                task = self.action(task, "move", status="in_progress")
            else:
                task = self.action(task, "claim", session="one")
        task = self.action(
            task, "submit", session="one" if task["execution"] else None, result=RESULT
        )
        return self.action(
            task, "complete", acceptance_note="The retry regression and criteria pass."
        )


class DomainTests(Fixture):
    def test_structural_quality_gate_and_protected_fields(self):
        draft = self.task(
            description_markdown="Very long prose. " * 100, acceptance_criteria=[]
        )
        self.fails(422, lambda: self.action(draft, "claim", session="one"), "not_ready")
        self.fails(
            422,
            lambda: self.write(
                f"tasks/{draft['id']}",
                {"expected_version": 1, "status": "done"},
                method="PATCH",
            ),
        )
        self.assertEqual(len(self.history(draft)), 1)
        short = self.action(self.task(), "claim", session="one")
        self.assertEqual(short["status"], "in_progress")
        self.fails(422, lambda: self.action(self.task(), "move", status="in_progress"))
        self.assertIsNone(self.get(draft)["execution"])

    def test_claim_cannot_overwrite_at_current_version(self):
        claimed = self.action(self.task(), "claim", session="old")
        self.fails(
            409, lambda: self.action(claimed, "claim", session="new"), "claim_conflict"
        )
        current = self.get(claimed)
        self.assertEqual(current["execution"]["session"], "old")
        self.assertEqual(
            [e["operation"] for e in self.history(claimed)], ["create", "claim"]
        )

    def test_lost_session_resume_handoff_and_owner_guards(self):
        task = self.action(self.task(), "claim", actor="a", session="old")
        for actor, session in [("b", "old"), ("a", "new")]:
            for operation, body in [
                ("checkpoint", {"checkpoint": CHECKPOINT}),
                ("submit", {"result": RESULT}),
                ("handoff", {"checkpoint": CHECKPOINT}),
                (
                    "move",
                    {"status": "backlog", "reason": "Pause", "checkpoint": CHECKPOINT},
                ),
            ]:
                self.fails(
                    409,
                    lambda op=operation, b=body, a=actor, s=session: self.action(
                        task, op, a, s, **b
                    ),
                )
        self.fails(
            409,
            lambda: self.write(
                f"tasks/{task['id']}",
                {"expected_version": task["version"], "assignee": "b"},
                method="PATCH",
            ),
        )
        task = self.action(task, "checkpoint", "a", "old", checkpoint=CHECKPOINT)
        # New service object simulates a replacement process without in-memory context.
        replacement = Service(Store(self.directory))
        brief = replacement.read("/api/v1/brief", actor="a")
        self.assertEqual(
            brief["items"][0]["checkpoint"]["next_action"], CHECKPOINT["next_action"]
        )
        self.assertEqual(self.get(task)["version"], task["version"])
        self.fails(409, lambda: self.action(task, "resume", "b", "new"))
        task = self.action(task, "resume", "a", "new")
        self.assertEqual(task["execution"]["session"], "new")
        task = self.action(task, "handoff", "a", "new", checkpoint=CHECKPOINT, to="b")
        self.assertEqual(task["assignee"], "b")
        self.assertIsNone(task["execution"])
        self.assertTrue(task["pickup_needed"])
        task = self.action(task, "claim", "b", "replacement")
        self.assertEqual(task["execution"]["actor"], "b")
        self.assertEqual(
            self.history(task)[-2]["detail"]["after"]["checkpoint"], task["checkpoint"]
        )

    def test_review_rules_apply_to_moves_and_manual_work(self):
        task = self.task(assignee="nate")
        task = self.action(task, "move", status="in_progress")
        self.assertIsNone(task["execution"])
        self.fails(409, lambda: self.action(task, "submit", actor="b", result=RESULT))
        self.fails(422, lambda: self.action(task, "move", status="review"))
        task = self.action(task, "move", status="review", result=RESULT)
        self.assertEqual(task["result"], RESULT)
        task = self.action(
            task, "request-changes", actor="reviewer", reason="Add a disconnect test."
        )
        self.assertIsNone(task["execution"])
        task = self.action(task, "submit", result=RESULT)
        task = self.action(
            task,
            "move",
            actor="reviewer",
            status="done",
            acceptance_note="Verified the disconnect test.",
        )
        self.assertEqual(task["completion"]["actor"], "reviewer")
        task = self.action(task, "reopen", reason="New regression")
        self.assertEqual(task["status"], "backlog")
        self.assertEqual(task["result"], RESULT)
        self.assertEqual(
            task["completion"]["acceptance_note"], "Verified the disconnect test."
        )

    def test_dependencies_cycles_archival_and_reopened_prerequisite(self):
        first = self.task(title="Prerequisite")
        second = self.task(title="Dependent", dependency_ids=[first["id"]])
        self.fails(422, lambda: self.action(second, "claim", session="one"))
        self.fails(422, lambda: self.action(first, "archive", reason="Hide"))
        self.fails(
            422,
            lambda: self.write(
                f"tasks/{first['id']}",
                {"expected_version": 1, "dependency_ids": [second["id"]]},
                method="PATCH",
            ),
        )
        self.assertEqual(self.get(first)["dependency_ids"], [])
        first = self.finish(first)
        second = self.action(self.get(second), "claim", session="one")
        first = self.action(first, "reopen", reason="Regression")
        current = self.get(second)
        self.assertEqual(current["status"], "in_progress")
        self.assertTrue(current["blocked"])
        self.fails(
            422, lambda: self.action(current, "submit", session="one", result=RESULT)
        )
        current = self.action(current, "block", reason="Waiting for data")
        current = self.action(current, "unblock", reason="Data arrived")
        self.assertTrue(
            current["blocked"],
            "Dependency remains blocked after resolving the manual overlay",
        )

    def test_epic_completion_and_atomic_child_reopen(self):
        epic = self.task(kind="epic", assignee="nate", title="Checkout reliability")
        child = self.task(parent_id=epic["id"])
        foreign = self.write("projects", {"key": "OTHER", "name": "Other"})
        self.fails(
            422, lambda: self.task(project_id=foreign["id"], parent_id=epic["id"])
        )
        self.fails(422, lambda: self.task(kind="epic", parent_id=epic["id"]))
        self.fails(422, lambda: self.action(epic, "claim", session="one"))
        self.fails(422, lambda: self.action(child, "archive", reason="Skip it"))
        epic = self.action(epic, "move", status="in_progress")
        epic = self.action(epic, "submit", result=RESULT)
        self.fails(
            422, lambda: self.action(epic, "complete", acceptance_note="Not yet")
        )
        child = self.finish(child)
        epic = self.action(
            epic, "complete", acceptance_note="All child tasks accepted."
        )
        self.assertEqual(epic["progress"], {"done": 1, "total": 1})
        self.fails(422, lambda: self.action(child, "reopen", reason="Bug"))
        self.assertEqual(self.get(epic)["status"], "done")
        child = self.action(child, "reopen", reason="Bug", reopen_parent=True)
        self.assertEqual(self.get(epic)["status"], "backlog")
        self.assertEqual(
            self.history(epic)[-1]["detail"]["reopened_by_child"], child["id"]
        )

    def test_rename_aliases_and_noops(self):
        epic = self.task(kind="epic")
        task = self.task(parent_id=epic["id"])
        note = self.write(
            f"tasks/{task['id']}/comments", {"body": "Keep this decision."}
        )
        attachment = self.write(
            f"tasks/{task['id']}/attachments",
            {"filename": "proof.txt", "comment_id": note["id"]},
            upload=b"exact evidence",
        )
        project = self.write(
            f"projects/{self.project['id']}",
            {"expected_version": 1, "key": "HARBOR", "name": "Harbor launch"},
            actor="reviewer",
            method="PATCH",
        )
        self.assertEqual(project["key"], "HARBOR")
        self.assertEqual(project["version"], 2)
        current = self.s.read(f"/api/v1/tasks/HBR-{task['id']}")
        self.assertEqual(current["reference"], f"HARBOR-{task['id']}")
        self.assertEqual(current["parent_id"], epic["id"])
        self.assertEqual(
            self.s.attachment(attachment["id"])[1].read_bytes(), b"exact evidence"
        )
        self.assertEqual(
            self.s.read("/api/v1/projects/by-key/HBR")["id"], project["id"]
        )
        self.fails(
            409,
            lambda: self.write(
                "projects", {"key": "HBR", "name": "Cannot steal alias"}
            ),
        )
        noop = self.write(
            f"projects/{project['id']}",
            {"expected_version": 2, "key": "HARBOR"},
            method="PATCH",
        )
        self.assertEqual(noop["version"], 2)
        renamed = self.write(
            f"projects/{project['id']}",
            {"expected_version": 2, "key": "HBR"},
            method="PATCH",
        )
        self.assertEqual(renamed["id"], project["id"])
        events = self.s.read("/api/v1/events", {"source": self.s.instance_id})["items"]
        renames = [e for e in events if e["operation"] == "rename"]
        self.assertEqual(len(renames), 2)
        self.assertEqual(renames[0]["actor"], "reviewer")
        other = self.write("projects", {"key": "OTHER", "name": "Other"})
        self.fails(
            404, lambda: self.s.read(f"/api/v1/tasks/{other['key']}-{task['id']}")
        )

    def test_idempotent_creation_and_version_retry(self):
        body = {"project_id": self.project["id"], "title": "One task"}
        task = self.write("tasks", body, key="create-once")
        repeated = self.write("tasks", body, key="create-once")
        self.assertEqual(task, repeated)
        self.assertEqual(self.s.read("/api/v1/tasks")["total"], 1)
        self.assertEqual(len(self.history(task)), 1)
        self.fails(
            409,
            lambda: self.write(
                "tasks", {**body, "title": "Different"}, key="create-once"
            ),
        )
        update = {"expected_version": 1, "title": "Updated once"}
        current = self.write(
            f"tasks/{task['id']}", update, key="update-once", method="PATCH"
        )
        self.assertEqual(
            current,
            self.write(
                f"tasks/{task['id']}", update, key="update-once", method="PATCH"
            ),
        )
        self.fails(
            409,
            lambda: self.write(f"tasks/{task['id']}", update, method="PATCH"),
            "version_conflict",
        )
        noop = self.write(
            f"tasks/{task['id']}",
            {"expected_version": 2, "title": "Updated once"},
            method="PATCH",
        )
        self.assertEqual(noop["version"], 2)
        self.assertEqual(len(self.history(task)), 2)

    def test_comments_attachments_exact_bytes_and_independent_versions(self):
        task, other = self.task(), self.task()
        comment = self.write(
            f"tasks/{task['id']}/comments",
            {"body": "A durable note."},
            actor="agent",
            session="s1",
            key="comment",
        )
        self.assertEqual(comment["session"], "s1")
        self.assertEqual(self.get(task)["version"], 1)
        data = b"\x00\xffexact\r\nbytes\x00"
        metadata = {
            "filename": "../../outside.html",
            "comment_id": comment["id"],
            "media_type": "text/html",
        }
        self.fails(
            422,
            lambda: self.write(
                f"tasks/{other['id']}/attachments", metadata, upload=data
            ),
        )
        a = self.write(
            f"tasks/{task['id']}/attachments", metadata, upload=data, key="upload"
        )
        self.assertEqual(
            a,
            self.write(
                f"tasks/{task['id']}/attachments", metadata, upload=data, key="upload"
            ),
        )
        self.assertEqual(a["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.s.attachment(a["id"])[1].read_bytes(), data)
        self.assertFalse((Path(self.temp.name) / "outside.html").exists())
        self.assertEqual(self.get(task)["version"], 1)
        self.assertEqual(
            [e["operation"] for e in self.history(task)],
            ["create", "comment", "attachment"],
        )
        self.fails(
            413,
            lambda: self.write(
                f"tasks/{task['id']}/attachments",
                metadata,
                upload=b"x" * (10 * 1024 * 1024 + 1),
            ),
        )

    def test_pagination_filtered_events_and_order_restart(self):
        tasks = [
            self.task(title=f"Task {i}", priority="high" if i % 2 else "normal")
            for i in range(7)
        ]
        query = {"limit": "2", "priority": "high"}
        page = self.s.read("/api/v1/tasks", query)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["project_total"], 7)
        next_page = self.s.read(
            "/api/v1/tasks", {**query, "cursor": page["next_cursor"]}
        )
        self.assertEqual(
            [x["id"] for x in page["items"] + next_page["items"]],
            [tasks[i]["id"] for i in (1, 3, 5)],
        )
        self.fails(
            400, lambda: self.s.read("/api/v1/tasks", {"cursor": page["next_cursor"]})
        )
        self.fails(422, lambda: self.s.read("/api/v1/tasks", {"pretend": "true"}))
        self.fails(409, lambda: self.s.read("/api/v1/events", {"source": "wrong"}))
        event_page = self.s.read(
            "/api/v1/events",
            {
                "source": self.s.instance_id,
                "task_id": str(tasks[-1]["id"]),
                "limit": "2",
            },
        )
        self.assertEqual(event_page["items"], [])
        self.assertTrue(event_page["has_more"])
        self.assertEqual(event_page["after"], 2)
        last = self.action(
            tasks[-1], "move", status="backlog", before_id=tasks[0]["id"]
        )
        restarted = Service(Store(self.directory))
        self.assertEqual(restarted.read("/api/v1/tasks")["items"][0]["id"], last["id"])
        noop = self.action(last, "move", status="backlog", before_id=tasks[0]["id"])
        self.assertEqual(noop["version"], last["version"])

    def test_thread_links_rename_and_absent_inbox(self):
        link = {
            "source": "9f3ee2dc-eae5-4ca2-9542-d3c6f137c2d9",
            "project": "harbor",
            "thread_id": "thr_decision",
            "primary": True,
        }
        task = self.task(thread_links=[link])
        self.assertIsNone(task["thread_links"][0]["url"])
        self.write(
            f"projects/{self.project['id']}",
            {"expected_version": 1, "key": "NEW"},
            method="PATCH",
        )
        self.assertEqual(self.get(task)["thread_links"][0]["source"], link["source"])
        self.assertEqual(self.get(task)["thread_links"][0]["project"], "harbor")
        self.fails(
            422, lambda: self.task(thread_links=[link, {**link, "thread_id": "other"}])
        )
        (self.directory / "config.json").write_text(
            json.dumps(
                {
                    "inbox_instances": {
                        link["source"]: {
                            "thread_url_template": "http://localhost:9999/explicit/{project}/{thread_id}"
                        }
                    }
                }
            )
        )
        resolved = Service(Store(self.directory)).read(f"/api/v1/tasks/{task['id']}")
        self.assertEqual(
            resolved["thread_links"][0]["url"],
            "http://localhost:9999/explicit/harbor/thr_decision",
        )

    def test_backup_restore_all_rows_and_hashes(self):
        epic = self.task(kind="epic")
        child = self.task(parent_id=epic["id"])
        self.write(
            f"tasks/{child['id']}/comments",
            {"body": "Preserve actor and history."},
            actor="a",
            session="before",
        )
        self.write(
            f"tasks/{child['id']}/attachments",
            {"filename": "file.bin"},
            upload=b"evidence",
        )
        child = self.action(child, "claim", actor="a", session="before")
        child = self.action(
            child, "checkpoint", actor="a", session="before", checkpoint=CHECKPOINT
        )
        backup = Path(self.temp.name) / "backup"
        self.store.backup(backup)
        restored = Path(self.temp.name) / "restored"
        Store.restore(backup, restored)
        other = Store(restored)
        with self.store.connection() as before, other.connection() as after:
            for table in (
                "instance",
                "projects",
                "project_aliases",
                "tasks",
                "dependencies",
                "comments",
                "attachments",
                "events",
                "receipts",
                "thread_links",
            ):
                self.assertEqual(
                    [tuple(x) for x in before.execute(f"SELECT * FROM {table}")],
                    [tuple(x) for x in after.execute(f"SELECT * FROM {table}")],
                    table,
                )
        self.assertEqual(
            Service(other).read(f"/api/v1/tasks/{child['id']}")["checkpoint"],
            self.get(child)["checkpoint"],
        )
        digest = hashlib.sha256(b"evidence").hexdigest()
        self.assertEqual((other.blobs / digest).read_bytes(), b"evidence")
        self.fails(409, lambda: Store.restore(backup, restored))
        (backup / "blobs" / digest).write_bytes(b"corrupted")
        self.fails(422, lambda: Store.restore(backup, Path(self.temp.name) / "bad"))
        self.assertFalse((Path(self.temp.name) / "bad").exists())

    def test_migrations_repeat_safe_and_unsupported_schemas(self):
        old = Path(self.temp.name) / "v1"
        old.mkdir()
        with closing(sqlite3.connect(old / "tasktrack.sqlite3")) as c, c:
            for sql in MIGRATIONS[1]:
                c.execute(sql)
            c.execute(
                "INSERT INTO instance VALUES ('legacy-v1-instance','2026-01-01T00:00:00Z')"
            )
            c.execute(
                "INSERT INTO projects VALUES (42,'OLD','Old project','Historical brief','[]',1,NULL,NULL,'2026-01-01T00:00:00Z',NULL,NULL,'2026-01-01T00:00:00Z')"
            )
            c.execute("INSERT INTO project_aliases VALUES ('OLD',42)")
            c.execute("PRAGMA user_version=1")
        for _ in range(2):
            upgraded = Store(old)
            self.assertEqual(
                Service(upgraded).read("/api/v1/projects/42")["brief_markdown"],
                "Historical brief",
            )
            self.assertIsNone(
                Service(upgraded).read("/api/v1/projects/42")["created_by"]
            )
        with upgraded.connection() as c:
            self.assertEqual(
                c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            c.execute("PRAGMA user_version=999")
        self.fails(503, lambda: Store(old), "newer_schema")
        unknown = Path(self.temp.name) / "unversioned"
        unknown.mkdir()
        with closing(sqlite3.connect(unknown / "tasktrack.sqlite3")) as c, c:
            c.execute("CREATE TABLE old_tasks(id PRIMARY KEY)")
            c.execute("INSERT INTO old_tasks VALUES (874)")
        self.fails(422, lambda: Store(unknown), "legacy_schema")
        with closing(sqlite3.connect(unknown / "tasktrack.sqlite3")) as c:
            self.assertEqual(c.execute("SELECT * FROM old_tasks").fetchall(), [(874,)])
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_migration_failure_rolls_back_and_storage_busy_is_retryable(self):
        old = Path(self.temp.name) / "broken-migration"
        old.mkdir()
        original = MIGRATIONS[2]
        MIGRATIONS[2] = original + ["INVALID SQL"]
        try:
            with self.assertRaises(sqlite3.OperationalError):
                Store(old)
        finally:
            MIGRATIONS[2] = original
        with closing(sqlite3.connect(old / "tasktrack.sqlite3")) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
                [],
            )
        self.store.timeout = 0.05
        with self.store.connection(write=True):
            self.fails(503, lambda: self.task(), "storage_busy")
        self.assertEqual(self.s.read("/api/v1/tasks")["total"], 0)


class HTTPTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = {
            **os.environ,
            "TT_DATA_DIR": str(self.directory),
            "PYTHONPATH": str(ROOT),
        }
        self.env.pop("TT_ACTOR", None)
        self.env.pop("TT_SESSION", None)
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tasktrack", "serve", "--port", "0"],
            cwd=ROOT,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        import select

        if not select.select([self.process.stdout], [], [], 10)[0]:
            self.process.kill()
            self.fail("HTTP process did not start in 10 seconds")
        line = self.process.stdout.readline()
        self.url = line.split("listening on ")[1].split(" ")[0]

    def stop_server(self):
        self.process.terminate()
        self.process.communicate(timeout=10)

    def http(
        self,
        path,
        method="GET",
        body=None,
        actor="nate",
        session=None,
        key=None,
        headers=None,
    ):
        h = {
            "Content-Type": "application/json",
            **({"X-Actor": actor} if actor else {}),
        }
        if method != "GET":
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        if session:
            h["X-Session"] = session
        h.update(headers or {})
        req = Request(
            self.url + "/api/v1/" + path,
            method=method,
            headers=h,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "tasktrack", *args, "--json"],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_http_cli_roundtrip_stale_edit_restart_and_attribution(self):
        renamed = self.cli(
            "project",
            "update",
            str(self.project["id"]),
            "--key",
            "CLI",
            "--name",
            "CLI project name",
            "--version",
            "1",
            "--as",
            "codex",
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        self.assertEqual(json.loads(renamed.stdout)["key"], "CLI")
        status, renamed = self.http(
            f"projects/{self.project['id']}",
            "PATCH",
            {"expected_version": 2, "key": "WEB"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["aliases"], ["CLI", "HBR", "WEB"])
        self.assertEqual(self.http("projects/by-key/HBR")[1]["key"], "WEB")
        status, task = self.http(
            "tasks",
            "POST",
            {"project_id": self.project["id"], "title": "Browser draft"},
            headers={"X-Via": "ui"},
        )
        self.assertEqual(status, 201)
        updated = self.cli(
            "task",
            "update",
            str(task["id"]),
            "--title",
            "CLI revision",
            "--version",
            "1",
            "--as",
            "codex",
        )
        self.assertEqual(updated.returncode, 0, updated.stderr)
        status, conflict = self.http(
            f"tasks/{task['id']}",
            "PATCH",
            {"expected_version": 1, "title": "Stale browser title"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["current_version"], 2)
        self.assertEqual(self.get(task)["title"], "CLI revision")
        self.stop_server()
        offline = self.cli("task", "get", str(task["id"]))
        self.assertEqual(json.loads(offline.stdout)["title"], "CLI revision")
        self.start_server()
        self.assertEqual(self.http(f"tasks/{task['id']}")[1]["title"], "CLI revision")
        events = self.history(task)
        self.assertEqual(
            [(e["actor"], e["via"]) for e in events], [("nate", "ui"), ("codex", "cli")]
        )
        self.assertEqual(
            self.cli(
                "task",
                "update",
                str(task["id"]),
                "--title",
                "No actor",
                "--version",
                "2",
            ).returncode,
            2,
        )
        self.assertEqual(
            self.cli(
                "task",
                "update",
                str(task["id"]),
                "--title",
                "Stale",
                "--version",
                "1",
                "--as",
                "nate",
            ).returncode,
            3,
        )

    def test_concurrent_cli_board_order_persists_after_restart(self):
        tasks = [self.task(title=f"Card {i}") for i in range(3)]
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tasktrack",
                    "task",
                    "move",
                    str(task["id"]),
                    "--status",
                    "backlog",
                    "--before-id",
                    str(tasks[0]["id"]),
                    "--version",
                    "1",
                    "--as",
                    "nate",
                    "--json",
                ],
                cwd=ROOT,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for task in tasks[1:]
        ]
        for process in processes:
            out, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
            self.assertEqual(json.loads(out)["version"], 2)
        listed = self.http("tasks?status=backlog")[1]["items"]
        self.assertEqual(listed[-1]["id"], tasks[0]["id"])
        self.assertEqual({t["id"] for t in listed[:2]}, {t["id"] for t in tasks[1:]})
        self.assertEqual(len({t["position"] for t in listed}), 3)
        self.stop_server()
        self.start_server()
        self.assertEqual(
            [t["id"] for t in self.http("tasks?status=backlog")[1]["items"]],
            [t["id"] for t in listed],
        )

    def test_real_cli_http_claim_race(self):
        task = self.task()
        gate = time.time() + 0.5
        cli_code = "import os,sys,time; time.sleep(max(0,float(sys.argv[1])-time.time())); os.execv(sys.executable,[sys.executable,'-m','tasktrack','task','claim',sys.argv[2],'--version','1','--as','cli-agent','--session','cli-race','--json'])"
        http_code = """import json,sys,time,urllib.request,urllib.error
time.sleep(max(0,float(sys.argv[1])-time.time()))
request=urllib.request.Request(sys.argv[2],data=b'{"expected_version":1}',headers={'Content-Type':'application/json','X-Actor':'http-agent','X-Session':'http-race','Idempotency-Key':'race'})
try:
 response=urllib.request.urlopen(request)
except urllib.error.HTTPError as error:
 response=error
print(json.dumps({'status':response.status,'body':json.loads(response.read())}))
"""
        cli = subprocess.Popen(
            [sys.executable, "-c", cli_code, str(gate), str(task["id"])],
            cwd=ROOT,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        http = subprocess.Popen(
            [
                sys.executable,
                "-c",
                http_code,
                str(gate),
                self.url + f"/api/v1/tasks/{task['id']}/claim",
            ],
            cwd=ROOT,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        cli_out, cli_error = cli.communicate(timeout=10)
        http_out, http_error = http.communicate(timeout=10)
        self.assertEqual(http.returncode, 0, http_error)
        status = json.loads(http_out)["status"]
        self.assertEqual(
            sorted(
                [
                    200 if cli.returncode == 0 else 409 if cli.returncode == 3 else 500,
                    status,
                ]
            ),
            [200, 409],
            cli_error,
        )
        current = self.get(task)
        self.assertEqual(current["version"], 2)
        self.assertEqual(
            current["execution"]["actor"],
            "cli-agent" if cli.returncode == 0 else "http-agent",
        )
        self.assertEqual(
            len([e for e in self.history(task) if e["operation"] == "claim"]), 1
        )

    def test_http_validation_security_and_multipart_retry(self):
        task = self.task()
        self.assertEqual(
            self.http("projects", "POST", {"key": "X", "name": "X"}, actor=None)[0], 400
        )
        self.assertEqual(
            self.http("health", headers={"Origin": "https://evil.example"})[0], 400
        )
        self.assertEqual(self.http("health", headers={"Host": "evil.example"})[0], 400)
        self.assertEqual(self.http("tasks?made_up=yes")[0], 422)
        self.assertEqual(
            self.http("projects", "POST", {}, headers={"Content-Type": "text/plain"})[
                0
            ],
            400,
        )
        content = b"<html><script>alert(1)</script>exact bytes\x00</html>"
        responses = []
        for boundary in ("boundary1", "different-boundary"):
            body = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="../../evidence.html"\r\nContent-Type: text/html\r\n\r\n'.encode()
                + content
                + f"\r\n--{boundary}--\r\n".encode()
            )
            request = Request(
                self.url + f"/api/v1/tasks/{task['id']}/attachments",
                data=body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=" + boundary,
                    "X-Actor": "uploader",
                    "Idempotency-Key": "same-upload",
                },
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 201)
                responses.append(json.loads(response.read()))
        self.assertEqual(responses[0], responses[1])
        with urlopen(self.url + responses[0]["download_url"]) as response:
            self.assertEqual(response.read(), content)
            self.assertEqual(
                response.headers["Content-Type"], "application/octet-stream"
            )
            self.assertTrue(
                response.headers["Content-Disposition"].startswith("attachment;")
            )
        # An interrupted request cannot leave a metadata row pointing to partial bytes.
        from urllib.parse import urlsplit

        port = urlsplit(self.url).port
        with socket.create_connection(("127.0.0.1", port)) as conn:
            conn.sendall(
                f"POST /api/v1/tasks/{task['id']}/attachments HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: multipart/form-data; boundary=x\r\nContent-Length: 1000\r\nX-Actor: test\r\nIdempotency-Key: interrupted\r\n\r\n--x\r\npartial".encode()
            )
            conn.shutdown(socket.SHUT_WR)
            conn.recv(4096)
        self.assertEqual(
            self.s.read(f"/api/v1/tasks/{task['id']}/attachments")["total"], 1
        )


if __name__ == "__main__":
    unittest.main()
