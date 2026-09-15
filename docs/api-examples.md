# Executed API examples

Captured from a disposable running Tasktrack instance. All data is synthetic.
The port, UUIDs, IDs and versions below are actual values from that run.
Use your server URL and returned IDs when reproducing it.

[Full unabridged transcript](api-transcript.json). Regenerate with
`python3 tests/capture_api.py`. This script asserts retry equality, rejection of
stale writes, handoff ownership, review/completion, and exact upload/download bytes.

## Instance identity

```sh
curl -sS -X GET http://127.0.0.1:63453/api/v1/health \
  -H 'X-Actor: nate' \
  -H 'X-Via: api'
```

HTTP **200** (selected fields for task records):

```json
{
  "instance_id": "36fb2c49-c4ac-49ba-b694-10eddf4cde1c",
  "schema_version": 2,
  "version": "1.0.0"
}
```

## Create project

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/projects \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: create-project' \
  -d '{"key": "HBR", "name": "Harbor", "brief_markdown": "# Reliable checkout\nOne order and receipt for every checkout, including retries."}'
```

HTTP **201** (selected fields for task records):

```json
{
  "aliases": [
    "HBR"
  ],
  "brief_markdown": "# Reliable checkout\nOne order and receipt for every checkout, including retries.",
  "created_at": "2026-09-15T16:05:35.976Z",
  "created_by": "nate",
  "created_via": "api",
  "document_links": [],
  "id": 1,
  "key": "HBR",
  "name": "Harbor",
  "updated_at": "2026-09-15T16:05:35.976Z",
  "updated_by": "nate",
  "updated_via": "api",
  "url": "http://127.0.0.1:63453/projects/HBR",
  "version": 1
}
```

## Create epic

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: create-epic' \
  -d '{"project_id": 1, "kind": "epic", "title": "Checkout reliability", "description_markdown": "Recover disconnected checkouts.", "acceptance_criteria": ["All retry cases pass."], "assignee": "nate"}'
```

HTTP **201** (selected fields for task records):

```json
{
  "id": 1,
  "reference": "HBR-1",
  "title": "Checkout reliability",
  "status": "backlog",
  "assignee": "nate",
  "execution": null,
  "version": 1,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Create task

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: task-create-001' \
  -d '{"project_id": 1, "kind": "task", "parent_id": 1, "title": "Make checkout retries safe", "description_markdown": "A disconnected client retries checkout. Preserve the original order and receipt.", "acceptance_criteria": ["Repeating a checkout request creates one order.", "A retry returns the original receipt."], "assignee": "codex@harbor", "priority": "high", "dependency_ids": []}'
```

HTTP **201** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HBR-2",
  "title": "Make checkout retries safe",
  "status": "backlog",
  "assignee": "codex@harbor",
  "execution": null,
  "version": 1,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Retry task creation

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: task-create-001' \
  -d '{"project_id": 1, "kind": "task", "parent_id": 1, "title": "Make checkout retries safe", "description_markdown": "A disconnected client retries checkout. Preserve the original order and receipt.", "acceptance_criteria": ["Repeating a checkout request creates one order.", "A retry returns the original receipt."], "assignee": "codex@harbor", "priority": "high", "dependency_ids": []}'
```

HTTP **201** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HBR-2",
  "title": "Make checkout retries safe",
  "status": "backlog",
  "assignee": "codex@harbor",
  "execution": null,
  "version": 1,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Rename project

```sh
curl -sS -X PATCH http://127.0.0.1:63453/api/v1/projects/1 \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: rename-project' \
  -d '{"expected_version": 1, "key": "HARBOR", "name": "Harbor checkout"}'
```

HTTP **200** (selected fields for task records):

```json
{
  "aliases": [
    "HARBOR",
    "HBR"
  ],
  "brief_markdown": "# Reliable checkout\nOne order and receipt for every checkout, including retries.",
  "created_at": "2026-09-15T16:05:35.976Z",
  "created_by": "nate",
  "created_via": "api",
  "document_links": [],
  "id": 1,
  "key": "HARBOR",
  "name": "Harbor checkout",
  "updated_at": "2026-09-15T16:05:35.981Z",
  "updated_by": "nate",
  "updated_via": "api",
  "url": "http://127.0.0.1:63453/projects/HARBOR",
  "version": 2
}
```

## Resolve earlier reference

```sh
curl -sS -X GET http://127.0.0.1:63453/api/v1/tasks/HBR-2 \
  -H 'X-Actor: nate' \
  -H 'X-Via: api'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "backlog",
  "assignee": "codex@harbor",
  "execution": null,
  "version": 1,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Claim task

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/claim \
  -H 'X-Actor: codex@harbor' \
  -H 'X-Via: api' \
  -H 'X-Session: checkout-1' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: task-claim-001' \
  -d '{"expected_version": 1}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "in_progress",
  "assignee": "codex@harbor",
  "execution": {
    "actor": "codex@harbor",
    "claimed_at": "2026-09-15T16:05:35.983Z",
    "session": "checkout-1"
  },
  "version": 2,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Retry claim after version advanced

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/claim \
  -H 'X-Actor: codex@harbor' \
  -H 'X-Via: api' \
  -H 'X-Session: checkout-1' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: task-claim-001' \
  -d '{"expected_version": 1}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "in_progress",
  "assignee": "codex@harbor",
  "execution": {
    "actor": "codex@harbor",
    "claimed_at": "2026-09-15T16:05:35.983Z",
    "session": "checkout-1"
  },
  "version": 2,
  "checkpoint": null,
  "result": null,
  "completion": null
}
```

## Reject changed request key reuse

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/claim \
  -H 'X-Actor: codex@harbor' \
  -H 'X-Via: api' \
  -H 'X-Session: checkout-1' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: task-claim-001' \
  -d '{"expected_version": 2}'
```

HTTP **409** (selected fields for task records):

```json
{
  "error": {
    "code": "idempotency_conflict",
    "fields": {},
    "message": "This request key was already used for different content or caller context."
  }
}
```

## Reject stale edit

```sh
curl -sS -X PATCH http://127.0.0.1:63453/api/v1/tasks/2 \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: reject-stale-edit' \
  -d '{"expected_version": 1, "title": "A stale title"}'
```

HTTP **409** (selected fields for task records):

```json
{
  "error": {
    "code": "version_conflict",
    "current_url": "http://127.0.0.1:63453/tasks/2",
    "current_version": 2,
    "fields": {},
    "message": "Record 2 changed. Fetch the current record before retrying."
  }
}
```

## Save checkpoint

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/checkpoint \
  -H 'X-Actor: codex@harbor' \
  -H 'X-Via: api' \
  -H 'X-Session: checkout-1' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: save-checkpoint' \
  -d '{"expected_version": 2, "checkpoint": {"summary": "Retry handling is implemented; the disconnect regression remains.", "next_action": "Add a regression for a retry after the connection closes.", "workspace": "/work/harbor", "branch": "fix/checkout-retries", "acceptance_remaining": ["A retry after a disconnect creates no second order."], "evidence": [{"label": "Current test run", "uri": "file:///work/harbor/artifacts/retry-tests.txt"}]}}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "in_progress",
  "assignee": "codex@harbor",
  "execution": {
    "actor": "codex@harbor",
    "claimed_at": "2026-09-15T16:05:35.983Z",
    "session": "checkout-1"
  },
  "version": 3,
  "checkpoint": {
    "acceptance_remaining": [
      "A retry after a disconnect creates no second order."
    ],
    "branch": "fix/checkout-retries",
    "commit": "",
    "evidence": [
      {
        "label": "Current test run",
        "uri": "file:///work/harbor/artifacts/retry-tests.txt"
      }
    ],
    "next_action": "Add a regression for a retry after the connection closes.",
    "not_applicable_reason": "",
    "summary": "Retry handling is implemented; the disconnect regression remains.",
    "workspace": "/work/harbor"
  },
  "result": null,
  "completion": null
}
```

## Handoff to another actor

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/handoff \
  -H 'X-Actor: codex@harbor' \
  -H 'X-Via: api' \
  -H 'X-Session: checkout-1' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: handoff-to-another-actor' \
  -d '{"expected_version": 3, "checkpoint": {"summary": "Retry handling is implemented; the disconnect regression remains.", "next_action": "Add a regression for a retry after the connection closes.", "workspace": "/work/harbor", "branch": "fix/checkout-retries", "acceptance_remaining": ["A retry after a disconnect creates no second order."], "evidence": [{"label": "Current test run", "uri": "file:///work/harbor/artifacts/retry-tests.txt"}]}, "to": "nate"}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "in_progress",
  "assignee": "nate",
  "execution": null,
  "version": 4,
  "checkpoint": {
    "acceptance_remaining": [
      "A retry after a disconnect creates no second order."
    ],
    "branch": "fix/checkout-retries",
    "commit": "",
    "evidence": [
      {
        "label": "Current test run",
        "uri": "file:///work/harbor/artifacts/retry-tests.txt"
      }
    ],
    "next_action": "Add a regression for a retry after the connection closes.",
    "not_applicable_reason": "",
    "summary": "Retry handling is implemented; the disconnect regression remains.",
    "workspace": "/work/harbor"
  },
  "result": null,
  "completion": null
}
```

## Recover context from brief

```sh
curl -sS -X GET 'http://127.0.0.1:63453/api/v1/brief?project_id=1' \
  -H 'X-Actor: nate' \
  -H 'X-Via: api'
```

HTTP **200** (selected fields for task records):

```json
{
  "has_more": false,
  "instance_id": "36fb2c49-c4ac-49ba-b694-10eddf4cde1c",
  "items": [
    {
      "assignee": "nate",
      "blockers": [],
      "checkpoint": null,
      "execution": null,
      "id": 1,
      "kind": "epic",
      "reference": "HARBOR-1",
      "result": null,
      "status": "backlog",
      "thread_links": [],
      "title": "Checkout reliability",
      "updated_at": "2026-09-15T16:05:35.977Z",
      "url": "http://127.0.0.1:63453/tasks/1",
      "version": 1
    },
    {
      "assignee": "nate",
      "blockers": [],
      "checkpoint": {
        "acceptance_remaining": [
          "A retry after a disconnect creates no second order."
        ],
        "branch": "fix/checkout-retries",
        "commit": "",
        "evidence": [
          {
            "label": "Current test run",
            "uri": "file:///work/harbor/artifacts/retry-tests.txt"
          }
        ],
        "next_action": "Add a regression for a retry after the connection closes.",
        "not_applicable_reason": "",
        "summary": "Retry handling is implemented; the disconnect regression remains.",
        "workspace": "/work/harbor"
      },
      "execution": null,
      "id": 2,
      "kind": "task",
      "reference": "HARBOR-2",
      "result": null,
      "status": "in_progress",
      "thread_links": [],
      "title": "Make checkout retries safe",
      "updated_at": "2026-09-15T16:05:35.987Z",
      "url": "http://127.0.0.1:63453/tasks/2",
      "version": 4
    }
  ],
  "next_cursor": null,
  "project_total": 2,
  "total": 2
}
```

## Claim handed off work

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/claim \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'X-Session: review-prep' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: claim-handed-off-work' \
  -d '{"expected_version": 4}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "in_progress",
  "assignee": "nate",
  "execution": {
    "actor": "nate",
    "claimed_at": "2026-09-15T16:05:35.990Z",
    "session": "review-prep"
  },
  "version": 5,
  "checkpoint": {
    "acceptance_remaining": [
      "A retry after a disconnect creates no second order."
    ],
    "branch": "fix/checkout-retries",
    "commit": "",
    "evidence": [
      {
        "label": "Current test run",
        "uri": "file:///work/harbor/artifacts/retry-tests.txt"
      }
    ],
    "next_action": "Add a regression for a retry after the connection closes.",
    "not_applicable_reason": "",
    "summary": "Retry handling is implemented; the disconnect regression remains.",
    "workspace": "/work/harbor"
  },
  "result": null,
  "completion": null
}
```

## Submit for review

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/submit \
  -H 'X-Actor: nate' \
  -H 'X-Via: api' \
  -H 'X-Session: review-prep' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: submit-for-review' \
  -d '{"expected_version": 5, "result": {"summary": "Disconnected retries preserve the original order and receipt.", "evidence": [{"label": "Retry regression", "uri": "https://example.org/harbor/runs/43"}]}}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "review",
  "assignee": "nate",
  "execution": null,
  "version": 6,
  "checkpoint": {
    "acceptance_remaining": [
      "A retry after a disconnect creates no second order."
    ],
    "branch": "fix/checkout-retries",
    "commit": "",
    "evidence": [
      {
        "label": "Current test run",
        "uri": "file:///work/harbor/artifacts/retry-tests.txt"
      }
    ],
    "next_action": "Add a regression for a retry after the connection closes.",
    "not_applicable_reason": "",
    "summary": "Retry handling is implemented; the disconnect regression remains.",
    "workspace": "/work/harbor"
  },
  "result": {
    "evidence": [
      {
        "label": "Retry regression",
        "uri": "https://example.org/harbor/runs/43"
      }
    ],
    "summary": "Disconnected retries preserve the original order and receipt."
  },
  "completion": null
}
```

## Accept completed work

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/complete \
  -H 'X-Actor: reviewer' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: accept-completed-work' \
  -d '{"expected_version": 6, "acceptance_note": "Both retry cases pass, and the receipt stays the same."}'
```

HTTP **200** (selected fields for task records):

```json
{
  "id": 2,
  "reference": "HARBOR-2",
  "title": "Make checkout retries safe",
  "status": "done",
  "assignee": "nate",
  "execution": null,
  "version": 7,
  "checkpoint": {
    "acceptance_remaining": [
      "A retry after a disconnect creates no second order."
    ],
    "branch": "fix/checkout-retries",
    "commit": "",
    "evidence": [
      {
        "label": "Current test run",
        "uri": "file:///work/harbor/artifacts/retry-tests.txt"
      }
    ],
    "next_action": "Add a regression for a retry after the connection closes.",
    "not_applicable_reason": "",
    "summary": "Retry handling is implemented; the disconnect regression remains.",
    "workspace": "/work/harbor"
  },
  "result": {
    "evidence": [
      {
        "label": "Retry regression",
        "uri": "https://example.org/harbor/runs/43"
      }
    ],
    "summary": "Disconnected retries preserve the original order and receipt."
  },
  "completion": {
    "acceptance_note": "Both retry cases pass, and the receipt stays the same.",
    "actor": "reviewer",
    "completed_at": "2026-09-15T16:05:35.993Z"
  }
}
```

## Append a review note

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/comments \
  -H 'X-Actor: reviewer' \
  -H 'X-Via: api' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: append-a-review-note' \
  -d '{"body": "Accepted after checking the retry evidence."}'
```

HTTP **201** (selected fields for task records):

```json
{
  "actor": "reviewer",
  "body": "Accepted after checking the retry evidence.",
  "created_at": "2026-09-15T16:05:35.994Z",
  "id": 1,
  "session": null,
  "task_id": 2,
  "via": "api"
}
```

## Upload attachment

```sh
curl -sS -X POST http://127.0.0.1:63453/api/v1/tasks/2/attachments \
  -H 'X-Actor: reviewer' \
  -H 'Idempotency-Key: attachment-001' \
  -F 'file=@evidence.bin' -F 'comment_id=1'
```

HTTP **201** (selected fields for task records):

```json
{
  "actor": "reviewer",
  "comment_id": 1,
  "created_at": "2026-09-15T16:05:35.997Z",
  "download_url": "/api/v1/attachments/1/content",
  "filename": "evidence.bin",
  "id": 1,
  "media_type": "application/octet-stream",
  "session": null,
  "sha256": "7e1cf3183b35dbb213f76a580b214b356891ab6f87c5bdf74b03a0a0cfb93a84",
  "size": 47,
  "stored_name": "7e1cf3183b35dbb213f76a580b214b356891ab6f87c5bdf74b03a0a0cfb93a84",
  "task_id": 2,
  "via": "api"
}
```

## Download exact attachment bytes

```sh
curl -sS -X GET http://127.0.0.1:63453/api/v1/attachments/1/content
```

HTTP **200** (selected fields for task records):

```json
{
  "size": 47,
  "sha256": "7e1cf3183b35dbb213f76a580b214b356891ab6f87c5bdf74b03a0a0cfb93a84",
  "content_type": "application/octet-stream",
  "content_disposition": "attachment; filename=\"download\"; filename*=UTF-8''evidence.bin",
  "exact_bytes_equal": true
}
```
