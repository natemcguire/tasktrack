# F01 — Tenant identity and authorization

**Wave:** 1 · **Dependencies:** none · **Review:** product owner + security review

## Outcome and scope

An agency signs up with an email magic link, creates a private tenant, and safely
works alongside other agencies on the same installation. Staff see projects open
to all internal members; customers see only projects they were individually
invited to. Implement the [permission matrix](../permissions.md) as server policy.
Do not add passwords, domain-based auto-join or a custom role builder.

## User flow and wireframe

[Access screen](../wireframes.html#access). Signup asks for email; after verified
redemption ask for agency display name and timezone. If the person arrived through
an invitation, accept that invitation rather than silently creating another tenant.
An existing user can choose “Create another workspace.” Tenant switching clears
cached data, rotates CSRF state and replaces the current project route.

Project settings contain `Internal access: All internal members | Restricted`.
Default is All. A separate external-participant list always names each person;
“Customer organization” is not an access toggle. The UI labels internal-only
resources and customer-shared resources before edits. Empty state offers create
project to authorized staff and “No projects shared yet” to external members.

## Data and constraints

Extend D1 memberships with `id`, `kind`, `role`, `status`, `revision`, and optional
`customer_id`; backfill owner→admin and member→internal regular. Tenant SQLite
holds customer records and project grants. Add project `internal_access`, nullable
`customer_id`, and `visibility_revision`. An external grant must match that
project's customer; changing the customer blocks until existing external grants
are explicitly revoked. Existing content defaults internal.

Keep identity separate from membership. Suspending membership revokes tenant
access without deleting the user's other tenants. Retain historical actor IDs.
At least one active admin must remain. Internal/external conversion is explicit,
audited and invalidates credentials. A client-supplied role or tenant never affects
authorization. Implement a centralized policy function reused by Service methods,
Worker routing, exports, jobs and serializers; avoid ad hoc UI-only checks.

## API and events

All routes follow [shared contracts](../contracts.md).

| Operation | Request / result |
| --- | --- |
| `POST /tenants` | `{name,timezone}` after verified login; returns tenant/admin membership |
| `GET /me` | identity, accessible tenants, current membership and revision; no other tenant data |
| `GET /me/capabilities?project_id=` | effective documented capabilities and safe reasons |
| `PATCH /memberships/{id}` | expected version, role/status; admin-only; last-admin conflict409 |
| `PATCH /projects/{id}/access` | expected version, internal_access, complete internal grant list |
| `GET /projects/{id}/participants` | eligible visible participants; external responses exclude unrelated staff |

Events: tenant.created, membership.changed/suspended, project.access_changed.
Role changes revoke affected D1 sessions/tokens before success. If cross-store
cleanup fails, a pending revocation denies access until reconciliation finishes.
No externally visible success while an old capability remains active.

## Failure and migration behavior

Magic links remain single-use, scanner-safe and rate-limited. Wrong-workspace tabs
receive409 with a switch prompt. Removed users receive401/403 without a resource
snippet. Prevent repeated verification from creating duplicate tenants. Backfill
customer/project grants transactionally and validate counts before enabling invites.
Cached authorization keys include membership and visibility revision; queued work
rechecks current policy. A failed policy store read fails closed.

## Acceptance and evidence

1. Two tenants with overlapping old task IDs cannot read or mutate each other's
   tasks, files, shares, events, counts, reports or agent callbacks.
2. A new internal member sees open-internal projects, but not a restricted one;
   an external member sees neither until explicitly granted a customer project.
3. Removing a grant denies the next API request and queued email/file delivery.
4. Role escalation, spoofed actor headers and domain-based auto-join are rejected.
5. Concurrent last-admin removals leave one admin, with one request rejected.
6. Upgrade a populated owner/member fixture; IDs, claims and shares survive and
   all historical content stays internal. Re-run migration without duplicates.
7. Browser checks cover signup/invite entry, tenant switching, forbidden URL,
   keyboard focus and phone layout. Record API denial tests, not screenshots alone.

Release behind `customer_access_v2`; external invitations remain unavailable until
F02's tests pass. Monitor authorization errors by code and policy revision without
logging private content. A policy regression disables new external sessions first.
