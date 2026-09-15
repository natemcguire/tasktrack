# Tasktrack on Cloudflare

[tasks.eastbayprojects.com](https://tasks.eastbayprojects.com) runs the browser,
API, accounts, files and sign-in email on Cloudflare.

## Sign in and share

Enter your email. The link expires after 15 minutes and works once. Opening the
email link shows a Continue button; mail scanners cannot consume it by following
the URL. Your first sign-in creates a workspace. Browser sessions last 30 days.

**Account** lets the owner rename the workspace, invite a teammate, remove a
member and issue agent tokens. Each invitation works once and expires in 48 hours.
An agent token belongs to one workspace, expires after 90 days and can be revoked.
Members can work on tasks; account administration belongs to the owner.

On a task, choose **Share with customer**, write an update and create the link.
The customer page shows the task title, current status and your update. Its
1200 × 630 image is a snapshot made when you share. Internal descriptions,
comments, attachments, identities and agent checkpoints stay private.

Anyone with a customer link can read it. Revoke it from the same dialog to stop
serving both the page and image. A messaging app may retain an image it already
downloaded. Open Graph metadata supports rich previews in apps such as Messages;
the receiving app controls whether and when it displays them. See
[Apple’s rich preview guidance](https://developer.apple.com/documentation/technotes/tn3156-create-rich-previews-for-messages/).

## Use the CLI

Install `tt` as described in the [README](../README.md), then create a token under
**Account → Agent access**:

```sh
export TT_URL=https://tasks.eastbayprojects.com
export TT_TOKEN=your-token
export TT_SESSION=checkout-1
tt project list
tt brief --json
```

The existing commands and retry contracts apply. The server derives the actor
from the token (`agent:codex`, for example); `TT_ACTOR` cannot impersonate another
user. Unset `TT_URL` to use a local database. Local data is not automatically
uploaded, and local `serve`, `backup` and `restore` refuse a configured `TT_URL`.

## Storage and authorization

| Cloudflare service                  | Stores                                                              |
| ----------------------------------- | ------------------------------------------------------------------- |
| Python Worker + static assets       | Application, API and browser                                        |
| D1 `tasktrack-accounts`             | Accounts, memberships, hashed credentials, invitations, share links |
| SQLite Durable Object per workspace | Projects, tasks, comments, claims, events and retry receipts        |
| Private R2 `tasktrack-files`        | Attachments and share images, under workspace prefixes              |
| Email Service                       | Sign-in email from `hello@tasks.eastbayprojects.com`                |

Each task operation runs synchronously inside a Durable Object SQLite transaction.
The existing domain service applies its validation, version checks and claims.
Task IDs may overlap between workspaces; only authenticated membership chooses
the database. A client cannot choose another workspace by changing an ID or actor
header. Switching workspace rotates the session’s CSRF token to reject stale tabs.

Cookies are Secure, HttpOnly and SameSite=Lax in production. Authenticated browser
writes require a CSRF token; cross-origin form submissions are rejected. Login
links, sessions and API tokens are stored as hashes. Responses are not cached.
Invocation logging is disabled so magic links are not recorded in request logs.

Uploads are limited to 10 MiB. Sign-in requests are limited per email and IP;
shares, writes, invitations and token creation also have rate limits. An hourly
job removes expired credentials, invitations and old rate-limit counters.

## Develop and verify

From a source checkout with Node 24+, Python 3.12+ and uv:

```sh
npm ci
npm run dev:cloudflare
```

Open `http://127.0.0.1:8787`. Local sign-in messages go to a local D1 outbox:

```sh
npx wrangler d1 execute tasktrack-accounts --local \
  --command 'SELECT recipient, body FROM local_mail ORDER BY id DESC LIMIT 1'
```

The outbox requires both a loopback `PUBLIC_URL` and `LOCAL_EMAIL=1`; production
uses the email binding. Do not enable remote bindings for this development mode.

```sh
python3 -m unittest -v
python3 tests/mutation_checks.py
npx playwright install chromium
npm run test:browser
npm run test:hosted
```

The hosted test starts a disposable workerd process with real local D1, Durable
Object SQLite and R2. It uses two accounts, competing claims, uploads, invitations,
revocation, customer pages, mobile layout and the remote CLI. No external email is
sent. Results: [hosted verification](hosted-verification.json),
[sign-in](screenshots/hosted-signin.png), [sharing](screenshots/hosted-share.png),
[customer view](screenshots/hosted-customer-mobile.png).

## Deploy

The checked-in configuration targets the production account. Authenticate
Wrangler, then run:

```sh
npm run deploy:cloudflare
```

This applies D1 migrations before deploying the Worker and assets. Workspace
schemas migrate atomically on first access; newer schemas are refused by older
application code. Preserve migration history when updating the application.
Do not delete or recreate the `WORKSPACES` Durable Object namespace.

For another installation, change the account, custom domain, `PUBLIC_URL`, sender
address and resource IDs. Create a D1 database and private R2 bucket, and
[onboard the sending domain](https://developers.cloudflare.com/email-service/get-started/send-emails/)
before deploying. Cloudflare manages the custom domain’s DNS and HTTPS certificate.
Python Workers and Cloudflare Email Service currently use beta platform APIs.

Local backup commands cover the local installation only. For hosted recovery,
retain the D1 database, Durable Object namespace and R2 bucket together; use
Cloudflare’s database recovery tools and retain R2 objects. Rolling back application
code does not roll back database schemas or task data.
