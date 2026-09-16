# F15 — Expenses and receipts

**Wave:** 3 · **Dependencies:** F01 · **Review:** billing

## Outcome

Capture project expenses with receipts, approval and allocation. Staff can submit
their own expenses; billing can approve, correct and report them. Track actual
spend independently from estimated AI cost and fixed-price customer billing.
No reimbursement transfer or corporate-card management in the first release.

## UX

[Expenses wireframe](../wireframes.html#expenses). Add expense asks merchant,
date, currency, total, category, paid-by, receipt and project allocation. Optional
task/epic allocation is available after selecting a project. Categories start
small: hosting, software, subcontractor, travel, other. Personal expenses stay
visible only to submitter and finance until approved project totals are computed.

Submitter sees draft/submitted/approved/rejected and rejection reason. Billing
queue shows duplicate warning, receipt preview and Approve/Request correction.
“Reimbursable” means money owed to the submitter, not “paid.” Mark reimbursed
requires separate evidence and permission; no button initiates a bank transfer.
A chargeable-to-customer flag proposes an invoice line but never sends an invoice
or changes a fixed-price commitment automatically.

## Data and rules

`expense(id,submitter_id,merchant,date,currency,total_minor,tax_minor,category_id,
paid_by,status,reimbursable,chargeable,receipt_asset_id,version,source_ref)`;
`expense_allocation(expense_id,project_id,task_id?,epic_id?,weight_bps)`;
`expense_approval(expense_id,actor,decision,reason,at)`;
`reimbursement(expense_id,amount_minor,evidence_id,paid_at,recorded_by)`.

Total must be positive except a credit/refund explicitly linked to an original
expense. Tax is a recorded component, not an inferred deduction. Allocations sum
to10,000bp, with a tenant overhead bucket allowed. Paid-by is agency or person;
recording a reimbursement is an internal transfer, not a second expense. Imported
processor fees and subscription invoices have unique source references so they
cannot be counted again as manual expenses. Reports separate estimated API cost
from recognized actual expenses to prevent double counting.

Receipts use private quarantined files. Optional text extraction may prefill a
draft with confidence labels but cannot approve or book an expense. Exclude
unnecessary card/account numbers from display and logs. A missing receipt needs
an explanation; tenant policy can require finance approval of the exception.

## API and access

`POST /expenses`, `GET /expenses?status=&project_id=`, `PATCH /expenses/{id}`,
`POST /expenses/{id}/submit`, `/approve`, `/reject`, `/corrections`,
`POST /expenses/{id}/reimbursements` and `GET /expense-categories`.
Own draft edits require `expense.submit`; approve/correct/reimburse require finance
capabilities. A submitter cannot approve their own expense by default; a sole-admin
tenant may self-approve with explicit reason and audit flag. Agents may create a
draft only with scoped permission; no automatic approval or reimbursement.

Approved records are immutable; correction creates an adjusted version/entry with
reason and preserves the original receipt. Locked accounting export periods require
a dated adjustment in the next open period. Events: expense.submitted/approved/
rejected/corrected and reimbursement.recorded. Only appropriate staff are notified.

## Acceptance

1. Staff can submit a receipt from phone, recover a failed upload and see status.
2. Other regular staff and all external users cannot read personal receipt content
   or reimbursements through direct URLs/search/export.
3. Duplicate imports/source IDs do not double-count a software subscription or
   processor fee already present in the actual-cost ledger.
4. Multi-project allocations and refunds sum exactly; overhead remains visible.
5. Approval races, self-approval rules, corrections and locked-period adjustments
   preserve immutable evidence and correct reports.
6. Reimbursement recording does not create a payment or second expense; missing
   evidence remains a visible incomplete state.

Migration adds empty expense tables. Seed generic categories only; no actual
merchant, account or receipt in public fixtures. Release manual capture before any
optional extraction or feed adapter, with a tested CSV export for finance review.
