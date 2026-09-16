# F21 — Migration, privacy, recovery and release evidence

**Wave:** every wave · **Dependencies:** F01 groundwork; all enabled features at release

## Outcome

Ship a reviewable, recoverable system whose public repository contains generic
software and whose hosted tenant contains private business configuration. Design
completion means PRDs/wireframes/tasks are ready for review; implementation completion
later requires tested behavior and production evidence. Do not conflate them.

## Privacy and configuration

Audit tracked source, docs, fixtures, generated screenshots and deployment files
for actual tenant/customer identities, hostnames, bank details, account/resource
IDs, credentials and private assets. Move installation-specific configuration to
ignored/private deployment storage; publish neutral templates with named variables.
No source-code branch checks an agency name to enable features. Verified email
sender, custom domain, resource IDs and integration account routing are configuration.

Preserve a private operational copy before changing deployment templates. Validate
the same Durable Object namespace, D1 and R2 bindings; do not recreate stateful
resources as part of cleanup. Existing public Git history may retain previously
published identifiers; inventory it privately and require an explicit decision
before history rewriting. Never falsely claim a HEAD cleanup erased history.

## Migration and backup protocol

Every migration has version, checksum, preconditions, backfill, validation query
and rollback/forward-recovery notes. Expand schema, deploy compatible code,
backfill, verify tenant counts/foreign keys/visibility, then enable feature flags.
All historical comments default internal. Preserve integer IDs, references,
claims, idempotency receipts, file digests and financial evidence.

Hosted export bundles include identities/membership references, tenant SQLite,
referenced R2 manifest, attachment digests, events and export schema version.
Credentials are excluded or exported separately encrypted under admin control.
Capture a consistent sequence boundary; track outbox/in-flight provider operations
so restore can reconcile rather than replay money/email blindly. Use signed
manifests and verify checksums before marking a backup complete.

Initial targets: daily full logical export, incremental/event recovery checkpoint
every15min where platform capabilities permit, RPO≤24h baseline and RTO≤4h after
an operator starts recovery. Do not promise15min RPO until a restore drill proves
all stores share that recoverable boundary. Keep30 daily backups initially with
tenant-configurable longer financial retention. Restore into an isolated test
tenant first, outbound email/payments/runners disabled, then verify counts/digests.

## Security and lifecycle

[Operations wireframe](../wireframes.html#operations). Admin sees audit history,
export/backup status, retention settings, failed operations and support access.
Audit logs record actor, action, target, version, reason and request ID, excluding
secrets/content/raw IP. Append-only corrections retain financial history.

Tenant offboarding revokes sessions/tokens/runner leases and stops new jobs first,
offers export, then applies deletion/retention policy. Legal retention exceptions
are explicitly configured; do not silently retain everything forever or delete
required evidence. Garbage collection checks parent references and retention
before deleting R2 objects. Customer access removal does not erase invoices.
Use quotas for signup, email, files, search and jobs; abusive customer input cannot
create unlimited background spending. Incident actions can disable an adapter or
tenant job queue without taking the board offline.

## Required evidence matrix

- Domain: permissions, versions, claims, custom phases, Comms conversion, money
  arithmetic, allocation and retention invariants with negative tests.
- Integration: real local D1/DO/R2, duplicate/out-of-order outbox/webhooks,
  provider timeouts, token expiry, rate limits and recovery.
- Browser:1440px desktop and390px phone, keyboard-only, screen reader labels,
  no overflow, contrast, long/empty/error/loading states; invoice PDF page renders.
- Isolation: two tenants/two customers/restricted internal project through every
  API, list/count/search/file/email/report/export/runner surface.
- Migration: populated v1 fixture upgrades, rerun idempotence and interrupted
  migration recovery, with exact before/after record/digest comparisons.
- Operations: restore drill, external side effects disabled during recovery,
  observed email delivery, payment sandbox receipts and runner cancellation proof.

Use independent expected outcomes, not tests that repeat implementation formulas.
Performance fixture:10k tasks,100k comments/events,1k invoices and100k usage rows
in one tenant. Warm p95 board<1s, search<500ms, writes<1s excluding external I/O;
record actual environment and regressions. Accessibility targets WCAG2.2 AA
where applicable; manual verification accompanies automated checks.

## Delivery and review gates

For each feature task: link PRD and wireframes, declare dependencies, record scoped
commit, meaningful test output and limitations. Product owner reviews design before
implementation starts. Billing owner reviews invoice/payment behavior in sandbox
before live credentials; admin reviews customer isolation before invites. Runner
qualification precedes provisioning/automation. A feature flag is not evidence.

Commit tenant-neutral design changes to main and keep private operations/handoff
details in private storage. The public handoff states exactly what is built versus
proposed. Scheduled coordination stores verified chat IDs/cursors privately and
must not imply continuous monitoring when the local scheduler is offline.
