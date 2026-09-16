# F13 — Stripe payment and per-invoice surcharge

**Wave:** 4 · **Dependencies:** F12, F08 · **Review:** billing + payment/security review

## Outcome

Customers pay invoices through Stripe; authoritative events update balances and
receipts. Every tenant uses its own configured account. A surcharge is an
admin-controlled **invoice-level** policy, separately disclosed, and applied only
where the selected method and applicable rules permit it. No financial connection
or live charge is part of the design phase.

## Payment experience

[Payment wireframe](../wireframes.html#payment). Customer chooses eligible card or
bank transfer. Show service amount, tax, payment fee and total before confirmation.
Bank instructions have no card fee. Pending/failed/cancelled payment leaves the
invoice payable. Success page says Payment received only after server-confirmed
payment state; a browser redirect is not evidence. Bank settlement can remain pending.

Store draft policy `{enabled,requested_rate_bps,calculation,policy_version}` per
invoice; admin may copy tenant defaults or override them. Once issued, policy is
part of immutable payment terms. Changing it requires a versioned amendment shown
to the payer and invalidates unconfirmed quotes. Billing staff cannot toggle it.
Invoice terms disclose the conditional fee; the final receipt records its exact
amount and method. Never charge a card fee on a bank payment or refundable credit.

## Fee arithmetic and eligibility

Additive fee: round(base×rate). Exact percentage recovery requires
`gross = ceil((base + fixed_processor_fee)/(1 - processor_rate))`; fee=gross−base,
in minor units with documented rounding. This differs from simply adding3.5%.
Actual processor rates can include fixed, cross-border or other components; a
configured estimate is not a guaranteed net receipt. Show expected net and record
actual processor fees after settlement.

Eligibility is server-side, versioned by jurisdiction, network, funding type and
processor requirements. U.S. Visa guidance limits credit surcharges to the lower
of acceptance cost or3%, and excludes debit/prepaid. See [Visa's requirements](https://usa.visa.com/dam/VCOM/global/support-legal/documents/merchant-surcharging-considerations-and-requirements.pdf).
Other methods/jurisdictions need verified rules before enabling fees. Reject an
invalid admin configuration with the reason; do not silently charge the requested
rate or call a surcharge a different fee to bypass a restriction.

For a funding type not known until payment details are collected, use a qualified
Stripe Payment Element flow: collect a provider token, server-resolve method
metadata, compute a short-lived quote, show the final total, then confirm an Intent
bound to that quote. No PAN/CVC reaches Tasktrack. Confirm this exact provider
flow in test mode before release. Plain hosted Checkout is acceptable with fee
off; do not assume it can dynamically enforce funding-specific fees. Unknown
eligibility offers fee-free card only if tenant policy allows it, otherwise bank
payment and an explanation. Never silently switch to paid API/service behavior.

## Tenant integration and ledger

Shared hosting uses Stripe Connect tenant accounts with direct charges and verified
account routing. Self-hosting can configure its own Stripe account through the
same adapter boundary. No platform-wide secret is exposed to tenants or runners.
Secrets are encrypted, write-only and rotatable; live/test environments are explicit.
An account cannot be attached to two unrelated tenants without admin reconciliation.

`payment_attempt(id,invoice_id,quote_id,provider_account_ref,intent_id,amount_minor,
currency,status,environment)`; `payment_quote(id,invoice_version,method_fingerprint,
principal_minor,fee_minor,tax_minor,expires_at,consented_at)`; append-only payment,
refund, dispute and allocation entries. One active attempt per payable balance;
late duplicate successful payments become overpayments requiring reconciliation.
Never drop money received because the invoice was concurrently voided or paid.

## API and processing

`POST /invoices/{id}/payment-quotes`, `POST /payment-quotes/{id}/confirm`,
`GET /payments/{id}`, `POST /payments/{id}/refunds` (explicit grant/admin),
`POST /integrations/stripe/webhook` (signature-authenticated),
`GET /integrations/stripe/status` and admin-only connect/disconnect operations.
Preserve provider IDs, account and mode in every dedupe key. Verify raw signature,
persist inbox, acknowledge, then process idempotently. Fetch authoritative provider
state on out-of-order events. [Stripe webhook guidance](https://docs.stripe.com/webhooks).

## Acceptance

Test two connected accounts, invalid signatures, replay, wrong mode/account,
duplicate/out-of-order events, lost callbacks, partial payment, overpayment,
refund and dispute reversal. Test fee off/on per invoice, additive versus gross-up,
debit exclusion, network cap, unknown eligibility, expired quote and a changed
balance before confirmation. Confirm exact cents, separate fee/principal totals,
no double receipt and no customer access outside a billing grant. Real payment
activation requires owner review of test evidence and current surcharge eligibility.
