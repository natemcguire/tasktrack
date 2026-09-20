# Tasktrack JavaScript client and components

Node 20+. The package is in this repository and is not published to npm. From your app, install a local checkout with `npm install /path/to/tasktracker/sdk`, or copy the small modules into your project. Run `npm test` inside this directory to test the client.

## Server credentials

Create a workspace-owned read-only app at `https://tasks.eastbayprojects.com/apps`. A workspace administrator chooses explicit projects and approves with a passkey. Save the one-time client secret in your server's secret store. Rotating or revoking the app immediately invalidates its access tokens.

```js
import { TasktrackClient } from '@tasktrack/client';
const tasks = new TasktrackClient({
  clientId: process.env.TT_CLIENT_ID,
  clientSecret: process.env.TT_CLIENT_SECRET,
});
const board = await tasks.board(3);
for await (const task of tasks.allTasks({ project_id: 3 })) {
  console.log(task.title);
}
```

The client obtains and caches 15-minute access tokens, coalesces concurrent exchanges and retries authentication once after a 401. It supports projects, project, tasks, task, board, comments, attachments (metadata), events and health. `notes.read` is required for comments and files. `disconnect()` clears the in-memory credential; revoke the app in Tasktrack to invalidate it remotely. No write methods or browser credentials are supported.

## Browser components

Serve `components.js` as a static asset. Your authenticated backend must authorize the viewer and return Tasktrack JSON from a fixed, permitted project. Never expose an arbitrary authenticated proxy or pass the app credential to the browser.

```html
<script type="module" src="/assets/components.js"></script>
<tasktrack-tasks src="/internal/tasks"></tasktrack-tasks>
<tasktrack-tasks src="/internal/board" tasks-src="/internal/tasks" view="board"></tasktrack-tasks>
```

The source URL must be same-origin. Task lists follow opaque `cursor` values using a Next page button. Boards render configured columns or default phase buckets. Set `tasks-src` to your same-origin task-list endpoint to load additional pages within each column. The backend must forward `cursor`, `view=summary`, and the selected `column_id` or `status`, while pinning the project and any other filters to match its board request. Without `tasks-src`, truncated columns display a notice. Titles and source content render as text. Components emit `tasktrack-loaded` and `tasktrack-error` events. Styling uses `--tasktrack-text`, `--tasktrack-column`, and `--tasktrack-card`.

## Runnable internal dashboard

Set `TT_CLIENT_ID`, `TT_CLIENT_SECRET`, `TT_PROJECT_ID`, and a `DASHBOARD_PASSWORD` of at least 16 characters in your private environment, then run:

```sh
node sdk/examples/dashboard.mjs
```

Open `http://127.0.0.1:7790` and sign in as `internal` using that password. Optional `TT_URL` selects another Tasktrack origin; `PORT` changes the listener. The example protects all routes, pins a single project, and keeps credentials on the server. It is a local integration example; a production host should provide HTTPS and your organization's own viewer authentication and authorization.

See [internal app architecture](../docs/internal-apps.md) for API pagination, event polling, cache isolation and attachment handling.
