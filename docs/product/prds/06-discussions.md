# F06 — Comments, mentions and attachments

**Wave:** 2 · **Dependencies:** F01 · **Review:** internal + external user review

## Outcome and scope

People and agents discuss tasks using the same durable thread. Customers can
comment on shared work; staff can also keep internal notes. Typed @ mentions
notify eligible participants and reference work without confusing a task citation
with a person. Support Markdown, files, edits and moderated redaction, not live chat.

## UX

[Discussion wireframe](../wireframes.html#discussion). A thread groups replies
chronologically with author, human/agent badge, timestamp and edited marker. Staff
composer has explicit “Customer-visible reply” and “Internal note” modes. Default
to the last mode used in this project, visibly labeled; never infer visibility
from recipients. External users get the shared composer only. Before publishing
an internal note, create a reviewed shared copy; ordinary edit cannot flip visibility.

Typing `@` opens a menu with People and Work items, scoped to current access.
Arrow keys/Enter select a typed token; Escape closes without changing text.
Store entity IDs separately from display text. Renaming a person or task preserves
the reference. Ambiguous shorthand requires selection; unresolved references remain
plain text with no notification. Staff cannot mention an uninvited customer into
a project or expose another customer's identity through autocomplete.

Uploads show progress, validated filename/size, failure and Retry. Ctrl/Cmd-Enter
submits; Enter inserts a newline. Keep an unsent draft locally under tenant/user/
resource key and clear it on logout. Do not automatically send restored drafts.

## Data and invariants

`discussion(id,project_id,resource_type,resource_id)`,
`comment(id,discussion_id,author_principal,visibility,body_markdown,version,
created_at,edited_at,redacted_at,published_from_id)`,
`comment_revision(comment_id,version,body_digest,editor,reason,created_at)`,
`mention(comment_id,entity_type,entity_id)` and attachment links.

Body is1–50,000 characters; allow attachment-only replies with an accessible file
label. Max50 mention entities. Store original author identity and agent credential
name; the caller cannot impersonate a human. Edits are author-only by default;
admins may redact with a reason and immutable audit metadata. Preserve private
revision bodies under restricted retention; customers see only the current shared
text and “edited,” never a removed internal draft.

## API and delivery

- `GET /discussions/{id}/comments?cursor=`: chronological permitted comments.
- `POST /discussions/{id}/comments`: `{body_markdown,visibility,mentions,attachment_ids}`;
  revalidate every mention and file against discussion/project at commit.
- `PATCH /comments/{id}`: expected version, body and mention replacement; removed
  mentions stop future notifications; newly added mentions notify once.
- `POST /comments/{id}/publish`: internal source, reviewed shared body, explicit
  permission; creates a new shared comment, never mutates the private original.
- `POST /comments/{id}/redact`: expected version, reason; placeholder remains.
- `GET /mentions?q=&project_id=&visibility=`: typed, bounded eligible results.
- Files use shared contracts; download rechecks parent visibility at request time.

Events: comment.created/edited/published/redacted, mention.added. F08 queues
notifications after commit and rechecks recipient access at send. Idempotent retry
never duplicates comments or pings. Editing a reply does not reset execution claims
or task metadata versions; the discussion has its own sequence/version.

## Acceptance and migration

1. External comment works through browser and scoped API, and staff can reply;
   internal notes and their counts never appear in customer responses.
2. @ selection works with keyboard, duplicate names and renamed/deleted entities;
   task citations are never resolved as message recipients.
3. XSS Markdown, malicious links, file MIME mismatch, cross-project attachment IDs
   and mention-based access escalation are rejected or safely rendered.
4. Network failure preserves draft; retry with same key produces one comment.
5. A newly added mention notifies once; editing old text does not notify everyone.
6. Revoked access prevents pending notification and attachment download.
7. Migrate existing comments as internal, preserving IDs/authors/order and task
   histories. Verify shared/internal composer labels at390px and desktop.

Audit metadata is retained even when content must be redacted. Monitor failed
uploads and notification fan-out without copying comment bodies into logs.
