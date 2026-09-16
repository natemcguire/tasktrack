# F14 — Bank payment instructions and reconciliation

**Wave:** 4 · **Dependencies:** F12 · **Review:** billing + reconciliation review

## Outcome and scope

Customers can pay by wire or ACH using verified tenant instructions. Billing staff
match actual receipts to invoices, including partial and overpayments. An optional
bank adapter can read transactions or create supported receivable requests later.
Do not equate an outbound ACH API with the ability to debit a customer. No bank
connection, mandate, transfer or automatic debit is enabled during design.

## UX

[Reconciliation wireframe](../wireframes.html#reconciliation). Admin settings has
versioned payment-instruction profiles: currency, beneficiary, bank identity,
routing/account/IBAN/SWIFT as applicable, domestic/international instructions and
required reference. Enter or privately upload evidence, review, then activate.
Display masked details in settings; issued invoices snapshot the full approved
instructions for authorized recipients. Show a change warning before activating
new destination details; retain who verified them and when.

Finance → Reconcile lists unmatched settled receipts beside open invoices. Suggest
matches by exact reference, currency, amount and customer, with explanation. A
human confirms allocations. One receipt can settle several invoices; several
receipts can settle one invoice. Keep overpayment in a customer credit/unallocated
bucket. Never force a match or mark an invoice paid from an email/PDF assertion.

## Data and adapter contract

`bank_instruction_version(id,tenant_id,currency,encrypted_fields,verified_by,
verified_at,effective_at,retired_at,digest)`;
`bank_transaction(id,source_account_ref,provider_transaction_id,status,currency,
amount_minor,booked_at,value_at,reference,sender_fingerprint,source_digest)`;
`reconciliation(id,transaction_id,invoice_id,principal_minor,fee_minor,created_by,
reason,reversal_of_id)`.

A generic banking adapter declares independent capabilities:
`instructions`, `transactions.read`, `receivables.create`, `inbound_debit`,
`webhooks`, `outbound_payments`. Only instructions and read/reconcile are initial
scope. Outbound payments remain disabled even if a provider API supports them.
Inbound debit requires confirmed provider support, customer authorization/mandate,
return handling and a separate approved implementation task. Provider names and
tenant account details are private configuration, not public seed data.

Manual CSV import accepts an explicit column mapping, ISO currency, date timezone
and signed amount convention. Preview errors before commit. Dedupe by source
transaction ID or stable digest plus confirmed collision resolution; identical
amount/date alone is not sufficient because real duplicate-value receipts exist.
Store raw import evidence privately with a retention limit.

## API and consistency

`POST /bank-instructions`, `POST /bank-instructions/{id}/activate` (admin only),
`GET /bank-transactions`, `POST /bank-imports/preview`, `POST /bank-imports/{id}/commit`,
`POST /reconciliations`, `POST /reconciliations/{id}/reverse`,
`GET /bank-integrations/capabilities` (finance/admin).
Version-lock both receipt and invoice balances. Sum allocations cannot exceed
settled available receipt value or invoice outstanding value unless explicitly
creating a customer credit. Currency mismatch is rejected, not converted silently.
Reversal appends an opposite entry and recalculates balances; it never deletes proof.

Events: bank.receipt_imported, receipt.matched/unmatched, reconciliation.reversed,
invoice.balance_changed, bank.instructions_changed. Customer payment notification
occurs only after confirmed settlement allocation. Pending ACH later returned must
reverse settlement, reopen the balance and notify authorized billing participants.

## Acceptance and release gates

1. Wire instruction versions are tenant-isolated; updating settings does not
   rewrite issued invoice PDFs or change existing payment references.
2. Import same file twice without duplicate receipts; two real equal-value
   transactions remain independently matchable.
3. Partial, multi-invoice and overpayment allocations reconcile exact cents;
   concurrent allocations cannot spend the same receipt twice.
4. Pending, returned and reversed bank transactions produce correct balances and
   preserve audit/evidence. A screenshot of a transfer is never sufficient alone.
5. No external user sees bank feed, unrelated transactions or source evidence.
6. Adapter qualification records actual supported inbound/outbound capabilities,
   scopes, auth expiry, pagination, rate limits and webhook verification.

Start with verified instructions and manual matching. Add read-only feed access
after an admin explicitly connects it; accounting export remains separate in F17.
