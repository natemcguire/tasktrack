# Roles and access

## Role definitions

**Tenant admin** is the agency's internal super admin. It is distinct from a
deployment operator, who maintains infrastructure and has no normal in-app right
to browse tenant records. Any emergency support access requires a recorded,
time-limited reason and audit event.

**Internal billing** handles invoices, payments, expenses and financial reporting.
**Internal regular** does project work. **External regular** participates in
explicitly granted customer projects. Being a billing contact is an additional
project grant, not a broad internal role. An agent credential is a service identity
with capabilities intersected with its owner's membership and allowed projects.

## Default matrix

A check applies only to accessible projects. “Grant” means an explicit capability
granted by an admin, never a self-service toggle. No custom role builder in v1.

| Action / resource | Admin | Billing | Internal regular | External regular |
| --- | --- | --- | --- | --- |
| See projects open to all internal members | Yes | Yes | Yes | No |
| See a restricted internal project | Yes | Explicit project grant | Explicit project grant | No |
| See a customer project | Yes | Yes unless restricted | Yes unless restricted | Explicit project grant |
| Create projects / edit project settings | Yes | Grant | Grant | No |
| Edit tasks, epics, PRDs and dependencies | Yes | Yes | Yes | No; submit Comms/change requests |
| Claim, checkpoint, hand off and submit work | Yes | Yes | Yes | No |
| Accept completion / reopen | Yes | Grant | `project.manage` grant | Customer approval request only |
| Change board columns | Yes | Grant | `project.manage` grant | No |
| Read shared comments and files | Yes | Yes | Yes | Project grant + shared visibility |
| Post shared comments / Comms | Yes | Yes | Yes | Yes in granted projects |
| Read/post internal notes | Yes | Yes | Yes | No |
| Mention a person | Visible, eligible participants only | Same | Same | Same |
| Invite internal people / change internal roles | Yes | No | No | No |
| Invite external people to a project | Yes | Grant | Grant | No |
| Manage branding / provider credentials | Yes | No | No | No |
| Read token counts, internal time and delivery cost | Yes | Yes | Project totals; own time | No |
| Read compensation or individual hourly cost rates | Yes | Yes | No | No |
| Record own time / annotate own location | Yes | Yes | Yes | No |
| Correct others' time / approve expense | Yes | Yes | No | No |
| Submit own expense and receipt | Yes | Yes | Yes | No |
| Read tenant financial reports / export accounting | Yes | Yes | No | No |
| Draft and issue invoices / send reminders | Yes | Yes | No | No |
| Configure invoice surcharge on/off or rate | Yes | No | No | No |
| Read/pay an issued customer invoice | Yes | Yes | No by default | Explicit billing-contact grant |
| Refund, void an issued invoice, write off balance | Yes | Grant | No | No |
| Record/match settled cash | Yes | Yes | No | No |
| Approve scope/change request | Yes | Grant | `project.manage` grant | Named approver only |
| Start/cancel an approved agent job | Yes | Grant | `run.execute` grant | No; submit Comms |
| Publish an agent's customer-facing reply | Yes | Yes | Yes | No |
| Configure runners / Git access / execution policies | Yes | No | No | No |
| Export all tenant data / request deletion | Yes | No | No | No |

Internal regulars may see aggregated delivery costs but not the private labor-rate
inputs used to compute them. A tenant may disable `cost.read` for regulars; there
is no permission to turn an external membership into a cost reader. Financial
resources require finance capabilities even when a project is internally open.

## Grant records and invariants

`membership(id,user_id,tenant_id,kind,role,status,revision)`; `kind` is internal or
external. Role and kind combinations are constrained. An external membership
references a customer organization. Cross-customer service accounts require an
internal membership and explicit project scope; do not fake them as customers.

`project_grant(project_id,membership_id,access,can_view_invoices,can_approve_scope,
created_by,created_at,revoked_at)`; `access` is participant or manager for internal
grants, participant for external. A project defaults to `internal_access=all`;
`restricted` uses grants. An admin can always administer project access.

External access requires **both** active external membership and a live project
grant whose customer matches the project. Adding a customer organization to a
project does not invite all its employees. Removing a project grant immediately
removes access to related search results, notifications, documents and downloads.

Capabilities are declared in code and exposed by `/api/v2/me/capabilities`. Extra
grants are restricted to documented grantable capabilities and bounded projects.
`identity.manage`, `integrations.manage`, `surcharge.manage`, `tenant.export`, and
`runner.configure` remain admin-only. A token's scopes cannot exceed its issuer.

Last-admin removal or suspension returns 409. Promotion from external to internal
requires an explicit admin action, explanatory confirmation of its access impact,
and fresh sign-in. No email-domain-based automatic promotion or customer invite
may perform that promotion. Self-signup never joins a tenant based on domain.

## Required denial tests

Use two tenants, two unrelated customers, an internally restricted project, a
removed member, expired credentials and identical numeric task IDs. Attempt each
action via UI, raw API, CLI, bulk operation, search, notification, attachment,
share preview, report, export and runner callback. Test internal note counts and
mention suggestions for existence leaks. A hidden button is not test evidence.
