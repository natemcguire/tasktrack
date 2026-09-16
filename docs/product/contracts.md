# Shared implementation contracts

These conventions are normative for every feature PRD. Routes below are proposed
`/api/v2` contracts; current `/api/v1` stays supported during migration.

## Requests and identity

- A browser uses an HttpOnly secure session cookie, CSRF token and same-origin
  checks. A CLI/agent uses a hashed, revocable bearer credential. No passwords.
- The server resolves tenant and actor from the credential. Requests can assert
  `X-Workspace-ID` to catch stale tabs, but cannot select unauthorized storage.
- `Idempotency-Key` is required on POST/PATCH/DELETE commands. Receipt scope is
  `(tenant, principal, operation, key)`; same request hash returns the original
  status/body, different hash returns 409. Default receipts last 7 days; payment,
  invoice issue and conversion natural dedupe keys remain permanently unique.
- Update/delete commands include `expected_version`; mismatch returns 409 with
  current version and safe conflict summary. No last-write-wins financial edits.
- UUIDs identify new domain records; existing integer task IDs remain stable and
  gain public UUID aliases. References are `(instance_id, project_id, task_id)`.
- UTC RFC3339 timestamps are authoritative. Tenant timezone is an IANA identifier
  used only for display, reporting boundaries and invoice date selection.
- Errors: `{error:{code,message,field_errors?,request_id,retryable}}`; 401 expired
  identity, 403 denied action, 404 invisible resource, 409 conflict, 422 invalid
  input/state, 429 quota with Retry-After, 503 temporary provider outage.
- Lists return `{items,next_cursor,has_more,as_of}`. Default limit 50, maximum 200.
  Cursors are opaque, bind filters and auth scope, and use stable keyset ordering.
  Aggregates and counts include only authorized records. A 202 result returns an
  operation ID and status URL; it is not proof the email/payment/export happened.

Example command:

```http
POST /api/v2/projects/p_demo/comms
Authorization: Bearer <scoped-token>
Idempotency-Key: question-01
Content-Type: application/json

{"subject":"Can the export include dates?","body_markdown":"We need the date on each row."}
```

```json
{"id":"c_demo","reference":"DEMO-C12","status":"open","version":1,"url":"/comms/c_demo"}
```

## Domain events and external side effects

An event has `event_id`, monotonic tenant `sequence`, `schema_version`, `type`,
`aggregate_type/id/version`, `occurred_at`, principal, project/customer scope,
visibility, correlation/causation IDs and a minimal payload. Do not embed secrets,
raw IP addresses, complete transcripts or payment card data.

Domain mutation, audit record and outbox row commit together in tenant SQLite.
A dispatcher submits outbox IDs to Cloudflare Queues and marks dispatch attempts.
Scheduled recovery scans undispatched rows. Queues may redeliver or reorder; each
consumer deduplicates its own `(consumer,event_id)` and enforces state transitions.
Retry transient failures with jitter, cap attempts, and surface dead-letter work
in an authorized admin TRIAGE view. Keep payment reconciliation until resolved;
do not discard a financial obligation because automatic retries stopped.

External webhook handlers verify raw-body signatures, clock tolerances, provider
account and environment. Persist an inbox record before acknowledging 2xx. Resolve
tenant from configured provider account routing, never a caller's metadata alone.
Unknown valid accounts are quarantined. Consumer state records replay results.

GET `/events?after=...` returns scanned cursor, filtered events and `has_more`.
Clients drain pages even when an authorized filter yields an empty event list.
Delivery, reading, acknowledging and completing work are separate operations.

## Money and attribution

Invoice/payment amounts use integer minor units and ISO4217 currency. Quantities
and unit prices use decimal strings until rounding a line. No IEEE floating-point
arithmetic for money. Sum rounded lines; apply documented tax/discount/surcharge
rules in a fixed order. Store the rule version with the issued document.

API cost uses integer **micro-units** of currency so fractions of a cent are not
lost. Token rate is a decimal currency amount per million tokens with effective
dates and provenance. Separate input, cached input, cache write, output and other
provider-billed categories. Do not double-count cached input inside total input.

Each invoice has one project and customer, one currency and many lines. Lines
allocate net service value to tasks, epics or a project-level “unallocated” bucket.
Allocation weights must sum to 10,000 basis points. When converting to cents,
use largest remainders with stable ID tie-breaking so totals remain exact.

`payment` records gross receipt, fees, refund/dispute adjustments, settlement state
and external evidence. `payment_allocation` maps received principal to invoices.
Tax, surcharge and service principal stay separate. “Cash collected” means settled
customer receipts less settled refunds; “net bank cash” subtracts provider fees
and follows payouts. Moving money from processor balance to bank is a transfer,
not a second customer receipt. “Allocated service revenue” is a management measure
under the chosen cash/invoiced basis, not a claim of GAAP revenue recognition.

Issued invoice content is immutable. Corrections use void/reissue before payment,
credit notes, or append-only allocation corrections with reasons. No hard deletion
of financial records; archive only. Refunds never erase the original receipt.
Currency conversion, if needed later, requires dated FX evidence; v1 reports each
currency separately and rejects mixed-currency totals.

## Files, search and visibility

Files are R2 objects under a tenant prefix with digest, MIME, byte length, owner,
resource and visibility in metadata. Downloads reauthorize against the parent.
Uploads quarantine until type/size/content validation passes; unreferenced objects
expire after 24 hours. No arbitrary server-side fetch from a user-supplied logo URL.

Render Markdown through an allowlist; escape HTML, sanitize links and disallow
active scripts. Content is untrusted, including provider results and repository
instructions. An `internal` comment can never be made customer-visible with a
normal edit. Explicit publish creates a reviewed shared copy and an audit link.

Search has separate internal/shared indexes or equivalent filters applied before
ranking/counting. Notification snippets and OG previews are produced from explicit
shared fields. Revoked images can remain in third-party caches; the share UI states
that existing downloaded screenshots cannot be recalled.

## Limits and observability

Initial product limits: 2 MiB JSON request, 50,000 characters per description or
comment, 120-character work-item title, 10 MiB file, 2 MiB logo, 200 items per batch,
50 mentions per comment and 50 active board columns per project. These are
configurable deployment safety limits, not fixed UI column counts.

Record request IDs, latency, denied capability code, queue age, retry state and
adapter status without content or credentials. Make failures actionable in the
tenant UI. Availability targets, load fixtures and recovery drills are in F21.
