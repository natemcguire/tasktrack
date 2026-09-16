# F09 — Scope, milestones, budgets and change approval

**Wave:** 3 · **Dependencies:** F06 · **Review:** delivery lead + billing

## Outcome

The team and customer can point to the same agreed scope and price. A question or
agent suggestion does not silently enlarge a fixed-price commitment. Track small
milestones and versioned change requests without building contract-signing software.

## UX

[Scope wireframe](../wireframes.html#scope). Project → Scope lists approved
milestones with deliverable, acceptance criteria, amount/currency and target date.
Drafts are internal. “Request approval” shows an exact customer-visible preview
and names the approver. Customer can Approve or Request changes with a note.
The confirmation repeats scope version and total; stale pages cannot accept an
updated proposal. Label this project scope approval; do not claim a legally
qualified electronic signature or automatic contract execution.

Staff can draft a change request from a Comms item/task with reason, scope delta,
price delta and schedule impact. Until approved, the current commitment stays
unchanged. Rejected/withdrawn requests remain in history. Internal budget fields
show expected hours, AI spend and expense allowance separately from customer price.
Show budget burn warnings; do not automatically increase invoices when costs rise.

## Data and rules

`scope_version(id,project_id,number,status,summary,deliverables_json,currency,
price_minor,created_by,issued_at,approved_by,approved_at,content_digest)`;
`milestone(id,scope_version_id,title,acceptance_json,price_minor,target_date)`;
`change_request(id,project_id,base_scope_version_id,proposed_version_id,status,
source_comms_id,reason,schedule_delta)`;
`budget(project_id,task_id?,epic_id?,hours_decimal,ai_cost_micro,expense_minor,
currency,warning_thresholds,version)`.

Scope status draft→pending→approved/rejected/withdrawn; approved versions immutable.
Only one current approved scope. A new approval supersedes it through an explicit
link, never a destructive edit. A proposal's approval must still target the current
base version; concurrent approvals of divergent changes conflict409. Currency
cannot change after invoicing without a new project billing arrangement.

## API and access

- `POST /projects/{id}/scope-versions`: draft scope, milestone lines and price.
- `PATCH /scope-versions/{id}`: expected version; draft only.
- `POST /scope-versions/{id}/request-approval`: named eligible approver IDs,
  customer preview digest; returns202 notification operation.
- `POST /scope-versions/{id}/approve` or `/request-changes`: expected version,
  content_digest, comment; named approver or authorized internal manager only.
- `POST /projects/{id}/change-requests`: base version, proposed delta, source item.
- `GET/PATCH /projects/{id}/budget`: internal cost capability only.

Events: scope.approval_requested/approved/changes_requested/withdrawn,
change_request.created and budget.threshold_crossed. Threshold warnings at80/100%
are deduplicated per budget version and period. F08 handles delivery; a bounced
approval email does not approve the proposal. API agents may draft requests but
cannot impersonate customer approval or grant themselves authority.

## Billing boundary and edge cases

Fixed-price invoices may reference approved milestones; multiple invoices can
cover one project. Track cumulative invoiced service value against milestone price
and warn on overbilling. A manager may approve a documented exception rather than
forcing every invoice into a single milestone. Deposits are labeled deposits and
later application cannot double-count revenue. A milestone completion is delivery
evidence, not a payment receipt. Financial adjustments belong in F12/F13.

If a project is archived, existing approvals remain readable but new requests are
disabled. Revoked approvers cannot act on old links. Approval URLs authenticate
normally; no bearer-link GET approves scope. Changing an approved task's PRD must
record a scope-impact note and, if needed, a new change request.

## Acceptance

1. Customer sees only issued/approved scope, approves exact digest/version and
   cannot read internal budgets or drafts.
2. Concurrent divergent approvals leave one current scope and a clear conflict.
3. Editing a proposal invalidates old approval actions; retries do not duplicate.
4. Converted Comms/task changes do not automatically alter price or approved scope.
5. Multiple project invoices allocate to milestones without double-billing a
   deposit or silently exceeding committed value.
6. Budget warnings are internal, deduplicated and based on dated actual/estimated
   cost categories; customer price remains unchanged.
7. Browser and API approval/withdraw/reject flows preserve a complete audit history.

Migration creates no fabricated historical approvals. Existing project briefs stay
unapproved source material until a human explicitly issues the first scope version.
