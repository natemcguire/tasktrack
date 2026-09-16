# F05 — TRIAGE, search and keyboard access

**Wave:** 2 · **Dependencies:** F01, F06 · **Review:** daily-use review

## Outcome

TRIAGE answers “what should I look at now?” with a simple newest-first list of
relevant items. It is not an opaque AI ranking system. Command-K finds accessible
projects, tasks, epics, Comms and navigation actions without requiring a mouse.

## Screens and interaction

[TRIAGE](../wireframes.html#triage) · [Search](../wireframes.html#search).
A row shows item type, title, project, latest relevant activity, timestamp, unread
badge and next action. Default order is latest visible activity descending, then
stable item ID. Coalesce repeated activity for the same item into one row; its
detail opens the underlying chronological events. New activity raises the item.
Internal-only activity must not reorder a customer's row or reveal a hidden count.

Default filters: All, Unread, Mentions, Questions, Assigned to me. Finance and
runner failures appear only for capable staff. Actions: Open, Mark read/unread,
Snooze until a chosen time. Do not mark an item read from a background fetch;
opening detail acknowledges only the latest event actually rendered. Newer unseen
activity remains unread. Snoozing does not alter the task state or another user's
inbox. A direct mention can resurface a snoozed item; show why it resurfaced.

Cmd/Ctrl-K opens a modal with a labeled input. Typing searches; up/down chooses,
Enter opens, Escape closes and returns focus. Distinguish “People” mentions from
task references. `/` focuses project search only outside editable fields; `?`
opens shortcut help. Destructive actions are navigation to a confirmation screen,
never immediate commands in search. No keyboard shortcut fires while composing
text or using an input method editor.

## Data and query plan

`attention_state(membership_id,item_type,item_id,last_read_sequence,snoozed_until,
manual_unread_at)` is per tenant. A materialized `item_activity` projection stores
latest internal and shared event sequences separately. Source events remain
authoritative. Rebuild projection from the event log with a checkpoint; never
advance past unprocessed history. Badge totals count items, not every keystroke.

Use tenant-local SQLite FTS for titles, permitted body text and references. Shared
and internal content must be separately indexed or filtered before scoring.
Initial prefix search starts at2 characters; direct references work at any length.
Debounce150ms, cancel obsolete requests, cap20 suggestions and50 full results per
page. Match exact reference first, then title, then body. No vector service needed.
Indexing lag is visible through `as_of`; unavailable search leaves navigation usable.

## API and events

- `GET /triage?filter=&project_id=&cursor=` returns permission-filtered rows,
  unread total and as_of sequence. Newest-first stable cursor binds filters.
- `POST /triage/{type}/{id}/read`: `{through_sequence}` limited to visible events;
  returns updated state. A client cannot acknowledge an unseen future sequence.
- `POST /triage/{type}/{id}/snooze`: `{until}` within90 days or null to clear.
- `GET /search?q=&project_id=&types=`: typed results with safe snippet and URL.
- `GET /shortcuts`: capability-aware declarative shortcuts for browser/help CLI.

Projection consumes task/comment/Comms/approval/payment/run events. Read state is
not an email read receipt. External users never receive staff finance failures or
internal mention identities. Search access is evaluated on every request, even
when reusing a cached result set.

## Acceptance and rollout

1. Three new comments on one task produce one raised row with the right unread
   sequence; opening an older page does not acknowledge a later comment.
2. Customer ordering, counts and snippets do not change from internal-only events.
3. Revoke a project grant while search is open: next query/result navigation
   denies access and clears cached snippets.
4. Concurrent read/snooze/new mention handling is deterministic across two tabs.
5. Cmd/Ctrl-K, arrows, Enter/Escape, IME and screen reader focus work on desktop;
   phone exposes a visible Search button without requiring shortcuts.
6. On a fixture of10,000 items, p95 search is below500ms and TRIAGE below700ms in
   a warmed local hosted runtime; record actual environment and results.
7. A projection rebuild yields identical visible rows and unread counts without
   sending notifications or mutating task versions.

Roll out after access and discussion visibility are stable. Track projection lag,
query latency and failed cursors without storing the user's search text by default.
