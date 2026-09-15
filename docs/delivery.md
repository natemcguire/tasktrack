# Tasktrack v1 delivery

The hosted edition adds magic-link accounts, workspaces, customer sharing and
Cloudflare deployment. See [Cloudflare setup and verification](cloudflare.md).
The record below describes the original local release.

## Source and preservation

Built in the separate `tasktracker` workspace for the repository
[`natemcguire/tasktrack`](https://github.com/natemcguire/tasktrack), on
`feature/tasktrack-v1`. The public default branch is `main`. Use `git rev-parse HEAD`
to identify the exact checkout revision.

The starting checkout had no commits, files or remote. Inspection found no installed
`tt`, existing Tasktrack directory/database in the expected locations, GitHub repository
matching Tasktrack, or process listening on port 7777. No prototype implementation was
available to reuse; no owner database or attachment directory was changed or migrated.

The input was the [Tasktrack handoff](https://github.com/natemcguire/agent-inboxes/blob/b13f38f810898047c2348751e53ab363bbb22f2e/docs/tasktrack-handoff.md)
at Agent Inboxes commit `b13f38f810898047c2348751e53ab363bbb22f2e`. Agent Inboxes code,
its installed service, public API and data were left unchanged. Development, API
transcripts, screenshots and tests used temporary directories outside the checkout.

## Implemented

- Standalone MIT application, installable `tt`, loopback HTTP API and offline browser UI
- Projects, key/name settings, stable historical aliases, project/epic PRDs and document links
- Four-column board, search and combined filters, counts and pagination, keyboard/menu
  ordering, drag/drop, archived view and restore, desktop and narrow-screen layouts
- Task/epic detail, acceptance criteria, dependencies, blockers, durable comments,
  exact-byte file attachments and attributed chronological history
- Atomic claims across real CLI/HTTP writers, version conflicts, idempotent retries,
  lost-session resume, explicit reassignment, checkpoints and structured handoffs
- Review evidence, acceptance notes, explicit epic/child reopening, preserved completion evidence
- Live SQLite/blob backups, validated isolated restores, atomic versioned migrations
  and safe refusal of unknown/unversioned or newer schemas
- Stored inbox thread identities and configured conversation URLs, without a second task authority

## Verification

| Check | Result |
| --- | --- |
| `python3 -m unittest -v` | 18 behavior tests passed on Python 3.14 |
| `python3.11 -m unittest -v` | The same 18 tests passed on the minimum supported Python |
| `python3 tests/mutation_checks.py` | All 3 deliberate regressions caught: disabled claim guard, skipped rename, duplicate retry |
| `npm run test:browser` | Real Chromium workflow passed at 1512×1100 and 390×844; no browser/server exceptions |
| `python3 tests/capture_api.py` | 22 real API requests captured with workflow assertions and exact-byte upload/download verification |
| `uv build` | Source archive and standalone wheel built |
| `python3 tests/check_package.py` | Wheel installed in a fresh venv outside the checkout; installed CLI/HTTP shared state and all offline assets matched |
| `uvx ruff check tasktrack tests` | Passed |

The concurrency tests launch real competing CLI and HTTP processes. Exactly one
claim commits; the competing operation returns a conflict, and history has one claim.
Concurrent independent board moves preserve deterministic order after service restart.

Backup/restore tests compare exact database rows, task/parent relationships, actors,
timestamps, receipts, histories and blob hashes. Corrupt backups leave no restored
destination. Migration tests cover v1 → v2, repeat startup, transactional rollback
after an injected migration failure, unsupported newer schemas, and untouched
unversioned prototype-shaped data.

Browser checks create the project, PRD, epic and task through actual forms, then
verify persisted records via CLI/API. They exercise a stale edit against a concurrent
CLI change, preserved unsaved input and explicit conflict review, filters, keyboard
ordering, real drag/drop, blocking, claim/checkpoint, safe Markdown, upload/download,
review evidence validation, completion, archive/restore, rename, old URLs and restart.
Mobile checks cover column selection, task reading and unclipped editing controls.

Actual captures: [board](screenshots/board.png), [task detail](screenshots/task-detail.png),
[history](screenshots/history.png), [mobile board](screenshots/mobile-board.png),
[mobile detail](screenshots/mobile-detail.png). The [browser run record](browser-verification.json)
includes the Chromium version and capture time. Screenshots contain synthetic work.

## Boundaries and continuation

**Prototype migration remains unverified.** The source/database named in the handoff
was unavailable. Next action: obtain that source and a complete backup, then implement
and rehearse its importer in this repository on a new migration branch. Acceptance:
preserved IDs, relationships, attribution, timestamps, hashes and legacy thread-link
identity, plus exercised actual legacy CLI/API entry points. Evidence currently
available: [preservation and migration procedure](persistence.md) and schema-refusal tests.

**Inbox enrichment is a separate repository change.** Next action: coordinate an
Agent Inboxes adapter only when requested, using Tasktrack’s HTTP API. Acceptance:
one task authority, at most one-second autocomplete calls, and working mail while
Tasktrack is absent. Current evidence: stored-link rename/absence regression and
[integration contract](integration.md). No inbox browser URL has been guessed.

V1 has no blob garbage collection, accounts, cloud sync, process supervision or
agent spawning. Current-state list cursors use offsets; edits between page reads
can shift pages, so restart a list to refresh the whole view. See the API reference
for the separate event-source/sequence cursor.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
tt serve
```

Open `http://127.0.0.1:7777`. Set `TT_DATA_DIR` to choose the durable data directory.
No Node installation or Agent Inboxes service is needed to run the app.
