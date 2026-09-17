"""SQLite row/cursor adapter for a workspace's Cloudflare Durable Object.

The caller wraps the complete synchronous domain operation in transactionSync.
No network operation or await can run inside that transaction.
"""

import hashlib
import uuid
from contextlib import contextmanager
from pathlib import Path

from .db import MIGRATIONS, SCHEMA_VERSION, now, validate_migration


class Row:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return self.values.keys()

    def __getitem__(self, key):
        return (
            list(self.values.values())[key]
            if isinstance(key, int)
            else self.values[key]
        )

    def __iter__(self):
        return iter(self.values.values())


class Cursor:
    def __init__(self, rows, lastrowid):
        self.rows = iter(rows)
        self.lastrowid = lastrowid

    def fetchone(self):
        return next(self.rows, None)

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return self.rows


class CloudStore:
    def __init__(self, storage):
        self.storage = storage
        self.directory = Path("/tasktrack-workspace-config")
        self.sql = storage.sql
        self.sql.exec("CREATE TABLE IF NOT EXISTS tt_schema(version INTEGER NOT NULL)")
        rows = self.sql.exec("SELECT version FROM tt_schema").toArray()
        version = rows[0]["version"] if rows else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError("Workspace schema requires a newer Tasktrack.")
        for target in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[target]:
                self.sql.exec(statement)
            if target == 1:
                self.sql.exec(
                    "INSERT INTO instance VALUES (?,?)", str(uuid.uuid4()), now()
                )
            validate_migration(self, target)
            self.sql.exec("DELETE FROM tt_schema")
            self.sql.exec("INSERT INTO tt_schema VALUES (?)", target)

    @contextmanager
    def connection(self, write=False):
        yield self

    def execute(self, statement, params=()):
        if statement == "BEGIN":
            # The whole read already runs inside the caller's transactionSync.
            return Cursor([], 0)
        result = self.sql.exec(statement, *params)
        rows = [Row(row) for row in result.toArray()]
        lastrowid = (
            self.sql.exec("SELECT last_insert_rowid() AS id").one()["id"]
            if statement.lstrip().upper().startswith("INSERT")
            else 0
        )
        return Cursor(rows, lastrowid)

    def put_blob(self, content):
        return hashlib.sha256(content).hexdigest()

    def executemany(self, statement, parameters):
        for params in parameters:
            self.execute(statement, params)
