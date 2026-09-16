# F17 — Agent API, CLI and QuickBooks Online boundary

**Wave:** 1–5 · **Dependencies:** F01; accounting export needs F12–F16

## Outcome

Every human workflow has a documented machine interface with identical authority,
validation and audit behavior. Agents can read context, draft changes, recover
work and perform explicitly granted actions. QuickBooks Online is the first
accounting target, initially export-only and disconnected.

## API/CLI completeness

Ship versioned OpenAPI for all F01–F21 endpoints, typed schemas, examples and error
codes. The [shared contracts](../contracts.md) govern identity, versions, receipts,
pagination, events and money. Maintain a feature/action→browser→API→CLI matrix;
release fails if an available UI action lacks an API equivalent. File upload,
invitations, invoice issue, approval, notification preferences and runner control
are included, not only task CRUD. Security-sensitive actions still require scopes.

CLI commands are composable: `tt comms create`, `tt invoice draft/issue`,
`tt usage ingest`, `tt expense submit`, `tt run start/status/cancel`, and
`tt export accounting`. Every command supports `--json`, nonzero errors,
`--request-id`, input files/stdin and explicit versions where relevant. A dry-run
returns exact validation/permission results without external side effects. Never
prompt interactively in noninteractive mode; return an actionable approval-required
error. Avoid secrets in arguments; prefer protected credential storage/env.

## Credentials and integrations UI

[Integration wireframe](../wireframes.html#integrations). Admin creates a service
identity with name, expiry, capabilities and project scope. Show secret once,
last-used metadata, revoke and rotate. Default read/write work, no invitations,
finance send/refund, access management or deployment. Effective permissions are
the intersection of scope, owning membership and project grants. Rotation permits
a short overlap; revocation is immediate and queued jobs recheck it.

Outbound webhooks subscribe to selected event types and projects; signed bodies
include event ID/schema version. Verify destination HTTPS and block private/
metadata IP ranges to prevent SSRF, including DNS rebinding checks. Retry with
idempotent delivery IDs and let admins inspect redacted failures. Do not expose
internal event payloads to an external-scoped integration.

## Accounting model and mappings

QuickBooks Online stays **Disconnected** until an admin explicitly connects it.
No credential collection in the initial design delivery. First implement a
deterministic export bundle: customers, invoices/lines, payments, credit notes,
expenses, taxes/fees and allocation metadata in CSV/JSON with schema version,
currency, source IDs, digests and a reconciliation summary. Mark it as an interchange
bundle, not a file guaranteed to import directly into every QBO screen.

Future adapter records `accounting_connection(tenant_id,provider,realm_id,
encrypted_oauth_ref,status,scopes,connected_by)` and
`external_mapping(local_type,local_id,provider,realm_id,external_id,sync_token,
last_digest,last_synced_at)`; unique per realm/entity. Map customer→Customer,
invoice→Invoice, receipt→Payment, credit→CreditMemo, approved expense→appropriate
Purchase/Bill chosen by payment status. Chart-of-account, tax code and item mappings
are finance-reviewed configuration. Do not infer an accounting account from a
free-text category or automatically create tax codes.

## Sync rules and APIs

`POST /accounting/exports`, `GET /accounting/exports/{id}`,
`GET/PATCH /accounting/mappings`, `GET /accounting/status`,
future admin connect/disconnect and `POST /accounting/sync-jobs`.
Tasktrack owns project/task allocation and source invoice intent; QBO owns its
accounting classifications once exported. Conflicting financial edits go to a
review queue; never last-write-wins. Respect provider concurrency tokens, OAuth
rotation, pagination and throttling. Store durable operation intent before network
calls, reconcile ambiguous timeouts through mappings/source IDs, and require a
human before adopting an externally edited invoice. Never implement bidirectional
silent deletion. [Intuit developer documentation](https://developer.intuit.com/app/developer/qbo/docs/get-started).

## Acceptance and stages

Stage A: OpenAPI lint/contract tests, complete action matrix, scoped credentials,
CLI workflows and dry-run tests. Existing v1 claims/idempotency/brief clients remain
compatible. Stage B: export golden fixture independently reconciles exact invoice,
payment, fee and expense totals with stable source IDs; repeat export is identical
at the same as_of sequence. Stage C (later explicit connection): QBO sandbox tests
two realms, duplicate sync, token expiry, conflict, partial failure and reconnect.

External/limited credentials must fail finance/access/admin actions even if they
know route names. Generated SDK/docs examples use fictional tenants and no real
tokens. Schema changes require compatibility notes and migration evidence. An API
capability does not grant an agent permission to use it without the owner's scope.
