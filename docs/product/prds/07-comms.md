# F07 — Comms question box and conversion

**Wave:** 2 · **Dependencies:** F04, F06 · **Review:** customer workflow review

## Outcome

A customer sends a question as naturally as an email. It becomes a durable 📞
Comms item with an owner, replies and clear status. A useful question can become
a task without copying away its history or treating a question as approved scope.
Human and AI clients operate on the same records and permission rules.

## Screens and lifecycle

[Comms wireframe](../wireframes.html#comms). Customer home has subject, question,
optional files and Send question. A successful submission says “Question received.
We'll reply here and notify you by email.” Do not display a typing indicator,
instant-answer promise or fabricated response-time estimate. The new item links
back to the project and appears in authorized TRIAGE.

Statuses: `open`, `waiting_on_team`, `waiting_on_customer`, `resolved`.
Creation defaults waiting_on_team. A shared staff reply may choose waiting_on_customer
or resolved; a customer reply to resolved reopens it. Internal notes do not change
customer-visible status. Assignment is internal; show a team contact externally,
not an unassigned automation failure. A “Received” empty reply state is legitimate.

Convert to task is internal-only. The dialog pre-fills a suggested title and
description, requires acceptance criteria before marking the task ready, and lets
staff select epic/priority. It clearly says the conversation will remain linked.
One primary task per conversion; repeat conversion returns the existing task.
Further tasks can be explicitly linked through a separate action, not accidental
button retries. Conversion does not close the question unless staff chooses it.

## Data and transaction

`comms(id,project_id,reference,subject,discussion_id,status,owner_membership_id,
created_by,created_at,updated_at,version,converted_task_id,converted_at)`.
Reference namespace `PROJECT-C<number>` prevents collision with task refs.
Subject1–120 characters; body/files use F06. A separate event sequence tracks
conversation activity. `comms_conversion(comms_id UNIQUE,task_id,request_key,
source_version,created_by)` records the permanent dedupe boundary.

Conversion creates task, attaches the shared discussion, writes linkage and audit
event in one tenant SQLite transaction. Keep all original timestamps/authors.
Task description includes a reviewed synopsis, not unfiltered internal transcript.
Attachments keep their original visibility. Customer-visible task sharing is an
explicit choice; creating an internal task must not expose its PRD or costs.

## API and agent contract

- `POST /projects/{id}/comms`: subject, body, files; authenticated project participant.
- `GET /comms?project_id=&status=&owner=` and `GET /comms/{id}` return permitted view.
- `PATCH /comms/{id}`: expected version, subject/status/owner; owner changes internal.
- `POST /comms/{id}/convert`: expected version, task fields, share_task flag;
  returns201 `{comms_id,task_id,discussion_id}` or original idempotent result.
- `POST /comms/{id}/links`: explicit related task IDs in same project.
- Replies use F06 discussion endpoints, with agent identity stamped by credential.

Events: comms.created/assigned/status_changed/reopened/converted. Notifications
from F08 tell the submitter their question was received once and announce replies.
Agent runner may draft a reply, but a human must publish customer-facing replies
by default. A Comms message cannot authorize spending, invitations or code deployment.

## Acceptance

1. Customer submits question/files via browser and API; staff sees one TRIAGE
   item and can reply asynchronously with persistent history.
2. Two simultaneous conversions create exactly one primary task and retain all
   comments/files/authors. Crash during conversion leaves no orphan task.
3. External users cannot convert, assign internal owners, read private notes or
   invoke runner/finance actions through message text or direct API calls.
4. Customer reply reopens resolved Comms; internal note does not send a false
   customer-visible status update.
5. Conversion preserves old Comms URL, search references and permissions. Task
   archiving does not delete or strand the conversation.
6. Empty, long, failed-send, offline draft and phone layouts are verified.

Migration adds tables without reclassifying old tasks. No bulk “AI cleanup” of
historical comments. Release requires F06 visibility and idempotency tests first.
