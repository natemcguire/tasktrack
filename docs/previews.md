# Private previews

Publish HTML design documents to a dedicated preview host on the same Cloudflare
Worker. Documents live in private R2 storage, not the public static asset bundle.
Set `PREVIEW_URL` to the HTTPS preview origin and add its custom-domain route to
your deployment configuration. Keep tenant deployment configuration outside Git.
The deployment script selects an ignored `wrangler.private.json` in the project
root when present; `TASKTRACK_CONFIG` can select another root-level config.
Keeping it in that directory lets Wrangler include the Python SDK.

Opening a preview uses the existing Tasktrack sign-in. Both hosts use separate
HttpOnly, Secure, host-only cookies. A browser-bound, single-use 60-second grant
connects the preview session to the original account session. Signing out or
revoking that session invalidates its preview sessions too. Documents and
handoffs use `Cache-Control: no-store`.

## Access

A preview belongs to one workspace and one project. Owners can read it. Other
readers must belong to that workspace and either have an explicit project
preview grant or belong to a project configured for team preview access.
The default is **owner only**. Access is checked again for every document request;
knowing its ID, changing a project ID, or forwarding a link is insufficient.

These grants control previews only. Tasktrack's existing owner/member workspace
model still controls task access. Do not invite external customers to an internal
workspace until full project-scoped task permissions ship.

## Management API

Use an owner browser session and `X-CSRF-Token`; existing agent tokens cannot
administer accounts. These endpoints inherit the account API's CSRF and origin
checks. No customer/provider information is required in source code.

```http
POST /api/account/preview-projects/42
Content-Type: application/json
X-CSRF-Token: <session csrf>

{"team_access": false, "emails": ["reviewer@example.com"]}
```

```http
POST /api/account/previews
Content-Type: application/json
X-CSRF-Token: <session csrf>

{"project_id": 42, "title": "Checkout review", "html": "<!doctype html><h1>Checkout</h1>"}
```

Returns `201` with an `id` and private `url`. Titles are limited to 120 characters;
HTML documents to 1 MiB. Publish a new document for a revision, then update the
associated task link. Revoke an old document with
`POST /api/account/previews/{id}/revoke` and an empty JSON object.

Documents render inside a sandboxed iframe. Inline prototype scripts/styles and
data images work; network requests, form submission, parent navigation, cookies,
storage access and embedded third-party frames are blocked. Bundle images as data
URLs. URL fragments select wireframe screens without changing their access scope.
Only upload trusted design artifacts; the sandbox is a boundary, not a sanitizer.

## Verification

`npm run test:hosted` exercises sign-in handoff, cross-workspace denial, restricted
project grants, team access, immediate revocation, and all 22 mobile wireframes.
Wrangler rewrites hosts in local development, so its test origins share a loopback
host with distinct cookies. Verify the separate production domains after deploy.
