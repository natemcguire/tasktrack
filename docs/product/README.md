# Agency workspace design

**Status:** proposed product design, 16 September 2026. These documents describe
work to build; they do not imply the features are already available.

Tasktrack should let a small consulting team run a project from the first question
to the final payment. Customers get a quiet place to ask questions, see progress
and pay invoices. The team keeps scope, execution, costs and decisions together.

The application remains MIT-licensed. Any agency can create a tenant and configure
its own branding, customers, payment providers and agents. A deployment's names,
domains, bank details, credentials and customer assets belong in private
configuration and tenant storage, never in public source or fixtures.

## The product, in order

1. **A workspace for each agency.** Email magic links, internal roles, project
   access, customer organizations and explicit customer invitations.
2. **A branded customer portal.** Agency mark × customer logo, shared work,
   questions and permitted invoices. No internal cost or agent-log leakage.
3. **A flexible task board.** Any reasonable number of named columns, epics,
   PRDs, assignments, dependencies, claims and resumable handoffs.
4. **TRIAGE.** A newest-first list of relevant activity, with unread state,
   attention filters and a next action. Command-K searches accessible work.
5. **Conversations that become work.** Comments, files, typed @ mentions and
   a 📞 Comms question box. Convert to task preserves the conversation.
6. **Useful notifications.** In-app and email delivery for actionable changes,
   invoices and comments; preferences, batching and reliable retries.
7. **Agreed scope.** Versioned estimates, budgets, milestones, change requests
   and customer acceptance. A customer question is not an approved purchase.
8. **Honest delivery economics.** Task and epic tokens, API-equivalent cost,
   actual spend, subscription allocation, time and work-location evidence.
9. **Fixed-price invoicing.** Multiple invoices per project, line attribution,
   immutable issued documents and an admin-controlled surcharge per invoice.
10. **Payment and reconciliation.** Stripe checkout, wire/ACH instructions,
    partial payments, refunds, disputes and matching settled cash to invoices.
11. **Expenses and reporting.** Receipts, project allocation, cash collected,
    receivables, costs, contribution margin and visible unallocated amounts.
12. **An open interface.** Every application action has an authorized API/CLI
    path. QuickBooks Online is the initial accounting export/adapter target;
    no accounting connection is made during design.
13. **An optional third engineer.** Queued, isolated agent jobs, project-specific
    conversation continuity, Git checkouts, reviewable changes and recovery.
14. **Operational foundations.** Audit trail, exports, backups, retention,
    accessibility, abuse controls, migration and meaningful release checks.

## Keep the interface small

Internal navigation: **TRIAGE · Projects · Comms · Finance · Reports**. Settings
contains People, Branding, Integrations and Runners. Hide unavailable navigation
based on capabilities; enforce the same rules on the server.

Customer navigation: **Overview · Board · 📞 Comms · Invoices**, scoped to a
selected project. Invoices appear only for a designated billing contact. A simple
question composer says “Send question”; submission acknowledges receipt without
pretending a human or agent is already answering.

Start with fixed-price invoices and manual expense/time entry. Add one runner
executing one job at a time. Defer payroll, a general ledger, automatic tax filing,
live chat, custom role builders, arbitrary workflow scripts, and multi-agent
orchestration. Preserve extension points without putting them into the first UI.

## Design map and implementation tasks

Each row is an independently reviewable implementation task with a complete PRD.
Dependencies are feature IDs, not mutable task titles. The private hosted project
holds the actual assignments and tenant-specific decisions.

| ID | Feature PRD | Depends on | Delivery wave |
| --- | --- | --- | --- |
| F01 | [Tenant identity and authorization](prds/01-tenancy-access.md) | — | 1 |
| F02 | [People and invitations](prds/02-people-invitations.md) | F01 | 1 |
| F03 | [Branding and customer portal](prds/03-branding-portal.md) | F01, F02 | 1 |
| F04 | [Custom board workflows](prds/04-workflows.md) | F01 | 2 |
| F05 | [TRIAGE, search and keyboard access](prds/05-triage-search.md) | F01, F06 | 2 |
| F06 | [Comments, mentions and attachments](prds/06-discussions.md) | F01 | 2 |
| F07 | [Comms and conversion to tasks](prds/07-comms.md) | F04, F06 | 2 |
| F08 | [Notifications and email](prds/08-notifications.md) | F02, F06 | 2 |
| F09 | [Scope, budgets and approval](prds/09-scope-approvals.md) | F06 | 3 |
| F10 | [Token usage and cost attribution](prds/10-usage-cost.md) | F01 | 3 |
| F11 | [Time and work-location evidence](prds/11-time-location.md) | F01 | 3 |
| F12 | [Fixed-price invoices](prds/12-invoices.md) | F03, F09 | 4 |
| F13 | [Stripe payments and surcharge](prds/13-payments.md) | F12, F08 | 4 |
| F14 | [Bank payments and reconciliation](prds/14-bank-reconciliation.md) | F12 | 4 |
| F15 | [Expenses and receipts](prds/15-expenses.md) | F01 | 3 |
| F16 | [Cash, revenue and delivery reports](prds/16-reporting.md) | F10, F11, F12, F13, F14, F15 | 4 |
| F17 | [API, CLI and accounting contracts](prds/17-api-accounting.md) | F01 | 1–5 |
| F18 | [Runner queue and isolation](prds/18-runner-control.md) | F01, F10, F17 | 5 |
| F19 | [Agent provider adapters](prds/19-agent-providers.md) | F18 | 5 |
| F20 | [Project code and conversation continuity](prds/20-code-memory.md) | F18, F19 | 5 |
| F21 | [Migration, recovery and release](prds/21-operations.md) | F01; all enabled features for final release | Every wave |

F17 establishes the API conventions in wave 1; each feature owns its endpoints
and API tests as it ships. Accounting export requires F12–F16. F21 provides
migration/backup groundwork early, then verifies each enabled wave. These are
milestone dependencies, not a cycle requiring the whole product before work starts.

## Shared specifications

- [Architecture and data boundaries](architecture.md)
- [Role and permission matrix](permissions.md)
- [API, event and financial conventions](contracts.md)
- [Wireframe gallery](wireframes.html) — desktop/mobile, fictional data only
- [Provider research and qualification](research.md)

Every PRD incorporates these shared contracts. Its own sections define the
feature-specific data, APIs, screens, edge cases, migrations and acceptance tests.

## Definition of complete

A feature is complete when its permission matrix works through browser and API,
its failure/retry behavior is tested, existing data survives migration, and its
visual states are verified at desktop and phone sizes. Record the commit, test
evidence and remaining limitations on its task. Issuing an invoice, collecting
cash, publishing a customer reply and merging code are distinct actions with
distinct evidence.

The current app has owner/member workspaces, fixed board states and task share
links. It does **not** yet have customer role isolation, invoicing or agent runners.
Do not invite external customers into a current workspace containing internal
projects before F01/F02 are deployed and verified.

## Review and handoff

Open the [wireframe gallery](wireframes.html), read [verification notes](REVIEW.md),
and use the [handoff](HANDOFF.md) when another engineer continues the review.
