"""Comprehensive regression tests for custom board columns and safe workflow (task104)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from tasktrack.access import Access
from tasktrack.db import MIGRATIONS, SCHEMA_VERSION, Error, Store
from tasktrack.service import Service

ROOT = Path(__file__).resolve().parents[1]

CHECKPOINT = {
    "summary": "Implementation ongoing.",
    "next_action": "Run unit tests.",
    "workspace": "/work/board",
    "branch": "feat/board",
    "acceptance_remaining": ["Acceptance test 1"],
    "evidence": [{"label": "Build log", "uri": "file:///work/build.txt"}],
}
RESULT = {
    "summary": "Completed feature work.",
    "evidence": [{"label": "CI Run", "uri": "https://example.org/build/104"}],
}


def identity(role="manager", kind="internal", **extra):
    return {
        "workspace_id": "tenant-test",
        "user_id": "u-test",
        "membership_id": f"m-{role}-{kind}",
        "kind": kind,
        "access_role": role,
        "status": "active",
        "membership_revision": 1,
        "bearer": False,
        "customer_id": "cust-test" if kind == "external" else None,
        **extra,
    }


class BoardColumnsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "data"
        self.store = Store(self.directory)
        self.s = Service(self.store)
        self.project = self.write(
            "projects",
            {
                "key": "COL",
                "name": "Columns Project",
                "brief_markdown": "# Board\nTesting custom columns.",
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
        service=None,
    ):
        svc = service or self.s
        return svc.mutate(
            method,
            "/api/v1/" + path,
            body,
            actor,
            key or str(uuid.uuid4()),
            session,
            "api",
        )[1]

    def read(self, path, query=None, actor="nate", service=None):
        svc = service or self.s
        return svc.read("/api/v1/" + path, query or {}, actor)

    def task(self, **extra):
        return self.write(
            "tasks",
            {
                "project_id": self.project["id"],
                "title": "Task item",
                "description_markdown": "Test description",
                "acceptance_criteria": ["Test criterion"],
                **extra,
            },
        )

    def action(self, task, action, actor="nate", session=None, service=None, **body):
        return self.write(
            f"tasks/{task['id']}/{action}",
            {"expected_version": task["version"], **body},
            actor,
            session,
            service=service,
        )

    # 1. Migration from v4 to v5, preserving tasks, versions, and rerun idempotency
    def test_migration_v4_to_v5_and_idempotency(self):
        mig_dir = Path(self.temp.name) / "migration_data"
        mig_dir.mkdir(parents=True, exist_ok=True)
        db_path = mig_dir / "tasktrack.sqlite3"

        # Apply migrations 1 through 4 manually
        conn = sqlite3.connect(db_path)
        for v in (1, 2, 3, 4):
            for statement in MIGRATIONS[v]:
                conn.execute(statement)
        conn.execute("PRAGMA user_version = 4")

        # Insert 2 projects and tasks in all 4 statuses with fixed versions
        conn.execute(
            "INSERT INTO projects(id, key, name, brief_markdown, document_links, version, created_at, updated_at) "
            "VALUES (1, 'OLD', 'Old Project', '', '[]', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO projects(id, key, name, brief_markdown, document_links, version, created_at, updated_at) "
            "VALUES (2, 'SEC', 'Second Project', '', '[]', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )

        tasks_data = [
            (10, 1, "task", "backlog", 1024, 3, '{"title": "Task 10"}'),
            (11, 1, "task", "in_progress", 2048, 5, '{"title": "Task 11"}'),
            (12, 1, "task", "review", 3072, 7, '{"title": "Task 12"}'),
            (13, 1, "task", "done", 4096, 9, '{"title": "Task 13"}'),
            (20, 2, "task", "backlog", 1024, 2, '{"title": "Task 20"}'),
            (21, 2, "task", "done", 2048, 4, '{"title": "Task 21"}'),
        ]
        conn.executemany(
            "INSERT INTO tasks(id, project_id, kind, status, position, version, data) VALUES (?,?,?,?,?,?,?)",
            tasks_data,
        )
        conn.commit()
        conn.close()

        # Instantiate Store to trigger migration 5
        mig_store = Store(mig_dir)
        with mig_store.connection() as c:
            v = c.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(v, SCHEMA_VERSION)

            # Check default 4 columns created per project
            p1_cols = c.execute(
                "SELECT id, name, allowed_phases_json FROM board_columns WHERE project_id=1 ORDER BY sort_key"
            ).fetchall()
            self.assertEqual(len(p1_cols), 4)
            names = [r["name"] for r in p1_cols]
            self.assertEqual(names, ["Backlog", "In progress", "Review", "Done"])

            p2_cols = c.execute(
                "SELECT id, name FROM board_columns WHERE project_id=2 ORDER BY sort_key"
            ).fetchall()
            self.assertEqual(len(p2_cols), 4)

            # Check phase defaults
            defaults = c.execute(
                "SELECT phase, column_id FROM board_phase_defaults WHERE project_id=1"
            ).fetchall()
            self.assertEqual(len(defaults), 4)

            # Check tasks backfilled with correct column_id while preserving id and version
            for tid, pid, _, status, _, orig_ver, _ in tasks_data:
                row = c.execute(
                    "SELECT id, version, column_id, status FROM tasks WHERE id=?",
                    (tid,),
                ).fetchone()
                self.assertEqual(row["version"], orig_ver)
                col_row = c.execute(
                    "SELECT name, allowed_phases_json FROM board_columns WHERE id=?",
                    (row["column_id"],),
                ).fetchone()
                self.assertIn(status, json.loads(col_row["allowed_phases_json"]))

        # Re-instantiating Store must be idempotent and create no duplicate columns
        mig_store_rerun = Store(mig_dir)
        with mig_store_rerun.connection() as c:
            col_count = c.execute("SELECT COUNT(*) FROM board_columns").fetchone()[0]
            self.assertEqual(col_count, 8)
            def_count = c.execute(
                "SELECT COUNT(*) FROM board_phase_defaults"
            ).fetchone()[0]
            self.assertEqual(def_count, 8)

    # 2. Access control and external view masking
    def test_access_roles_and_external_masking(self):
        admin_svc = Service(self.store, access=Access(identity("admin")))
        regular_svc = Service(self.store, access=Access(identity("regular")))
        external_svc = Service(
            self.store, access=Access(identity("regular", kind="external"))
        )

        with self.store.connection(write=True) as c:
            c.execute(
                "UPDATE project_access SET customer_id='cust-test' WHERE project_id=?",
                (self.project["id"],),
            )
            c.execute(
                "INSERT INTO project_grants(project_id,membership_id,customer_id) VALUES(?,'m-regular-external','cust-test')",
                (self.project["id"],),
            )

        # Internal read: includes wip_limit, task_count, phase_defaults
        admin_view = self.read(
            f"projects/{self.project['id']}/columns", service=admin_svc
        )
        self.assertIn("columns", admin_view)
        self.assertIn("phase_defaults", admin_view)
        for col in admin_view["columns"]:
            self.assertIn("wip_limit", col)
            self.assertIn("task_count", col)

        # External read: masks wip_limit, task_count, and internal phase_defaults
        ext_view = self.read(
            f"projects/{self.project['id']}/columns", service=external_svc
        )
        self.assertIn("columns", ext_view)
        self.assertNotIn("phase_defaults", ext_view)
        for col in ext_view["columns"]:
            self.assertNotIn("wip_limit", col)
            self.assertNotIn("task_count", col)
            self.assertIn("name", col)
            self.assertIn("sort_key", col)

        # Regular user lacks workflow.manage -> PUT rejected with 403
        update_payload = {
            "expected_version": admin_view["version"],
            "columns": admin_view["columns"],
            "phase_defaults": admin_view["phase_defaults"],
        }
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                update_payload,
                method="PUT",
                service=regular_svc,
            )
        self.assertEqual(cm.exception.status, 403)

        # Admin / manager with workflow.manage succeeds
        res = self.write(
            f"projects/{self.project['id']}/columns",
            update_payload,
            method="PUT",
            service=admin_svc,
        )
        self.assertEqual(res["version"], admin_view["version"] + 1)

    # 3. Configuration validation rules & atomic rollback
    def test_configuration_validation_and_atomic_rollback(self):
        cur = self.read(f"projects/{self.project['id']}/columns")
        base_cols = cur["columns"]
        base_defs = cur["phase_defaults"]
        v = cur["version"]

        # Missing / stale expected_version
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {"columns": base_cols, "phase_defaults": base_defs},
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v + 99,
                    "columns": base_cols,
                    "phase_defaults": base_defs,
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 409)

        # Empty columns (<1)
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v,
                    "columns": [],
                    "phase_defaults": base_defs,
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        # More than 50 columns
        too_many = [
            {"name": f"Col {i}", "allowed_phases": ["backlog"], "sort_key": i}
            for i in range(51)
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v,
                    "columns": too_many,
                    "phase_defaults": base_defs,
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        # Duplicate column names (case-insensitive)
        dup_names = [
            {"name": "Backlog", "allowed_phases": ["backlog"], "sort_key": 1},
            {
                "name": "backlog",
                "allowed_phases": ["in_progress", "review", "done"],
                "sort_key": 2,
            },
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v,
                    "columns": dup_names,
                    "phase_defaults": {"backlog": "Backlog", "in_progress": "backlog"},
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        # Unreachable phase (missing 'done')
        unreachable = [
            {
                "name": "Todo",
                "allowed_phases": ["backlog", "in_progress", "review"],
                "sort_key": 1,
            }
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v,
                    "columns": unreachable,
                    "phase_defaults": {
                        "backlog": "Todo",
                        "in_progress": "Todo",
                        "review": "Todo",
                        "done": "Todo",
                    },
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        # Phase defaults mapping pointing to column that does not allow that phase
        mismatched_def = [
            {"name": "Todo", "allowed_phases": ["backlog"], "sort_key": 1},
            {
                "name": "Complete",
                "allowed_phases": ["in_progress", "review", "done"],
                "sort_key": 2,
            },
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": v,
                    "columns": mismatched_def,
                    "phase_defaults": {
                        "backlog": "Todo",
                        "in_progress": "Todo",  # Todo only allows backlog!
                        "review": "Complete",
                        "done": "Complete",
                    },
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

        # Verify atomic rollback: columns and version are completely untouched
        after_failed = self.read(f"projects/{self.project['id']}/columns")
        self.assertEqual(after_failed["version"], v)
        self.assertEqual(len(after_failed["columns"]), len(base_cols))

    # 4. Valid board configurations: 1-column, 2-column, and 7-column boards
    def test_valid_configurations_1_2_and_7_columns(self):
        # --- 1-Column Board ---
        cur = self.read(f"projects/{self.project['id']}/columns")
        one_col_cfg = {
            "expected_version": cur["version"],
            "columns": [
                {
                    "name": "Everything",
                    "allowed_phases": ["backlog", "in_progress", "review", "done"],
                    "sort_key": 100,
                }
            ],
            "phase_defaults": {
                "backlog": "Everything",
                "in_progress": "Everything",
                "review": "Everything",
                "done": "Everything",
            },
            "retirements": {col["id"]: "Everything" for col in cur["columns"]},
        }
        res1 = self.write(
            f"projects/{self.project['id']}/columns", one_col_cfg, method="PUT"
        )
        self.assertEqual(len(res1["columns"]), 1)
        self.assertEqual(res1["columns"][0]["name"], "Everything")
        self.assertEqual(
            set(res1["columns"][0]["allowed_phases"]),
            {"backlog", "in_progress", "review", "done"},
        )
        everything_id = res1["columns"][0]["id"]

        # --- 2-Column Board ---
        two_col_cfg = {
            "expected_version": res1["version"],
            "columns": [
                {"name": "To Do", "allowed_phases": ["backlog"], "sort_key": 10},
                {
                    "name": "Doing & Done",
                    "allowed_phases": ["in_progress", "review", "done"],
                    "sort_key": 20,
                },
            ],
            "phase_defaults": {
                "backlog": "To Do",
                "in_progress": "Doing & Done",
                "review": "Doing & Done",
                "done": "Doing & Done",
            },
            "retirements": {everything_id: "To Do"},
        }
        res2 = self.write(
            f"projects/{self.project['id']}/columns", two_col_cfg, method="PUT"
        )
        self.assertEqual(len(res2["columns"]), 2)
        names2 = [c["name"] for c in res2["columns"]]
        self.assertEqual(names2, ["To Do", "Doing & Done"])

        # --- 7-Column Board ---
        col_specs = [
            ("Ideas", ["backlog"], 10),
            ("Ready", ["backlog"], 20),
            ("Building", ["in_progress"], 30),
            ("Testing", ["in_progress"], 40),
            ("Waiting Review", ["review"], 50),
            ("Customer Review", ["review"], 60),
            ("Shipped", ["done"], 70),
        ]
        seven_cols = [
            {"name": name, "allowed_phases": phases, "sort_key": sk}
            for name, phases, sk in col_specs
        ]
        seven_col_cfg = {
            "expected_version": res2["version"],
            "columns": seven_cols,
            "phase_defaults": {
                "backlog": "Ready",
                "in_progress": "Building",
                "review": "Waiting Review",
                "done": "Shipped",
            },
            "retirements": {
                res2["columns"][0]["id"]: "Ready",
                res2["columns"][1]["id"]: "Building",
            },
        }
        res7 = self.write(
            f"projects/{self.project['id']}/columns", seven_col_cfg, method="PUT"
        )
        self.assertEqual(len(res7["columns"]), 7)
        names7 = [c["name"] for c in res7["columns"]]
        self.assertEqual(
            names7,
            [
                "Ideas",
                "Ready",
                "Building",
                "Testing",
                "Waiting Review",
                "Customer Review",
                "Shipped",
            ],
        )

        # Check emitted board event
        events = self.read(
            "events", {"source": self.s.instance_id, "project_id": self.project["id"]}
        )["items"]
        board_events = [e for e in events if e["operation"] == "board.columns_changed"]
        self.assertEqual(len(board_events), 3)

    # 5. Retiring occupied column: unmapped retirement fails, valid same-phase succeeds
    def test_occupied_column_retirement_and_task_migration(self):
        t1 = self.task(title="Task in Backlog")
        cur = self.read(f"projects/{self.project['id']}/columns")
        backlog_col = next(c for c in cur["columns"] if c["name"] == "Backlog")
        self.assertEqual(t1["column_id"], backlog_col["id"])

        # Attempt to remove Backlog column without retirement mapping -> fails 422
        cols_without_backlog = [
            c for c in cur["columns"] if c["id"] != backlog_col["id"]
        ]
        # Add backlog phase to In progress to satisfy reachability
        for c in cols_without_backlog:
            if c["name"] == "In progress":
                c["allowed_phases"] = ["backlog", "in_progress"]
        phase_defaults = dict(cur["phase_defaults"])
        phase_defaults["backlog"] = cols_without_backlog[0]["id"]

        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": cur["version"],
                    "columns": cols_without_backlog,
                    "phase_defaults": phase_defaults,
                    # No retirements provided
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual(
            cm.exception.payload["error"]["code"], "occupied_column_retirement"
        )

        # Provide valid retirement mapping to In progress (which allows backlog)
        inp_col = next(c for c in cols_without_backlog if c["name"] == "In progress")
        update_with_retire = {
            "expected_version": cur["version"],
            "columns": cols_without_backlog,
            "phase_defaults": phase_defaults,
            "retirements": {backlog_col["id"]: inp_col["id"]},
        }
        res = self.write(
            f"projects/{self.project['id']}/columns",
            update_with_retire,
            method="PUT",
        )
        self.assertEqual(len(res["columns"]), 3)

        # Verify task migrated to In progress column atomically and emitted task.moved
        t1_after = self.read(f"tasks/{t1['id']}")
        self.assertEqual(t1_after["column_id"], inp_col["id"])
        self.assertEqual(t1_after["status"], "backlog")

        events = self.read(
            "events", {"source": self.s.instance_id, "project_id": self.project["id"]}
        )["items"]
        move_events = [
            e
            for e in events
            if e["operation"] == "task.moved" and e["entity_id"] == t1["id"]
        ]
        self.assertTrue(move_events)
        detail = move_events[-1]["detail"]
        detail_dict = json.loads(detail) if isinstance(detail, str) else detail
        self.assertEqual(detail_dict["after_column_id"], inp_col["id"])

    # 6. Retiring occupied column cross-phase fails pre-commit if tasks cannot transition
    def test_cross_phase_retirement_blockers_reject_change(self):
        t1 = self.task(title="Unfinished task")
        cur = self.read(f"projects/{self.project['id']}/columns")
        backlog_col = next(c for c in cur["columns"] if c["name"] == "Backlog")

        # All 4 phases remain reachable on the new board, but retirement sends backlog cards to Done
        cols = [
            {
                "name": "Active Work",
                "allowed_phases": ["backlog", "in_progress", "review"],
                "sort_key": 10,
            },
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 20},
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": cur["version"],
                    "columns": cols,
                    "phase_defaults": {
                        "backlog": "Active Work",
                        "in_progress": "Active Work",
                        "review": "Active Work",
                        "done": "Done",
                    },
                    "retirements": {backlog_col["id"]: "Done"},
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)
        payload = cm.exception.payload["error"]
        blockers = payload.get("blockers") or payload.get("fields", {}).get("blockers")
        self.assertTrue(blockers)

        # Board and task remain completely unchanged
        t1_check = self.read(f"tasks/{t1['id']}")
        self.assertEqual(t1_check["column_id"], backlog_col["id"])
        cols_check = self.read(f"projects/{self.project['id']}/columns")
        self.assertEqual(cols_check["version"], cur["version"])

    # 7. WIP limits: ordinary user blocked, manager override required, done phase exempt
    def test_wip_limit_and_manager_override(self):
        admin_svc = Service(self.store, access=Access(identity("admin")))
        regular_svc = Service(self.store, access=Access(identity("regular")))

        # Set WIP limit of 1 on 'In progress' column and 'Done' column
        cur = self.read(f"projects/{self.project['id']}/columns", service=admin_svc)
        updated_cols = []
        for c in cur["columns"]:
            col = dict(c)
            if col["name"] in {"In progress", "Done"}:
                col["wip_limit"] = 1
            updated_cols.append(col)

        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": updated_cols,
                "phase_defaults": cur["phase_defaults"],
            },
            method="PUT",
            service=admin_svc,
        )

        inp_col = next(c for c in updated_cols if c["name"] == "In progress")
        done_col = next(c for c in updated_cols if c["name"] == "Done")

        # Task 1 moves into 'In progress' via claim
        t1 = self.task(title="First active task")
        t1 = self.action(t1, "claim", actor="dev1", session="s1")
        self.assertEqual(t1["column_id"], inp_col["id"])

        # Task 2 created in backlog with assignee
        t2 = self.task(title="Second active task", assignee="dev2")

        # Regular user tries to move t2 into full In progress column -> 409 wip_limit_exceeded
        with self.assertRaises(Error) as cm:
            self.action(
                t2,
                "move",
                column_id=inp_col["id"],
                phase="in_progress",
                actor="dev2",
                service=regular_svc,
            )
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_limit_exceeded")

        # Manager tries to move without override reason -> 409 wip_override_required
        with self.assertRaises(Error) as cm:
            self.action(
                t2,
                "move",
                column_id=inp_col["id"],
                phase="in_progress",
                actor="mgr",
                service=admin_svc,
            )
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_override_required")

        # Manager provides wip_override_reason -> succeeds!
        t2_moved = self.action(
            t2,
            "move",
            column_id=inp_col["id"],
            phase="in_progress",
            wip_override_reason="Production emergency incident",
            actor="mgr",
            service=admin_svc,
        )
        self.assertEqual(t2_moved["column_id"], inp_col["id"])

        # Completing to 'Done' is never blocked by WIP limits
        # Move t1 through review to done
        t1 = self.action(t1, "submit", actor="dev1", session="s1", result=RESULT)
        t1 = self.action(
            t1,
            "complete",
            actor="mgr",
            acceptance_note="All criteria satisfied.",
            service=admin_svc,
        )
        self.assertEqual(t1["column_id"], done_col["id"])

        # Second task moves to Done even though Done WIP limit is 1
        t2_submitted = self.action(
            t2_moved,
            "submit",
            actor="dev2",
            result=RESULT,
            service=admin_svc,
        )
        t2_done = self.action(
            t2_submitted,
            "complete",
            actor="mgr",
            acceptance_note="Also approved.",
            service=admin_svc,
        )
        self.assertEqual(t2_done["column_id"], done_col["id"])

    # 8. Same-phase movement preserves claim and session; cross-phase enforces workflow
    def test_same_phase_claim_preservation_and_cross_phase_enforcement(self):
        # Configure 2 columns for 'in_progress': 'Active Dev' and 'Bugfixing'
        cur = self.read(f"projects/{self.project['id']}/columns")
        cols = [
            {"name": "Backlog", "allowed_phases": ["backlog"], "sort_key": 10},
            {
                "name": "Active Dev",
                "allowed_phases": ["in_progress"],
                "sort_key": 20,
            },
            {"name": "Bugfixing", "allowed_phases": ["in_progress"], "sort_key": 30},
            {"name": "Review", "allowed_phases": ["review"], "sort_key": 40},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 50},
        ]
        res = self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": {
                    "backlog": "Backlog",
                    "in_progress": "Active Dev",
                    "review": "Review",
                    "done": "Done",
                },
                "retirements": {c["id"]: "Active Dev" for c in cur["columns"]},
            },
            method="PUT",
        )
        active_col = next(c for c in res["columns"] if c["name"] == "Active Dev")
        bug_col = next(c for c in res["columns"] if c["name"] == "Bugfixing")

        # Claim a task into Active Dev
        t = self.task(title="Claimed task")
        t = self.action(t, "claim", actor="worker-1", session="session-abc")
        self.assertEqual(t["column_id"], active_col["id"])
        self.assertEqual(t["assignee"], "worker-1")
        self.assertEqual(t["execution"]["session"], "session-abc")

        # Move task to Bugfixing (same phase: in_progress -> in_progress)
        moved = self.action(
            t,
            "move",
            column_id=bug_col["id"],
            actor="worker-1",
            session="session-abc",
        )
        self.assertEqual(moved["column_id"], bug_col["id"])
        self.assertEqual(moved["status"], "in_progress")
        self.assertEqual(moved["assignee"], "worker-1")
        # Claim and session are strictly preserved!
        self.assertIsNotNone(moved["execution"])
        self.assertEqual(moved["execution"]["session"], "session-abc")

        # Cross-phase move: attempt to move directly to 'done' without evidence/review
        done_col = next(c for c in res["columns"] if c["name"] == "Done")
        with self.assertRaises(Error) as cm:
            self.action(
                moved,
                "move",
                column_id=done_col["id"],
                phase="done",
                actor="worker-1",
            )
        # Rejected because task cannot transition directly to done without review
        self.assertIn(cm.exception.status, (400, 403, 422))

    # 9. API v1 status compatibility: moves to status choose phase default column
    def test_v1_status_moves_route_to_configured_phase_default(self):
        cur = self.read(f"projects/{self.project['id']}/columns")
        cols = [
            {"name": "Ideas", "allowed_phases": ["backlog"], "sort_key": 10},
            {"name": "Ready", "allowed_phases": ["backlog"], "sort_key": 20},
            {
                "name": "Doing",
                "allowed_phases": ["in_progress"],
                "sort_key": 30,
            },
            {"name": "Review", "allowed_phases": ["review"], "sort_key": 40},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 50},
        ]
        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": {
                    "backlog": "Ready",  # Default for backlog is Ready, not Ideas!
                    "in_progress": "Doing",
                    "review": "Review",
                    "done": "Done",
                },
                "retirements": {c["id"]: "Ready" for c in cur["columns"]},
            },
            method="PUT",
        )
        cols_now = self.read(f"projects/{self.project['id']}/columns")["columns"]
        ready_col = next(c for c in cols_now if c["name"] == "Ready")
        doing_col = next(c for c in cols_now if c["name"] == "Doing")

        # Creating a task without specifying column_id lands in phase default (Ready)
        new_task = self.task(title="Brand new item")
        self.assertEqual(new_task["column_id"], ready_col["id"])

        # v1 move using status="in_progress" without column_id
        t_claimed = self.action(new_task, "claim", actor="agent", session="s1")
        self.assertEqual(t_claimed["column_id"], doing_col["id"])

    # 10. Reordering within column: invalid/missing/archived/wrong-project anchors fail safely
    def test_invalid_and_ambiguous_anchors_fail_without_mutation(self):
        t1 = self.task(title="Task 1")
        t2 = self.task(title="Task 2")
        t3 = self.task(title="Task 3")
        orig_t3_pos = t3["position"]
        orig_t3_ver = t3["version"]

        # Missing / deleted anchor (404)
        with self.assertRaises(Error) as cm:
            self.action(t3, "move", column_id=t3["column_id"], before_id=999999)
        self.assertEqual(cm.exception.status, 404)

        with self.assertRaises(Error) as cm:
            self.action(t3, "move", column_id=t3["column_id"], after_id=999999)
        self.assertEqual(cm.exception.status, 404)

        # Wrong project anchor (422)
        p2 = self.write("projects", {"key": "OTH", "name": "Other Project"})
        t_other = self.write("tasks", {"project_id": p2["id"], "title": "Other task"})
        with self.assertRaises(Error) as cm:
            self.action(t3, "move", column_id=t3["column_id"], before_id=t_other["id"])
        self.assertEqual(cm.exception.status, 422)

        # Wrong column anchor (422)
        cols_now = self.read(f"projects/{self.project['id']}/columns")["columns"]
        _inp_col = next(c for c in cols_now if c["name"] == "In progress")
        t_inp = self.task(title="In progress task")
        t_inp = self.action(t_inp, "claim", session="s-1")
        with self.assertRaises(Error) as cm:
            self.action(t3, "move", column_id=t3["column_id"], before_id=t_inp["id"])
        self.assertEqual(cm.exception.status, 422)

        # Archived anchor (422)
        t_archived = self.task(title="Archived task")
        self.action(t_archived, "archive", reason="done testing")
        with self.assertRaises(Error) as cm:
            self.action(
                t3, "move", column_id=t3["column_id"], before_id=t_archived["id"]
            )
        self.assertEqual(cm.exception.status, 422)

        # Self as anchor (422)
        with self.assertRaises(Error) as cm:
            self.action(t3, "move", column_id=t3["column_id"], before_id=t3["id"])
        self.assertEqual(cm.exception.status, 422)

        # Ambiguous / conflicting anchors (422)
        # t1, t2 are in order. before_id=t1 and after_id=t2 is backwards/impossible
        with self.assertRaises(Error) as cm:
            self.action(
                t3,
                "move",
                column_id=t3["column_id"],
                before_id=t1["id"],
                after_id=t2["id"],
            )
        self.assertEqual(cm.exception.status, 422)

        # Same task as both anchors (422)
        with self.assertRaises(Error) as cm:
            self.action(
                t3,
                "move",
                column_id=t3["column_id"],
                before_id=t1["id"],
                after_id=t1["id"],
            )
        self.assertEqual(cm.exception.status, 422)

        # Verify NO mutation occurred on t3
        t3_check = self.read(f"tasks/{t3['id']}")
        self.assertEqual(t3_check["position"], orig_t3_pos)
        self.assertEqual(t3_check["version"], orig_t3_ver)

    # 11. Task list filters by column_id and phase with stable order
    def test_task_list_filters_and_ordering(self):
        t1 = self.task(title="Task 1")
        t2 = self.task(title="Task 2")
        t2 = self.action(t2, "claim", session="s")

        # Filter by column_id
        t1_col_list = self.read(
            "tasks",
            {
                "project_id": self.project["id"],
                "column_id": t1["column_id"],
            },
        )["items"]
        ids = [t["id"] for t in t1_col_list]
        self.assertIn(t1["id"], ids)
        self.assertNotIn(t2["id"], ids)

        # Filter by phase
        inp_list = self.read(
            "tasks",
            {
                "project_id": self.project["id"],
                "phase": "in_progress",
            },
        )["items"]
        inp_ids = [t["id"] for t in inp_list]
        self.assertIn(t2["id"], inp_ids)
        self.assertNotIn(t1["id"], inp_ids)

    # 12. CLI integration smoke test
    def test_cli_columns_and_move(self):
        env = {
            **dict(os.environ),
            "TT_DATA_DIR": str(self.directory),
        }

        # tt project columns <id> --json
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "tasktrack",
                "project",
                "columns",
                str(self.project["id"]),
                "--json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        cols_data = json.loads(res.stdout)
        self.assertEqual(len(cols_data["columns"]), 4)

        # tt task list --column-id <id> --json
        col0_id = cols_data["columns"][0]["id"]
        res_list = subprocess.run(
            [
                sys.executable,
                "-m",
                "tasktrack",
                "task",
                "list",
                "--project",
                self.project["key"],
                "--column-id",
                str(col0_id),
                "--json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(res_list.returncode, 0, res_list.stderr)
        items = json.loads(res_list.stdout)["items"]
        for it in items:
            self.assertEqual(it["column_id"], col0_id)

    # 13. Direct repro: claim overflow rejection, ordinary user denied override, manager override succeeds
    def test_claim_wip_overflow_rejection_and_override(self):
        admin_svc = Service(self.store, access=Access(identity("admin")))
        regular_svc = Service(self.store, access=Access(identity("regular")))

        # Set WIP=1 on 'In progress' column
        cur = self.read(f"projects/{self.project['id']}/columns", service=admin_svc)
        cols = [dict(c) for c in cur["columns"]]
        for c in cols:
            if c["name"] == "In progress":
                c["wip_limit"] = 1
        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": cur["phase_defaults"],
            },
            method="PUT",
            service=admin_svc,
        )

        t1 = self.task(title="Claim Task 1")
        t2 = self.task(title="Claim Task 2")

        # First claim succeeds -> in_progress WIP count = 1
        t1 = self.action(t1, "claim", actor="dev1", session="s-1", service=regular_svc)
        self.assertEqual(t1["status"], "in_progress")

        # Second claim by normal user must reject atomically with 409 wip_limit_exceeded
        with self.assertRaises(Error) as cm:
            self.action(t2, "claim", actor="dev2", session="s-2", service=regular_svc)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_limit_exceeded")

        # Ordinary user passing wip_override_reason is also denied
        with self.assertRaises(Error) as cm:
            self.action(
                t2,
                "claim",
                actor="dev2",
                session="s-2",
                wip_override_reason="Need to work on this",
                service=regular_svc,
            )
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_limit_exceeded")

        # Task 2 remained in backlog untouched
        t2_check = self.read(f"tasks/{t2['id']}")
        self.assertEqual(t2_check["status"], "backlog")
        self.assertIsNone(t2_check["execution"])

        # Manager claim without override reason -> 409 wip_override_required
        with self.assertRaises(Error) as cm:
            self.action(t2, "claim", actor="mgr", session="s-mgr", service=admin_svc)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_override_required")

        # Manager claim with valid wip_override_reason -> succeeds!
        t2_claimed = self.action(
            t2,
            "claim",
            actor="mgr",
            session="s-mgr",
            wip_override_reason="P0 escalation override",
            service=admin_svc,
        )
        self.assertEqual(t2_claimed["status"], "in_progress")
        self.assertIsNotNone(t2_claimed["execution"])

    # 14. Direct repro: create and restore WIP overflow
    def test_create_and_restore_wip_overflow(self):
        admin_svc = Service(self.store, access=Access(identity("admin")))
        regular_svc = Service(self.store, access=Access(identity("regular")))

        # Set WIP limit 1 on Backlog column
        cur = self.read(f"projects/{self.project['id']}/columns", service=admin_svc)
        cols = [dict(c) for c in cur["columns"]]
        for c in cols:
            if c["name"] == "Backlog":
                c["wip_limit"] = 1
        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": cur["phase_defaults"],
            },
            method="PUT",
            service=admin_svc,
        )

        # First create succeeds
        t1 = self.task(title="Backlog task 1")
        self.assertEqual(t1["status"], "backlog")

        # Second create by regular user rejected (Backlog full)
        with self.assertRaises(Error) as cm:
            self.write(
                "tasks",
                {
                    "project_id": self.project["id"],
                    "title": "Backlog task 2",
                    "description_markdown": "desc",
                    "acceptance_criteria": ["crit"],
                },
                service=regular_svc,
            )
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_limit_exceeded")

        # Manager create with override succeeds
        t2 = self.write(
            "tasks",
            {
                "project_id": self.project["id"],
                "title": "Backlog task 2",
                "description_markdown": "desc",
                "acceptance_criteria": ["crit"],
                "wip_override_reason": "Executive priority",
            },
            service=admin_svc,
        )
        self.assertEqual(t2["status"], "backlog")

        # Archive t1; now 1 active task in Backlog (t2)
        t1 = self.action(t1, "archive", reason="cleaning up")

        # Regular user restoring t1 rejected because Backlog has 1 active task (WIP=1)
        with self.assertRaises(Error) as cm:
            self.action(t1, "restore", reason="need it back", service=regular_svc)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.payload["error"]["code"], "wip_limit_exceeded")

        # Manager restore with wip_override_reason succeeds
        t1_restored = self.action(
            t1,
            "restore",
            reason="need it back",
            wip_override_reason="Manager approved restore",
            service=admin_svc,
        )
        self.assertIsNone(t1_restored["archived_at"])

    # 15. Direct repro: retained column phase change rejects on occupant mismatch
    def test_retained_column_phase_change_rejects_occupant_mismatch(self):
        # Create a column allowing backlog and in_progress
        cur = self.read(f"projects/{self.project['id']}/columns")
        cols = [
            {
                "name": "Unified",
                "allowed_phases": ["backlog", "in_progress"],
                "sort_key": 10,
            },
            {"name": "Review", "allowed_phases": ["review"], "sort_key": 20},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 30},
        ]
        res = self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": {
                    "backlog": "Unified",
                    "in_progress": "Unified",
                    "review": "Review",
                    "done": "Done",
                },
                "retirements": {c["id"]: "Unified" for c in cur["columns"]},
            },
            method="PUT",
        )
        unified_col = next(c for c in res["columns"] if c["name"] == "Unified")

        # Create and claim a task in Unified -> status='in_progress'
        t = self.task(title="Working item")
        t = self.action(t, "claim", session="s-claim")
        self.assertEqual(t["column_id"], unified_col["id"])
        self.assertEqual(t["status"], "in_progress")

        # Try to retain Unified column but change its allowed_phases to only ['backlog']
        # Active task 't' would now be invalid! Must reject with 422 before writing.
        cur_v2 = self.read(f"projects/{self.project['id']}/columns")
        cols_bad = [
            {
                "id": unified_col["id"],
                "name": "Unified",
                "allowed_phases": ["backlog"],
                "sort_key": 10,
            },
            {
                "name": "Doing",
                "allowed_phases": ["in_progress"],
                "sort_key": 15,
            },
            {
                "id": next(c["id"] for c in cur_v2["columns"] if c["name"] == "Review"),
                "name": "Review",
                "allowed_phases": ["review"],
                "sort_key": 20,
            },
            {
                "id": next(c["id"] for c in cur_v2["columns"] if c["name"] == "Done"),
                "name": "Done",
                "allowed_phases": ["done"],
                "sort_key": 30,
            },
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": cur_v2["version"],
                    "columns": cols_bad,
                    "phase_defaults": {
                        "backlog": "Unified",
                        "in_progress": "Doing",
                        "review": "Review",
                        "done": "Done",
                    },
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual(cm.exception.payload["error"]["code"], "invalid_column_phases")

        # Board version unchanged
        self.assertEqual(
            self.read(f"projects/{self.project['id']}/columns")["version"],
            cur_v2["version"],
        )

    # 16. Direct repro: restore from retired column relocates to active compatible default
    def test_restore_from_retired_column_relocates_to_active_default(self):
        # Create and archive a task in Backlog
        t = self.task(title="Archived item")
        t_arch = self.action(t, "archive", reason="archiving for now")
        old_col_id = t_arch["column_id"]

        # Reconfigure board: retire old columns, new columns 'Todo', 'Work', 'QA', 'Done'
        cur = self.read(f"projects/{self.project['id']}/columns")
        cols = [
            {"name": "Todo", "allowed_phases": ["backlog"], "sort_key": 10},
            {"name": "Work", "allowed_phases": ["in_progress"], "sort_key": 20},
            {"name": "QA", "allowed_phases": ["review"], "sort_key": 30},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 40},
        ]
        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": {
                    "backlog": "Todo",
                    "in_progress": "Work",
                    "review": "QA",
                    "done": "Done",
                },
                "retirements": {c["id"]: "Todo" for c in cur["columns"]},
            },
            method="PUT",
        )
        cols_now = self.read(f"projects/{self.project['id']}/columns")["columns"]
        todo_col = next(c for c in cols_now if c["name"] == "Todo")

        # Restore task whose previous column is retired: relocates to Todo (active default for backlog)
        restored = self.action(t_arch, "restore", reason="unarchive")
        self.assertEqual(restored["column_id"], todo_col["id"])
        self.assertNotEqual(restored["column_id"], old_col_id)
        self.assertIsNone(restored["archived_at"])

    # 17. Direct repro: duplicate column IDs in configuration rejected atomically
    def test_duplicate_column_ids_reject(self):
        cur = self.read(f"projects/{self.project['id']}/columns")
        first_id = cur["columns"][0]["id"]
        dup_cols = [
            {
                "id": first_id,
                "name": "Column One",
                "allowed_phases": ["backlog"],
                "sort_key": 10,
            },
            {
                "id": first_id,
                "name": "Column Two",
                "allowed_phases": ["in_progress"],
                "sort_key": 20,
            },
            {"name": "Review", "allowed_phases": ["review"], "sort_key": 30},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 40},
        ]
        with self.assertRaises(Error) as cm:
            self.write(
                f"projects/{self.project['id']}/columns",
                {
                    "expected_version": cur["version"],
                    "columns": dup_cols,
                    "phase_defaults": {
                        "backlog": "Column One",
                        "in_progress": "Column Two",
                        "review": "Review",
                        "done": "Done",
                    },
                },
                method="PUT",
            )
        self.assertEqual(cm.exception.status, 422)

    # 18. Direct repro: bulk retirement preserves execution claims and does not manufacture acceptance
    def test_bulk_retirement_preserves_execution_claim(self):
        # Configure two in_progress columns: Dev1 and Dev2
        cur = self.read(f"projects/{self.project['id']}/columns")
        cols = [
            {"name": "Backlog", "allowed_phases": ["backlog"], "sort_key": 10},
            {"name": "Dev1", "allowed_phases": ["in_progress"], "sort_key": 20},
            {"name": "Dev2", "allowed_phases": ["in_progress"], "sort_key": 30},
            {"name": "Review", "allowed_phases": ["review"], "sort_key": 40},
            {"name": "Done", "allowed_phases": ["done"], "sort_key": 50},
        ]
        res = self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur["version"],
                "columns": cols,
                "phase_defaults": {
                    "backlog": "Backlog",
                    "in_progress": "Dev1",
                    "review": "Review",
                    "done": "Done",
                },
                "retirements": {c["id"]: "Backlog" for c in cur["columns"]},
            },
            method="PUT",
        )
        dev1_col = next(c for c in res["columns"] if c["name"] == "Dev1")
        dev2_col = next(c for c in res["columns"] if c["name"] == "Dev2")

        # Claim a task in Dev1
        t = self.task(title="Claimed task in Dev1")
        t = self.action(t, "claim", actor="worker-1", session="session-w1")
        self.assertEqual(t["column_id"], dev1_col["id"])
        self.assertIsNotNone(t["execution"])

        # Retire Dev1 and migrate to Dev2 (same phase: in_progress -> in_progress)
        cur_v2 = self.read(f"projects/{self.project['id']}/columns")
        cols_no_dev1 = [c for c in cur_v2["columns"] if c["name"] != "Dev1"]
        self.write(
            f"projects/{self.project['id']}/columns",
            {
                "expected_version": cur_v2["version"],
                "columns": cols_no_dev1,
                "phase_defaults": {
                    "backlog": "Backlog",
                    "in_progress": "Dev2",
                    "review": "Review",
                    "done": "Done",
                },
                "retirements": {dev1_col["id"]: "Dev2"},
            },
            method="PUT",
        )

        # Migrated task is now in Dev2, still in_progress, with execution claim strictly intact
        t_after = self.read(f"tasks/{t['id']}")
        self.assertEqual(t_after["column_id"], dev2_col["id"])
        self.assertEqual(t_after["status"], "in_progress")
        self.assertIsNotNone(t_after["execution"])
        self.assertEqual(t_after["execution"]["actor"], "worker-1")
        self.assertEqual(t_after["execution"]["session"], "session-w1")
        self.assertIsNone(t_after.get("completion"))

    def test_revoked_workflow_permission_cannot_replay_configuration_receipt(self):
        admin = Service(self.store, access=Access(identity("admin")))
        regular = Service(self.store, access=Access(identity("regular")))
        path = f"projects/{self.project['id']}/columns"
        current = self.read(path, service=admin)
        body = {"expected_version": current["version"], "columns": current["columns"]}
        self.write(path, body, method="PUT", key="config-replay", service=admin)
        with self.store.connection(write=True) as c:
            c.execute(
                "INSERT INTO project_grants(project_id,membership_id,capabilities) VALUES(?,?,'[\"project.settings\"]')",
                (self.project["id"], "m-regular-internal"),
            )
        with self.assertRaises(Error) as denied:
            self.write(path, body, method="PUT", key="config-replay", service=regular)
        self.assertEqual(denied.exception.status, 403)

    def test_malformed_override_and_conflicting_phase_fields_do_not_mutate(self):
        task = self.task()
        for fields in (
            {"wip_override_reason": True},
            {"status": "backlog", "phase": "done"},
        ):
            with self.subTest(fields=fields), self.assertRaises(Error) as denied:
                self.write(
                    f"tasks/{task['id']}/move",
                    {"expected_version": task["version"], **fields},
                )
            self.assertEqual(denied.exception.status, 422)
        self.assertEqual(self.read(f"tasks/{task['id']}")["version"], task["version"])


if __name__ == "__main__":
    unittest.main()
