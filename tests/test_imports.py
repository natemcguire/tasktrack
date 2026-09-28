import hashlib
import tempfile
import unittest
import uuid

from tasktrack.db import Error, Store
from tasktrack.imports import validate
from tasktrack.service import Service


class ImportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Service(Store(tmp.name))
        self.p = self.write("/projects", {"key": "IMPORT", "name": "Imported work"})

    def write(self, path, body, method="POST"):
        return self.s.mutate(
            method, "/api/v1" + path, body, "importer", str(uuid.uuid4())
        )[1]

    def dataset(self, records=None):
        return {
            "schema_version": 1,
            "provider": "jira",
            "account_id": "site",
            "source_project": "SAIL",
            "complete": True,
            "records": records
            or [
                {
                    "id": "SAIL-1",
                    "title": "Released empty epic",
                    "type": "Epic",
                    "status": "Released",
                    "phase": "done",
                    "created_at": "2020-01-02T03:04:05Z",
                    "history": [{"event": "original"}],
                    "comments": [
                        {
                            "id": "c1",
                            "body": "Source comment",
                            "author": "Alex",
                            "created_at": "2020-01-02T03:05:00Z",
                        }
                    ],
                }
            ],
        }

    def preview(self, data):
        return self.write(
            "/import-jobs",
            {
                "project_id": self.p["id"],
                "dataset": data,
                "allow_visibility_change": True,
            },
        )

    def commit(self, j):
        return self.write(
            "/import-jobs/" + j["id"] + "/commit", {"digest": j["digest"]}
        )

    def test_history_completed_empty_epic_and_retry_no_duplicates(self):
        j = self.preview(self.dataset())
        r = self.commit(j)
        self.assertEqual(r["state"], "complete")
        self.assertEqual(r["report"]["created"], 1)
        replay = self.commit(self.preview(self.dataset()))
        self.assertEqual(replay["report"]["unchanged"], 1)
        task = self.s.read("/api/v1/tasks")["items"][0]
        self.assertEqual(task["kind"], "epic")
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["completion"])
        self.assertEqual(task["created_at"], "2020-01-02T03:04:05Z")
        self.assertEqual(task["import_source"]["history"], [{"event": "original"}])
        with self.s.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(
                c.execute("SELECT count(*) FROM comments").fetchone()[0], 1
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM events WHERE operation='complete'"
                ).fetchone()[0],
                0,
            )

    def test_source_changes_never_overwrite_user_edits(self):
        self.commit(self.preview(self.dataset()))
        t = self.s.read("/api/v1/tasks")["items"][0]
        self.write(
            "/tasks/" + str(t["id"]),
            {"expected_version": 1, "title": "Edited by owner"},
            "PATCH",
        )
        changed = self.dataset()
        changed["records"][0]["title"] = "New source title"
        result = self.commit(self.preview(changed))
        self.assertEqual(result["state"], "partial")
        self.assertEqual(len(result["report"]["conflicts"]), 1)
        updated = self.s.read("/api/v1/tasks/" + str(t["id"]))
        self.assertEqual(updated["title"], "Edited by owner")
        self.assertIn("import_source", updated)

    def test_missing_hash_and_privacy_ack_block_before_mutation(self):
        with self.assertRaises(Error):
            self.write(
                "/import-jobs", {"project_id": self.p["id"], "dataset": self.dataset()}
            )
        data = self.dataset()
        data["records"][0]["attachments"] = [{"id": "f", "filename": "a", "size": 1}]
        with self.assertRaises(Error):
            self.preview(data)

    def test_files_require_verified_manifest_before_commit(self):
        content = b"hello"
        sha = hashlib.sha256(content).hexdigest()
        data = self.dataset()
        data["records"][0]["attachments"] = [
            {"id": "f", "filename": "hello.txt", "size": 5, "sha256": sha}
        ]
        j = self.preview(data)
        with self.assertRaises(Error):
            self.commit(j)
        self.s.store.put_blob(content)
        self.write("/import-jobs/" + j["id"] + "/blob", {"sha256": sha, "size": 5})
        r = self.commit(j)
        self.assertEqual(r["report"]["attachments"], 1)
        with self.s.store.connection() as c:
            self.assertEqual(
                c.execute("SELECT sha256 FROM attachments").fetchone()[0], sha
            )

    def test_batch_resume_cancel_hierarchy_and_cycles(self):
        records = [
            {
                "id": str(i),
                "title": "Task " + str(i),
                "phase": "backlog",
                "status": "Todo",
            }
            for i in range(30)
        ]
        records[0]["parent_id"] = "29"
        records[29]["type"] = "Epic"
        j = self.preview(self.dataset(records))
        r = self.commit(j)
        self.assertEqual(r["cursor"], 25)
        self.write("/import-jobs/" + j["id"] + "/cancel", {"digest": j["digest"]})
        with self.assertRaises(Error):
            self.commit(j)
        r = self.write("/import-jobs/" + j["id"] + "/resume", {"digest": j["digest"]})
        self.assertEqual(r["report"]["created"], 30)
        records[29]["parent_id"] = "0"
        with self.assertRaises(Error):
            validate(self.dataset(records))

    def test_rollback_preserves_later_user_comments(self):
        j = self.preview(self.dataset())
        self.commit(j)
        tid = self.s.read("/api/v1/tasks")["items"][0]["id"]
        self.write(
            "/tasks/" + str(tid) + "/comments",
            {"body": "Owner added this after import"},
        )
        preview = self.s.read("/api/v1/import-jobs/" + j["id"] + "/rollback")
        self.assertEqual(preview["conflicts"], [tid])
        with self.assertRaises(Error):
            self.write(
                "/import-jobs/" + j["id"] + "/rollback",
                {"digest": j["digest"], "manifest_digest": preview["digest"]},
            )
        self.assertIsNone(self.s.read("/api/v1/tasks/" + str(tid))["archived_at"])

    def test_people_mapping_changes_preview_digest_and_preserves_assignment(self):
        data = self.dataset()
        data["records"][0]["source_assignees"] = [{"id": "source-user", "name": "Alex"}]
        j = self.preview(data)
        mapped = self.write(
            "/import-jobs/" + j["id"] + "/people",
            {"digest": j["digest"], "mapping": {"source-user": "alex@example.com"}},
        )
        self.assertNotEqual(mapped["digest"], j["digest"])
        with self.assertRaises(Error):
            self.commit(j)
        self.commit(mapped)
        task = self.s.read("/api/v1/tasks")["items"][0]
        self.assertEqual(task["assignee"], "alex@example.com")
        with self.assertRaises(Error):
            self.write(
                "/import-jobs/" + j["id"] + "/people",
                {"digest": mapped["digest"], "mapping": {}},
            )
