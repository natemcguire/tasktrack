# Client previews

Hosted boards and task pages prepare a 1200 × 630 PNG during browser idle time.
A sequential background queue keeps rendering and uploads off the navigation path.
The image includes only the task reference, title and status. The customer update
is entered separately when creating a share link and appears on the customer page.

One current image per workspace/task is persisted in D1. Its task version must
match before it can be shared; stale uploads cannot replace newer versions.
Saved share links retain their own immutable PNG in R2, so refreshing a task's
cache does not change an already-shared image. Revocation still gates both the
public page and its image.

The share dialog opens immediately. If preparation is unfinished it displays
“Generating client preview” with a spinner. Creating a link waits for preparation
and sends the summary and version, without uploading the image again or fetching
the full task a second time. Existing links load independently.

Preparation requires an open browser viewing the board or task. API-only changes
are picked up on the next view; there is no continuously running screenshot
browser. Data-saver mode skips speculative preparation and generates on demand.

API: authenticated `GET /api/v1/tasks/{id}/client-preview` returns the current
`version` and base64 `image`, or `version: null`. `PUT` accepts a 1200 × 630 PNG
as base64 `image` and `expected_version`. Share creation accepts
`use_cached_preview: true` in place of `image`; existing image uploads remain
supported for agents. All routes enforce the current workspace authorization.
