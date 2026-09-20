# Display Tasktrack work inside internal apps

The hosted REST API supports task lists, project boards, task details, comments, attachments and change events. Internal apps should render these responses in their own UI. A server-side JavaScript SDK and reusable task-list/board web component are available in `sdk/`. Tasktrack blocks iframe embedding and does not provide cross-origin browser CORS or an end-user SSO authorization-code flow.

## Recommended architecture

The internal app authenticates its users with its existing login. Its backend calls Tasktrack with a workspace-owned, project-scoped read-only app credential and returns only data its signed-in viewer may access. Store tokens only on the backend. Never put an owner token in browser JavaScript, local storage, URLs or public environment variables.

A shared app token represents the application, not the internal app's viewer. Tasktrack cannot infer that viewer's identity. The app must authorize every viewer for every project before returning data. Do not expose an unrestricted proxy or accept arbitrary Tasktrack paths from the browser. If viewers have different Tasktrack permissions, use separate per-user grants or enforce an explicit equivalent access policy in the app.

## Provision read-only access

Open **Account → Internal apps** (`/apps`) as a workspace administrator. Name the app, choose its explicit projects, optionally include comments/files (`notes.read`), and confirm with a fresh passkey. Copy the client ID and one-time client secret into the app backend's secret store. Required scopes are `project.read` and `work.read`; app identities cannot write tasks or manage workspace settings.

These credentials belong to the workspace and survive removal of the creating administrator. They have no 30-day human grant expiry. Administrators can rotate or revoke them on `/apps`; both immediately invalidate previously issued access tokens. Lost secrets must be rotated. Scope changes require a new app.

Exchange credentials through JSON `POST /oauth/token` with `grant_type: "client_credentials"`, `client_id`, and `client_secret`. Access tokens last 15 minutes. There is no refresh token: exchange the client credentials again. The SDK caches short-lived access tokens and coalesces concurrent exchanges:

```js
import { TasktrackClient } from './sdk/client.js';
const tasktrack = new TasktrackClient({
  clientId: process.env.TT_CLIENT_ID,
  clientSecret: process.env.TT_CLIENT_SECRET,
});
// After your app authenticates and authorizes its viewer:
const board = await tasktrack.board(3, { limit: 20 });
const tasks = await tasktrack.tasks({ project_id: 3, limit: 50 });
```

Return the authorized result from a fixed same-origin backend endpoint. Serve `sdk/components.js` as a static asset and render:

```html
<script type="module" src="/assets/tasktrack-components.js"></script>
<tasktrack-tasks src="/internal/tasktrack/board" tasks-src="/internal/tasktrack/tasks" view="board"></tasktrack-tasks>
```

Omit `view="board"` for a paginated task list. The component renders text safely and never receives Tasktrack credentials. Set `tasks-src` to enable per-column pagination. The backend must preserve the board query filters and forward `cursor`, `view=summary`, and `column_id` or `status`; the runnable example implements this. See [the SDK guide](../sdk/README.md) for the runnable, authenticated dashboard example. The SDK is checked into this repository; it is not published to npm.

For tools acting as a particular human, use [device enrollment](agent-authorization.md) instead. Those grants remain bounded by their owner's permissions and 30-day lifetime.

## Display endpoints

| Display | Request |
| --- | --- |
| Accessible projects | `GET /api/v1/projects` |
| Resolve project key | `GET /api/v1/projects/by-key/SAIL` |
| Task list | `GET /api/v1/tasks?project_id=1&limit=50` |
| One column | `GET /api/v1/tasks?project_id=1&column_id=7&limit=50` |
| Board with column configuration | `GET /api/v1/board?project_id=1&limit=20` |
| Task detail | `GET /api/v1/tasks/42` |
| Comments | `GET /api/v1/tasks/42/comments` |
| Attachment metadata | `GET /api/v1/tasks/42/attachments` |
| Original file | `GET /api/v1/attachments/17/content` |
| Incremental changes | `GET /api/v1/events?source=INSTANCE_ID&after=0&project_id=1&limit=50` |

Every request sends `Authorization: Bearer ACCESS_TOKEN`. The token binds the workspace; overlapping numeric IDs in another workspace do not refer to the same records. Cache keys must include workspace and applicable viewer/grant scope. Do not share cached private responses across viewers.

Task lists return `items`, `next_cursor`, `has_more`, `total`, and `project_total`. Follow opaque `next_cursor` until `has_more` is false. Supported filters and task fields are in [the API reference](api.md). Boards return `configuration`, legacy phase `columns`, `epics`, and `configured_columns` keyed by column ID when custom columns are configured. Use `configuration.columns` for display order/names and the matching configured bucket; default boards use the phase buckets. Page full column contents through the task-list endpoint.

For near-live display, poll filtered task lists or the event feed and refetch affected tasks. Events use `source`/`after`, distinct from list pagination. Advance the returned cursor even on an empty filtered page and drain `has_more`; a global page can contain no matching project events. Obtain `instance_id` from `/api/v1/health`. There is no WebSocket or webhook subscription for this integration yet.

Treat Markdown and source content as untrusted: render plain text or sanitize rendered HTML. Proxy authorized attachment downloads through the app backend if necessary; do not add bearer tokens to download URLs. Handle 401 by checking app revocation or secret rotation, 403 as insufficient access, and 429 using retry/backoff. Remove cached access immediately when the integration is disconnected.

## Current boundaries

Workspace app credentials, the SDK, and task-list/board component are implemented. Per-user SSO, webhooks and push updates remain unavailable. The example dashboard is a runnable integration example, not an independently deployed production app. Your backend remains responsible for viewer authentication and project authorization.
