# Provider imports and agent access roadmap

Prepared September 20, 2026 from Nate's request. The implementation is now in this checkout; see the implementation documents for current behavior, limits and configuration. Live provider applications and personal phone delivery require account setup.

## Delivery order

Prioritize scoped agent credentials, phone enrollment and action approvals. Implement the reusable import foundation alongside that work when capacity permits, then Jira, Trello, Basecamp and generic Kanban files. Notification delivery extends the existing F08 work; configured board rendering remains owned by F04 task 105.

The implementation details and acceptance tests are in [agent authorization](agent-authorization.md) and [provider imports](imports.md). All ten new tickets were created and read back in production in **nate’s workspace → EBPBIZ**, under the existing Access, Work and communication, and Agent platform epics. Task IDs are workspace-scoped; switch to that workspace before opening these URLs.

| Task | Implementation slice | Depends on |
| --- | --- | --- |
| [106](https://tasks.eastbayprojects.com/tasks/106) | Persist scoped agent credentials and revocation | Existing F01/F17 policy foundation |
| [107](https://tasks.eastbayprojects.com/tasks/107) | Device enrollment and fresh human passkey approval | 106 |
| [108](https://tasks.eastbayprojects.com/tasks/108) | Single-use approval bound to the exact destructive action | 107; coordinate F18 runner |
| [109](https://tasks.eastbayprojects.com/tasks/109) | Verified channels and iMessage feasibility | 107; extend existing F08 |
| [110](https://tasks.eastbayprojects.com/tasks/110) | Import engine and native historical ingestion | Extends F21 |
| [111](https://tasks.eastbayprojects.com/tasks/111) | Import connection, preview, mapping and report UI/API | 110 |
| [112](https://tasks.eastbayprojects.com/tasks/112) | Jira Cloud OAuth adapter | 110, 111 |
| [113](https://tasks.eastbayprojects.com/tasks/113) | Trello with complete comment history | 110, 111 |
| [114](https://tasks.eastbayprojects.com/tasks/114) | Basecamp to-dos and card tables | 110, 111 |
| [115](https://tasks.eastbayprojects.com/tasks/115) | Generic Kanban CSV/JSON mapping | 110, 111 |

Each ticket contains scope, existing-feature coordination, concrete acceptance criteria and dependency IDs. Ticket acceptance remains separate from code implementation and requires the relevant live-provider checks. New high-priority items are 106–108.

## September migration and source cleanup

The completed migration required no application code changes. Existing APIs plus external scripts imported 53 projects, 805 issues, 609 comments and 50 original attachments, with 974 source history entries preserved in archives. Original four native projects remained untouched.

After a fresh source-delta check and archive validation, all 53 migrated live Jira projects were moved to recoverable trash using `enableUndo=true`. Both eastbayprojects.atlassian.net and sailscan.atlassian.net were verified to have zero live/archived projects and every migrated project in trash. Existing previously trashed projects were not permanently purged. This did not delete Atlassian accounts, billing or global settings. Jira retains trashed projects for 60 days before permanent deletion; this work intentionally retained that recovery window.

Durable local evidence:

- `/Users/nate/Library/Application Support/Tasktrack Migrations/2026-09-20-All-Jira/MIGRATION-AUDIT.md`
- `/Users/nate/Library/Application Support/Tasktrack Migrations/2026-09-20-All-Jira/final-report.json`
- `/Users/nate/Library/Application Support/Tasktrack Migrations/2026-09-20-All-Jira/jira-trash-receipts.json`
- `/Users/nate/Library/Application Support/Tasktrack Migrations/2026-09-20-SailScan/`

[Atlassian project trash and recovery](https://support.atlassian.com/jira-service-management-cloud/docs/trash-and-restore-a-project/) documents the recovery period. These backups and source exports contain private customer data and must not be committed to the repository.

## Decisions before release

Implemented credential lifetimes are 15 minutes for access and 30 days for a rotating grant; uncertain refresh responses require re-enrollment. iMessage delivery needs a supported consent/enrollment route before it can be promised as a product channel. Initial Basecamp coverage must enumerate unsupported tools. Provider connection buttons remain unavailable until credentials are configured. Denial, retry, reconciliation and phone-browser tests cover the implemented local flows. Protected external actions require credentials controlled by the approval gateway; a prompt alone cannot enforce approval.
