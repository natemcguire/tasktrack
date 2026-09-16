# F12 — Fixed-price invoices

**Wave:** 4 · **Dependencies:** F03, F09 · **Review:** billing + customer document review

## Outcome

Create multiple fixed-price invoices per project, each with clear line items,
customer recipients, due date, payment instructions and task/epic attribution.
Keep issued documents stable and make draft, issued, partially paid, overdue,
paid, void and credited states unambiguous. Do not build time-and-materials billing
or recurring subscriptions in this release.

## Screens

[Invoice editor](../wireframes.html#invoice). Finance → Invoices lists number,
customer/project, issue/due dates, currency, total, paid and outstanding. New invoice
chooses a project/customer, copies billing details for review, adds fixed-price
lines, references approved milestones and selects recipients. Quantity may represent
fixed deliverable units, not automatically imported hours or token usage.

Preview shows agency/customer marks, legal seller identity, invoice number, dates,
line descriptions, subtotal, discounts/tax if applicable, base amount due and
conditional payment-fee terms. Bank instructions are tenant configuration, displayed
only on an issued invoice for its authorized billing recipients. An admin sees a
per-invoice Surcharge setting (off/on, rate/method); billing staff can read but not
change it. Defaults copy from tenant policy into each draft, never live-reference
future settings. F13 defines collection and rule validation.

## Data and calculation

`invoice(id,project_id,customer_id,number,currency,state,version,issued_at,due_date,
seller_snapshot,buyer_snapshot,brand_version,terms_snapshot,surcharge_policy,
subtotal_minor,discount_minor,tax_minor,base_total_minor,pdf_asset_id,digest)`;
`invoice_line(id,invoice_id,description,quantity_decimal,unit_price_decimal,
net_minor,tax_code?,milestone_id?)`; `line_allocation(line_id,target_type,target_id,
weight_bps)`; `invoice_recipient(invoice_id,membership_id,email_snapshot)`.

Use shared exact-money rules. Invoice numbering is tenant-unique and monotonically
allocated at issue, not draft creation. Number prefix/year policy is configurable;
never reuse a voided number. First release permits one currency per invoice and
reports currencies separately. Tax amounts/rates require explicit billing input
and supporting jurisdiction/tax-code configuration; do not infer taxable status
from IP location. Price includes no hidden AI markup or labor-rate disclosure.

## Issue transaction and lifecycle

`draft → issuing → issued`; issuing freezes a content snapshot, reserves number,
queues PDF generation and email intent. If PDF generation fails, show Issue failed
with retry against the same snapshot/number, not an editable half-issued invoice.
After immutable PDF is stored and digest recorded, issue event enables delivery.
No invoice is marked sent until delivery outcome is recorded. Payment states are
derived from settled allocations: unpaid/part_paid/paid; overdue is derived from
due date and outstanding balance. Do not encode every combination in one enum.

Issued content cannot be edited. Before payment, admin-authorized void/reissue
retains the original number and reason. After payment, use a credit note/refund
workflow. Archiving a project cannot delete its invoices or disable payment of
an outstanding valid invoice. Deleting a customer keeps legally retained snapshots.

## API and events

`POST /projects/{id}/invoices`, `GET/PATCH /invoices/{id}` (draft only),
`POST /invoices/{id}/issue`, `GET /invoices/{id}/pdf`,
`POST /invoices/{id}/void`, `POST /invoices/{id}/credit-notes`,
`POST /invoices/{id}/reminders`, `PATCH /invoices/{id}/surcharge-policy` (admin).
All mutations use versions and idempotency. Issuing requires `invoice.send`,
explicit recipients and a validated preview digest. Agent credentials have neither
send nor surcharge capability by default. Events include invoice.issued/voided,
invoice.delivery_failed, credit_note.issued and invoice.balance_changed.

## Acceptance

1. Three invoices for one project retain independent numbers, dates, recipients,
   fee settings and balances; approved milestone allocations remain traceable.
2. Decimal/discount/tax fixtures reconcile line totals to exact minor units.
3. Concurrent issue/retry reserves one number, PDF and notification intent.
4. Changing tenant branding/bank instructions/rates does not rewrite issued PDFs.
5. External billing contact can read/pay only their permitted issued invoices;
   ordinary external/internal users cannot obtain PDFs through guessed IDs.
6. PDF is legible on A4/Letter, multi-page lines and mobile download; no clipped
   bank instructions, distorted logos or internal costs. Render and inspect pages.
7. Payment, credit note, overdue and void states match ledger evidence; draft or
   failed email is never counted as collected cash.

No historical invoices are fabricated during migration. Initial invoices start in
test mode; real issue/send is a deliberate authorized action after F13/F14 gates.
