"""SQLite lifecycle, additive migrations and complete snapshot backups."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3


def now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Error(Exception):
    def __init__(self, status, code, message, fields=None, **extra):
        super().__init__(message)
        self.status = status
        self.payload = {
            "error": {"code": code, "message": message, "fields": fields or {}, **extra}
        }


MIGRATIONS = {
    1: [
        "CREATE TABLE instance (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)",
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, brief_markdown TEXT NOT NULL DEFAULT '', document_links TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL, created_by TEXT, created_via TEXT, created_at TEXT NOT NULL, updated_by TEXT, updated_via TEXT, updated_at TEXT NOT NULL)",
        "CREATE TABLE project_aliases (key TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id))",
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL CHECK(kind IN ('task','epic')), parent_id INTEGER REFERENCES tasks(id), status TEXT NOT NULL CHECK(status IN ('backlog','in_progress','review','done')), assignee TEXT, position INTEGER NOT NULL, archived_at TEXT, version INTEGER NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE dependencies (task_id INTEGER NOT NULL REFERENCES tasks(id), prerequisite_id INTEGER NOT NULL REFERENCES tasks(id), PRIMARY KEY(task_id, prerequisite_id), CHECK(task_id != prerequisite_id))",
        "CREATE TABLE thread_links (task_id INTEGER NOT NULL REFERENCES tasks(id), source TEXT NOT NULL, project TEXT NOT NULL, thread_id TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(task_id,source,project,thread_id))",
        "CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), actor TEXT NOT NULL, session TEXT, via TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id), comment_id INTEGER REFERENCES comments(id), sha256 TEXT NOT NULL, stored_name TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL, media_type TEXT NOT NULL, actor TEXT NOT NULL, session TEXT, via TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, project_id INTEGER NOT NULL REFERENCES projects(id), actor TEXT NOT NULL, session TEXT, via TEXT NOT NULL, operation TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL)",
    ],
    2: [
        "CREATE TABLE receipts (actor TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, status INTEGER NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(actor,request_key))",
        "CREATE INDEX tasks_board ON tasks(project_id,status,archived_at,position,id)",
        "CREATE INDEX tasks_owner ON tasks(assignee,status)",
        "CREATE INDEX task_events ON events(entity_type,entity_id,sequence)",
        "CREATE INDEX project_events ON events(project_id,sequence)",
    ],
    3: [
        "ALTER TABLE comments ADD COLUMN parent_id INTEGER REFERENCES comments(id)",
        "CREATE INDEX comments_thread ON comments(task_id,parent_id,id)",
    ],
}


class Store:
    def __init__(self, directory=None, timeout=2.0):
        self.directory = (
            Path(
                directory
                or os.environ.get("TT_DATA_DIR")
                or Path.home() / ".local/share/tasktrack"
            )
            .expanduser()
            .resolve()
        )
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "tasktrack.sqlite3"
        self.blobs = self.directory / "blobs"
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.timeout = timeout
        with self.connection() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise Error(
                    503,
                    "newer_schema",
                    f"Schema {version} requires a newer Tasktrack; this binary supports {SCHEMA_VERSION}.",
                )
            if (
                version == 0
                and c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone()
            ):
                raise Error(
                    422,
                    "legacy_schema",
                    "Unversioned database detected. Preserve it and use the documented isolated migration procedure; no data was changed.",
                )
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("BEGIN IMMEDIATE")
            # Re-read under the writer lock: another process may have migrated it.
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise Error(
                    503, "newer_schema", "Database was upgraded by a newer Tasktrack."
                )
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[target]:
                    c.execute(statement)
                if target == 1:
                    c.execute(
                        "INSERT INTO instance VALUES (?,?)", (str(uuid.uuid4()), now())
                    )
                c.execute(f"PRAGMA user_version={target}")
            c.commit()

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            if write:
                c.commit()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) or "busy" in str(exc):
                raise Error(
                    503,
                    "storage_busy",
                    "Storage is busy; retry with the same request key.",
                    retryable=True,
                ) from exc
            raise
        finally:
            if c.in_transaction:
                c.rollback()
            c.close()

    def put_blob(self, content):
        digest = hashlib.sha256(content).hexdigest()
        destination = self.blobs / digest
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=self.blobs)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(content)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, destination)
                directory_fd = os.open(self.blobs, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return digest

    def backup(self, destination):
        destination = Path(destination).expanduser().resolve()
        if destination.exists():
            raise Error(
                409,
                "backup_exists",
                "Choose a new backup directory; existing backups are never replaced.",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(prefix=".tasktrack-backup-", dir=destination.parent)
        )
        try:
            snapshot = sqlite3.connect(stage / "tasktrack.sqlite3")
            try:
                with self.connection() as c:
                    c.backup(snapshot)
                blobs = [
                    r[0]
                    for r in snapshot.execute("SELECT DISTINCT sha256 FROM attachments")
                ]
                instance_id = snapshot.execute("SELECT id FROM instance").fetchone()[0]
            finally:
                snapshot.close()
            (stage / "blobs").mkdir()
            # Blobs are immutable and v1 never deletes them, so live writers cannot
            # remove bytes referenced by this exact SQLite snapshot.
            for digest in blobs:
                source = self.blobs / digest
                if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise Error(
                        503,
                        "blob_corrupt",
                        f"Cannot back up missing or corrupt blob {digest}.",
                    )
                shutil.copyfile(source, stage / "blobs" / digest)
            if (self.directory / "config.json").exists():
                shutil.copyfile(self.directory / "config.json", stage / "config.json")
            (stage / "manifest.json").write_text(
                encode(
                    {
                        "instance_id": instance_id,
                        "schema_version": SCHEMA_VERSION,
                        "created_at": now(),
                        "blobs": blobs,
                    }
                )
                + "\n"
            )
            os.rename(stage, destination)
            return {
                "path": str(destination),
                "instance_id": instance_id,
                "blobs": len(blobs),
            }
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    @staticmethod
    def restore(source, destination):
        source, destination = (
            Path(source).resolve(),
            Path(destination).expanduser().resolve(),
        )
        if destination.exists():
            raise Error(
                409,
                "restore_exists",
                "Restore requires a new directory; stop the service before switching data directories.",
            )
        manifest = json.loads((source / "manifest.json").read_text())
        with closing(
            sqlite3.connect(f"file:{source / 'tasktrack.sqlite3'}?mode=ro", uri=True)
        ) as c:
            if (
                c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or c.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise Error(
                    422, "invalid_backup", "Backup failed SQLite integrity checks."
                )
            referenced = {
                r[0] for r in c.execute("SELECT DISTINCT sha256 FROM attachments")
            }
            if (
                referenced != set(manifest["blobs"])
                or c.execute("SELECT id FROM instance").fetchone()[0]
                != manifest["instance_id"]
            ):
                raise Error(
                    422,
                    "invalid_backup",
                    "Backup manifest does not match its database.",
                )
            if c.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                raise Error(503, "newer_schema", "Backup requires a newer Tasktrack.")
        for digest in referenced:
            if (
                len(digest) != 64
                or any(x not in "0123456789abcdef" for x in digest)
                or hashlib.sha256((source / "blobs" / digest).read_bytes()).hexdigest()
                != digest
            ):
                raise Error(
                    422, "invalid_backup", "Backup contains a missing or corrupt blob."
                )
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(prefix=".tasktrack-restore-", dir=destination.parent)
        )
        try:
            shutil.copyfile(source / "tasktrack.sqlite3", stage / "tasktrack.sqlite3")
            (stage / "blobs").mkdir()
            for digest in referenced:
                shutil.copyfile(source / "blobs" / digest, stage / "blobs" / digest)
            if (source / "config.json").exists():
                shutil.copyfile(source / "config.json", stage / "config.json")
            Store(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return {
            "path": str(destination),
            "instance_id": manifest["instance_id"],
            "blobs": len(referenced),
        }
