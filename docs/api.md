# HTTP and CLI reference

The UI and API share `http://127.0.0.1:7777`. Start with `tt serve`; choose another
port using `--port`. V1 binds to loopback and rejects foreign browser origins/hosts.
All UI assets are local. No wildcard CORS or external runtime service is required.

The [hosted edition](cloudflare.md) uses `https://tasks.eastbayprojects.com` with
the same `/api/v1` routes. Send `Authorization: Bearer <agent-token>`; the token
determines the workspace and actor. Hosted requests ignore `X-Actor`. The hosted
CLI uses `TT_URL` and `TT_TOKEN`. Browser sessions require `X-CSRF-Token` for writes;
the browser also sends `X-Workspace-ID` to detect a stale workspace selection.

[Executed request/response examples](api-examples.md) ·
[Complete captured transcript](api-transcript.json)

## Attribution, versions and retries

Every mutation requires:

| HTTP | CLI | Meaning |
| --- | --- | --- |
| `X-Actor` | `--as`, or `TT_ACTOR` | Supplied human/agent identity; never inferred |
| `X-Session` | `--session`, or `TT_SESSION` | Required for claim/resume and actions under that claim |
| `X-Via` | Set to `cli` automatically | `api` (default), `ui`, or `cli` |
| `Idempotency-Key` | `--request-id` | Key for an operation’s retries; CLI generates one if omitted |
| Body `expected_version` | `--version` | Required for updates/actions on existing mutable entities |

Locally, these identities provide attribution, not authentication. Claiming unassigned work
assigns it to the claimant; other assignments require explicit reassignment first.
An agent’s claim does not acquire an inbox file reservation.

Use `Content-Type: application/json`, except multipart uploads. Unknown body fields
and query filters fail with a field error. A successful state mutation increments
the version once and appends an event. No-ops do neither. Independent comments and
attachments append events without changing task state versions.

Receipts are scoped by actor and request key. The same method, path, body, session
and origin return the original status and response, even if the original version
has since advanced. A changed request under the same key fails with `409`.
Attachment fingerprints use metadata and the file hash, not the multipart boundary.
Keep an explicit request ID when retrying a CLI invocation after a lost response.

Stale versions return the current version and a record URL. Fetch the record,
reconcile your changes, and issue a new request key with its current version.
Versions prevent accidental overwrites; they do not silently merge edits.

## Routes

| Route | Contract |
| --- | --- |
| `GET /api/v1/health` | Version, schema version, persistent instance UUID |
| `GET, POST /api/v1/projects` | Paginated list; create project |
| `GET, PATCH /api/v1/projects/{id}` | Full project; version-checked edit/rename |
| `GET /api/v1/projects/by-key/{key}` | Resolve current or historical key |
| `GET, POST /api/v1/tasks` | Paginated/filterable list; create task or epic in backlog |
| `GET, PATCH /api/v1/tasks/{id}` | Full task; version-checked metadata edit |
| `POST /api/v1/tasks/{id}/{action}` | Shared validated workflow action (below) |
| `GET, POST /api/v1/tasks/{id}/comments` | Paginated notes; append `{body}` |
| `GET, POST /api/v1/tasks/{id}/attachments` | Paginated metadata; multipart upload |
| `GET /api/v1/attachments/{id}/content` | Exact bytes as an attachment download |
| `GET /api/v1/tasks/{id}/history` | Paginated chronological task events |
| `GET /api/v1/events` | Ordered instance events, optionally filtered |
| `GET /api/v1/brief` | Bounded assigned/claimed unfinished work for `X-Actor` |

Project lookup also accepts a key in place of `{id}`. Task lookup accepts a
project-qualified reference such as `HBR-2`; earlier keys resolve through aliases,
and membership is checked. Browser URLs are `/projects/HBR` and `/tasks/2`.

### Project fields

Create accepts `key`, `name`, `brief_markdown` and `document_links`. Update accepts
the same fields plus `expected_version`. Keys are trimmed, uppercased and match
`[A-Z][A-Z0-9]{0,9}`. Names are nonempty and at most 200 characters. Document links
are `{label, uri}` entries. Renaming preserves integer IDs and historical aliases;
another project cannot take a former key. Renaming back to your own alias is allowed.

### Task fields

Create accepts `project_id`, `kind` (`task` or `epic`), and the metadata below.
Status starts at `backlog`; version starts at 1. IDs are immutable global integers.

| Metadata | Validation |
| --- | --- |
| `title` | Trimmed, nonempty, at most 120 characters |
| `description_markdown` | May be empty in backlog; required to start |
| `acceptance_criteria` | List of nonempty strings; at least one to start |
| `assignee` | Actor name or `null`; claimed ownership needs `reassign` |
| `priority` | `low`, `normal` (default), `high`, `urgent` |
| `parent_id` | One unarchived epic in this project, for ordinary tasks only |
| `dependency_ids` | Complete replacement list of prerequisite task IDs; no self/cycles/cross-project references |
| `thread_links` | Complete replacement list of inbox links, at most one primary |

PATCH accepts only that metadata and `expected_version`. Workflow fields such as
`status`, `execution`, `position`, `checkpoint`, `result`, `completion` and archive
timestamps cannot bypass the named actions. Thread identities use
`{source, project, thread_id, primary}`; `source` is the inbox database UUID.

Full records include requirements, dependencies and computed blockers, parent epic,
assignment and execution, checkpoint, result, retained completion, archive timestamp,
version, actor/origin/timestamps, current reference, stable URL and instance UUID.
Epic `progress` gives child completion counts; `children_url` is a paginated task query.

## Workflow actions

Every action includes `expected_version`.

| Action | Additional body and requirements |
| --- | --- |
| `claim` | Session header; ordinary task in backlog or unclaimed in-progress; readiness and assignee checks |
| `resume` | Session header; rebinds an existing claim for the same actor |
| `checkpoint` | `checkpoint`; in-progress work, acting assignee and matching claim session |
| `handoff` | `checkpoint`, optional `to`; clears claim, retains in-progress status; omitted `to` retains assignee, explicit `null` offers pickup |
| `reassign` | `assignee` (or null), `reason`; explicitly clears any claim |
| `block`, `unblock` | `reason`; set/clear explicit blocker overlay |
| `submit` | `result: {summary, evidence}`; in-progress, unblocked, matching owner/session; nonempty evidence |
| `complete` | `acceptance_note`; review, unblocked, retained evidence; epics require completed children |
| `request-changes` | `reason`; review to in-progress, no new claim |
| `reopen` | `reason`; done to backlog; `reopen_parent: true` explicitly reopens a completed epic in the same transaction |
| `archive`, `restore` | `reason`; claims and unfinished relied-on relationships must be resolved before archiving |
| `move` | `status`, optional `before_id`, and the equivalent transition’s required fields |

Manual backlog → in-progress movement requires an assignee and readiness but acquires
no claim. In-progress → review requires `result`; review → done requires
`acceptance_note`; review → in-progress requires `reason`. In-progress/review → backlog
requires `reason` and `checkpoint`, clearing the claim. Done → backlog follows
`reopen`, including explicit parent reopening. Other jumps fail. Reordering within a
column requires no transition context. An omitted/null `before_id` chooses the end;
an anchor must be another unarchived task in the destination project and column.
Ranks are server-owned, computed transactionally with deterministic ID tie-breaking.

An unfinished dependency blocks start, submit and complete. Reopening a prerequisite
shows downstream work blocked without changing its column. Archiving does not make
unfinished work count as complete.

### Checkpoint shape

See [example-checkpoint.json](example-checkpoint.json). `summary` and `next_action`
are required. Code work provides `workspace` and `branch` or `commit`; other work
provides a nonempty `not_applicable_reason`. `acceptance_remaining` is a string list;
`evidence` is a list of `{label, uri}`. Evidence allows `http`, `https` and `file`
references. Tasktrack records links; it never executes task text or opens a local
evidence path on the server.

## Pagination and events

Lists return `items`, `next_cursor`, `has_more` and `total`. Default limit is 50;
maximum is 200. Task lists also return `project_total` before assignee/epic/priority/
blocked/search filters (within the chosen project, column and archive view).

Task filters are `project_id`, `status`, `assignee`, `parent_id`, `priority`, `blocked`,
`archived`, `q`, and `kind`. Booleans use `true`/`false`; archived defaults to false.
Search matches current and historical references, titles and descriptions. Ordering
is board column order, then server position, then ID. Other lists use ascending ID;
history uses ascending event sequence. Cursors are bound to the instance, route and
filters. These are pages of current state; edits between requests can shift offsets.
Restarting a list gives a fresh view. Never treat a first page as the whole project.

Brief supports `project_id`, `limit` and `cursor`. It includes unfinished assigned or
claimed work, blockers, current checkpoint/next action, result evidence, thread links,
versions and full task URLs. Brief reads never claim, resume or mutate work.

Events use a separate cursor: `source=<instance_id>&after=0&limit=50`, with optional
`project_id`/`task_id`. A page examines up to `limit` events globally, then filters.
An empty filtered page can still have `has_more: true`. Advance `after` only to the
returned examined sequence and keep draining. A wrong source fails explicitly.
Reading events creates no receipt, acknowledgment, claim or status change.

## Attachments

Upload multipart fields `file` and optional `comment_id`. The comment must belong to
that task. Files are bounded to 10 MiB, content-addressed by SHA-256, and downloaded
by immutable attachment ID. Filenames are display metadata, never storage paths.
Downloads use `application/octet-stream`, `Content-Disposition: attachment` and
`nosniff`, including uploaded HTML.

## Errors and CLI

| HTTP | Meaning | CLI exit |
| --- | --- | --- |
| `200`, `201` | Successful read/action, creation | 0 |
| `400` | Malformed input, absent actor/key, invalid content type/origin | 2 |
| `404` | Unknown record or route | 2 |
| `409` | Version, claim, alias or idempotency conflict | 3 |
| `422` | Field or workflow validation | 2 |
| `413` | Upload/request too large | 2 |
| `503` | Bounded storage unavailability or unsupported newer schema | 4 |

Errors have `{error: {code, message, fields}}`, with `current_version` and
`current_url` for version conflicts. `fields` maps invalid inputs to explanations.

Use `tt --help`, `tt task --help`, and `tt task ACTION --help`. Common flags work
before or after subcommands. Human output is the default; `--json` gives stable JSON
on stdout and structured failures on stderr.

```sh
tt project create HBR Harbor --as nate
tt project update 1 --key HARBOR --name Harbor --version 1 --as nate
tt project update 1 --brief-file prd.md --version 2 --as nate
tt task create HARBOR --file docs/example-task.json --as nate --request-id create-1
tt task list --project HARBOR --assignee codex@harbor --json
tt task update 2 --file task-metadata.json --version 1 --as nate
tt task claim 2 --version 2 --as codex@harbor --session checkout-1
tt task checkpoint 2 --file docs/example-checkpoint.json --version 3 --as codex@harbor --session checkout-1
tt task handoff 2 --to nate --file docs/example-checkpoint.json --version 4 --as codex@harbor --session checkout-1
tt task comment 2 --body 'Decision: preserve the first receipt.' --as nate
tt task attach 2 evidence.txt --comment-id 1 --as nate
tt task download 1 ./downloaded-evidence.txt
tt task history 2 --json
```

These CLI examples show command shapes; obtain IDs and versions from your records.
All actions in the table have matching `tt task ACTION` commands. `--file` supplies
the JSON action body; checkpoint/handoff also accept a bare checkpoint object.
`--to null`/`--assignee null` explicitly clears ownership. Dependency and thread-link
lists are supplied with `task update --file`. `project create/update --file` accepts
document links. CLI uploads read an explicitly selected file, and downloads refuse
to overwrite an existing destination.

## Hosted access administration

These routes require hosted authentication; browser mutations require the current
CSRF token. Actor/role headers cannot grant permissions.

| Route | Contract |
| --- | --- |
| `GET /api/v1/me` | Identity, current membership and accessible workspaces. Agent tokens see only their workspace. |
| `GET /api/v1/me/capabilities?project_id=ID` | `policy_version` and a map of documented action names to booleans. |
| `GET /api/v1/memberships` | Admin-only member records with `id`, `kind`, `role`, `status`, `revision`, `customer_id`, and `capabilities`. |
| `PATCH /api/v1/memberships/ID` | Admin-only; `expected_version`, optional `role` (admin/billing/regular), `status` (active/suspended), `kind`, `customer_id`, `capabilities`, `reason`. Internal/external conversion requires a recent browser sign-in and a reason. |
| `GET /api/v1/projects/ID/access` | Settings permission; returns `revision`, `internal_access`, `customer_id`, and grants. |
| `PATCH /api/v1/projects/ID/access` | Admin-only; `expected_version` is the access revision, `internal_access` is all/restricted, and `grants` replaces the complete participant list. |
| `GET /api/v1/projects/ID/participants` | Eligible active participants, without unrelated customer or staff records. |
| `POST /api/v1/tenants` | Browser session, `Idempotency-Key`, `{name,timezone}`; creates a workspace/admin membership and switches the session. Reload to obtain rotated CSRF state. |

A project grant has `membership_id`, `access` (participant/manager), optional
`capabilities`, `can_view_invoices`, and `can_approve_scope`. Customer identity is
resolved from stored membership, never accepted from grant JSON. Existing customer
grants must be removed before changing a project's customer. The API rejects stale
revisions with 409, and removing the last active admin with `last_admin` (409).
Role/status/kind/capability changes revoke affected sessions and agent credentials
in the same database transaction. A membership-level `project.create` grant can
allow an internal non-admin to create projects; admin-only capabilities cannot
be delegated. See [authorization policy](authorization-policy.md).
