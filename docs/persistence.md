# Data, backup and migration

This page describes the local installation. Hosted workspaces use
[Cloudflare storage](cloudflare.md#storage-and-authorization) independently.

## Where data lives

`TT_DATA_DIR` or `--data-dir` selects the data directory. The default is
`~/.local/share/tasktrack`. It contains:

- `tasktrack.sqlite3` and SQLite WAL/SHM files while connections are active
- `blobs/`, containing immutable files named by SHA-256
- optional `config.json` for inbox browser URL templates and access logging

Keep this directory outside the source checkout. Each database has a persistent
UUID. Its integer project/task IDs and original creation attribution survive a
backup and restore. Restoring creates another copy of the same instance, so stop
the old service before treating the restored copy as the authority.

Writes use a separate SQLite connection per operation, foreign keys, WAL and
`BEGIN IMMEDIATE`. Validation, state changes, events and retry receipts commit
together. The default busy wait is two seconds; exhausted contention returns
`503 storage_busy` and CLI exit 4. Retry with the original request key.

This follows [SQLite’s transaction isolation](https://www.sqlite.org/isolation.html).

## Back up a running service

```sh
tt backup /safe/location/tasktrack-2026-09-15
```

The destination must not already exist. Tasktrack takes a live SQLite snapshot
with [Python’s SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup),
then copies only the immutable blobs referenced by that exact snapshot. A manifest
records the instance, schema and hashes. It also copies `config.json` if present.
The finished backup directory appears only after copying and validation succeed.

Blobs are written to temporary files, flushed, and atomically renamed before their
metadata transaction commits. An interrupted upload can leave an unreferenced blob
or temporary file, but cannot commit a reference to partial bytes. V1 deliberately
does not garbage-collect blobs. This keeps referenced bytes available throughout
a live backup and preserves shared content across attachments.

## Restore into an isolated directory

```sh
tt restore /safe/location/tasktrack-2026-09-15 /safe/location/tasktrack-restored
tt --data-dir /safe/location/tasktrack-restored health
tt --data-dir /safe/location/tasktrack-restored task list --json
tt --data-dir /safe/location/tasktrack-restored serve --port 7778
```

Restore refuses an existing destination, checks SQLite integrity and relationships,
matches the manifest against the database, and verifies every referenced blob hash.
It stages the restored directory before publishing it. Inspect it on port 7778,
stop the old service, then use the restored directory with your usual port.

The regression suite restores task IDs, parent links, dependencies, comments,
attachment metadata, claims, checkpoints, history, aliases and idempotency receipts,
and compares the exact rows and bytes. Corrupt backups fail without a destination.

## Schema migrations

`PRAGMA user_version` tracks the schema. V1 creates the core relational records;
v2 adds idempotency receipts and lookup indexes. Startup applies missing migrations
in one transaction. A concurrent process rechecks the version after acquiring the
writer lock. Repeated startup is safe. An older binary refuses a newer schema.

Tests construct a version-1 database with a preserved project ID, original timestamp
and unknown historical actor, then upgrade it twice. A deliberately failing migration
rolls back both DDL and the schema version. Unknown attribution stays unknown.

### The earlier prototype

The handoff mentions prototype commit `874fa91`, an unversioned `/api`, and an
`integration` branch. No source, database, installed `tt`, or running port-7777
service was found during this build. **Compatibility with that prototype is
unverified.** There is no speculative importer and no compatibility claim for its
old commands or routes.

An unversioned nonempty database is rejected before schema changes. To migrate a
prototype later:

1. Locate its source revision, schema, complete database and attachment directory.
2. Use its SQLite backup facility to snapshot the database; copy all referenced files.
3. Inspect a copy in an isolated directory. Record incompatible keys and missing actors.
4. Build an explicit, versioned importer preserving IDs, relationships, actors,
   timestamps and hashes. Map legacy thread IDs using configured inbox source/project
   identity; mark unresolved links without inventing identities or historical events.
5. Exercise the actual legacy CLI and `/api` entry points before adding aliases, then
   compare original/imported rows and blob hashes. Switch the live service only after
   that rehearsal succeeds.

Agent Inboxes data is never opened or migrated by Tasktrack.
