# Task discussions

Use **Comments → Add comment** to start a discussion, or **Reply** under a
comment to continue its thread. Task headers include a Comments shortcut.
Replies stay grouped with the original comment; a reply can link to another
reply without creating ever-narrower mobile columns. Comments have permanent
anchors and preserve author, time, session and API/UI provenance.

```sh
tt task comment 42 --body "Please simplify the mobile controls."
tt task comment 42 --reply-to 7 --body "Updated. Ready for another look."
```

```http
POST /api/v1/tasks/42/comments
Authorization: Bearer <agent token>
Idempotency-Key: review-reply-1
Content-Type: application/json

{"body":"Updated. Ready for another look.","parent_id":7}
```

The server derives the author from the authenticated browser or agent token.
The parent must exist on the same task. Retry with the same idempotency key to
avoid duplicate replies. `GET /api/v1/tasks/42/comments` returns the usual paged
comment records, now including nullable `parent_id`. Older comments remain root
comments after the additive schema migration. Comments remain append-only.

Hosted task access still follows workspace membership. This change does not
implement external-customer project permissions or comment-email notifications.
Those remain separate reviewed features.
