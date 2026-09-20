# Agent access and human decisions

Implemented September 20, 2026. Account settings links to `/agents` for enrollment, revocation, pending decisions and notification channels.

## Connect an agent

Run:

```sh
tt auth login --url https://tasks.eastbayprojects.com --workspace WORKSPACE_ID --name Codex --project PROJECT_ID --owner-email YOUR_EMAIL
```

Use repeated `--project` and `--capability` options to narrow access. The CLI displays a review link and short code. On a signed-in browser, enter the code, review scope, then confirm with a fresh user-verified passkey. If another workspace is selected, lookup automatically routes to the request’s workspace when your account has active internal membership and satisfies any named-owner restriction. Routing does not approve access; the review and passkey are still required. The page shows the signed-in email so account mismatches are visible. Register a passkey in Account settings first. The code pairs the agent and browser; the passkey provides human verification. A link visit never approves access.

The agent alone holds the private polling secret and receives credentials through the API. Tokens never appear in notification links. Access tokens last 15 minutes; rotating refresh credentials expire with the 30-day grant. Reused refresh credentials revoke the family. The CLI serializes refreshes and stores secrets in a mode-0600 file under a mode-0700 config directory. Lost credential responses require re-enrollment; there is no refresh replay grace.

Scopes intersect current membership and project policy. Owner revocation and membership changes invalidate access. Existing manual tokens retain their legacy policy and can be migrated through the new enrollment flow. `tt auth logout` removes local credentials; revoke the grant on `/agents` to invalidate it server-side.

## API

`POST /oauth/device_authorization` accepts JSON `workspace_id`, `name`, explicit `project_ids`, `capabilities`, and optional `owner_email`. It returns `device_code`, `user_code`, `verification_uri`, `expires_in` and `interval`. An explicitly named owner cannot be replaced by another member.

`POST /oauth/token` accepts JSON with `grant_type=urn:ietf:params:oauth:grant-type:device_code` and `device_code`, or `grant_type=refresh_token` and `refresh_token`. Pending, slow-down, denied, expired and invalid-grant responses follow device-flow semantics. Enrollment expires after 15 minutes; initial polling interval is five seconds.

Human lookup, challenge and decision routes are browser-only, session/CSRF protected and passkey-bound. Browser responses never return agent credentials. `/api/v1/agents` lists grants; `POST /api/v1/agents/{id}/revoke` revokes an owned grant.

## Destructive actions

Agents request typed `task.archive`, `task.reopen`, `task.restore`, `task.reassign` or `task.backlog` approvals through `POST /api/v1/approval-requests`, supplying `task_id`, `expected_version`, and a reason. The server constructs the immutable target/action manifest. A human reviews it and approves with a fresh passkey. The same agent family can execute through `POST /api/v1/approval-requests/{id}/execute` while authority, expiry and task version still match. Mutation and receipt consumption commit in one transaction; retries return the original outcome.

Reassignment requests include the exact `assignee` (or null to clear it). Reopening a parent also binds the parent version in the reviewed manifest. Direct bearer calls for these actions, including legacy tokens and board moves that resolve to a protected action, are rejected. CLI commands are under `tt approval`. Import rollback has its own passkey-bound review and unchanged-record checks. These controls protect these Tasktrack endpoints; they do not constrain arbitrary shell commands or independently held external service credentials. New destructive operations require explicit typed adapters and enforcement at their executor.

## Notifications and iMessage

Users can opt into email notifications and link phone channels on `/agents`. Phone setup requires a one-time code and fresh passkey verification. Enrollment and action requests send review links only to the owner's configured channels. Delivery does not approve anything.

Email uses the existing deployment email binding. SMS requires Worker secrets `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM`. iMessage uses a personal Mac bridge:

```sh
tt auth login --url https://tasks.eastbayprojects.com --workspace WORKSPACE_ID --name 'Mac message bridge' --project PROJECT_ID --capability notifications.deliver
tt message-bridge --url https://tasks.eastbayprojects.com
```

Use a separate protected `TT_CONFIG_DIR` for a dedicated bridge credential. The Mac must be signed into Messages and grant macOS Automation permission when requested. Start the bridge before verifying the iMessage phone number. It claims only its owner's queued messages, checks channel/lease validity immediately before sending, and records ambiguous sends without automatic retry. This personal Mac adapter is not a general cloud iMessage service. Provider SMS delivery and real Mac delivery require live setup; automated tests use a local outbox and never send real phone messages.

## Validation

Unit and hosted browser tests exercise scoped redemption, refresh reuse, owner binding, revocation, expiry, wrong-agent/owner denial, passkey decisions, direct-route bypass, single-effect execution, phone verification, removed-channel lease invalidation and phone layouts. Fresh passkey verification is required for approvals; agents cannot approve themselves.
