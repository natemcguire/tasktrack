# Display Tasktrack work inside internal apps

The hosted REST API supports task lists, project boards, task details, comments, attachments and change events. Internal apps should render these responses in their own UI. Tasktrack currently blocks iframe embedding and does not provide cross-origin browser CORS, an embeddable widget, app-only client credentials or an end-user SSO authorization-code flow.

## Recommended architecture

The internal app authenticates its users with its existing login. Its backend calls Tasktrack with a dedicated project-scoped read-only agent grant and returns only data its signed-in viewer may access. Store tokens only on the backend. Never put an owner token in browser JavaScript, local storage, URLs or public environment variables.

A shared integration token represents its approving owner, not the internal app's viewer. Tasktrack cannot infer that viewer's identity. The app must authorize every viewer for every project before returning data. Do not expose an unrestricted proxy or accept arbitrary Tasktrack paths from the browser. If viewers have different Tasktrack permissions, use separate per-user grants or enforce an explicit equivalent access policy in the app.

## Provision read-only access

Use the device enrollment API or the CLI:

```sh
tt auth login --url https://tasks.eastbayprojects.com \
  --workspace WORKSPACE_ID --name 'SailScan internal dashboard' \
  --project PROJECT_ID --capability project.read --capability work.read \
  --owner-email OWNER_EMAIL
```

A human reviews the exact projects/capabilities and approves with a passkey. Repeat `--project` only for projects this app needs. Add `notes.read` only when displaying comments/files. The grant cannot exceed the owner's current permissions. Admin-only membership endpoints are unnecessary for rendering tasks.

The CLI stores and refreshes credentials privately. A custom backend implements the same [device and refresh protocol](agent-authorization.md): 15-minute access tokens, rotating refresh tokens, and a 30-day maximum grant. Serialize refreshes, store the replacement refresh token atomically, and require reconnect after expiry, revocation or an uncertain refresh response. This is currently a human-owned installation grant, not a permanent machine account.

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

For example, this code runs on the app server after its own viewer authorization. `tokenStore.validAccessToken()` represents the app's protected credential store with serialized refresh; it is not a Tasktrack SDK function.

```js
async function loadProjectTasks(projectId, tokenStore) {
  const token = await tokenStore.validAccessToken();
  const url = new URL('/api/v1/tasks', 'https://tasks.eastbayprojects.com');
  url.searchParams.set('project_id', String(projectId));
  url.searchParams.set('limit', '50');
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    redirect: 'error',
    cache: 'no-store',
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(`Tasktrack returned ${response.status}`);
  return response.json();
}
```

For near-live display, poll filtered task lists or the event feed and refetch affected tasks. Events use `source`/`after`, distinct from list pagination. Advance the returned cursor even on an empty filtered page and drain `has_more`; a global page can contain no matching project events. Obtain `instance_id` from `/api/v1/health`. There is no WebSocket or webhook subscription for this integration yet.

Treat Markdown and source content as untrusted: render plain text or sanitize rendered HTML. Proxy authorized attachment downloads through the app backend if necessary; do not add bearer tokens to download URLs. Handle 401 by reconnecting when refresh is unavailable, 403 as insufficient access, and 429 using retry/backoff. Remove cached access immediately when the integration is disconnected.

## Current boundaries

The REST data endpoints and scoped authentication are deployed. A reusable frontend widget, per-user SSO, app-owned service identities, JavaScript token-refresh SDK, and push updates are not implemented. These can build on this API without exposing broad credentials to the browser.
