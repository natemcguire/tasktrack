# Design handoff

## Scope

This package is for human review before implementation. It contains a product
plan, architecture, shared contracts, permission matrix, 21 feature PRDs and an
interactive wireframe gallery. No feature is implemented by these documents.

Start at [the product index](README.md), then [permissions](permissions.md) and
[contracts](contracts.md). Each feature PRD lists its dependencies, screen links,
data/API design, failure behavior, migration and acceptance evidence. Review the
[wireframes](wireframes.html) as sample layouts, not functioning authorization,
payments or agent execution.

## Decisions to preserve

- Multi-tenant MIT software; deployment/customer identities, assets and financial
  configuration stay private. Integrations are tenant-configurable.
- Magic-link sign-in only. Internal admin/billing/regular and external roles.
- Projects default open to internal members; external grants are per person,
  per project. Restricted internal projects remain possible.
- Fixed-price invoices, multiple per project; admin-controlled surcharge per
  invoice with validated rules and explicit customer disclosure.
- QuickBooks Online only as the initial accounting target; disconnected first.
- Comms is asynchronous, retains history on conversion, and does not authorize
  spending or automatic customer-facing agent replies.
- Column labels/count are configurable, including fewer than four columns;
  execution phases and claims remain explicit.
- Observed/estimated tokens, API-equivalent cost, subscription spend, human time,
  runner time, invoice value and settled cash are separate measures.
- Human work declarations, network location and runner region are separate facts.
- Cloudflare hosts the app. A private runner is optional, sequential initially,
  with explicit approvals, budgets, credential isolation and recovery.

## Continue safely

1. Read the private hosted review project's current decisions and task comments.
   Do not copy tenant data into this repository or this public handoff.
2. Verify the latest main commit and current reservations before editing.
3. Review PRDs and wireframes with the product owners; record accepted changes.
4. Implement only an explicitly approved feature, respecting dependency waves.
5. Use F21's migration, tenant isolation, browser and recovery gates. Preserve
   current `/api/v1` contracts and stateful Cloudflare resources.

The private operational handoff records hosted project IDs, coordination schedules,
provider sources and in-progress verification. It is intentionally excluded from
Git. A continuation agent should request that private handoff if it is not present
in its workspace rather than guessing tenant, chat or payment-account identities.
