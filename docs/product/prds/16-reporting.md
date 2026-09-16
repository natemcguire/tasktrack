# F16 — Cash, revenue attribution and delivery reports

**Wave:** 4 · **Dependencies:** F10–F15 · **Review:** billing + delivery lead

## Outcome

One dashboard explains invoices issued/sent, customer cash collected, money in the
bank, outstanding balances and the projects consuming the most time/tokens/cost.
Every number has a definition, date basis and drill-down. Estimated cost and actual
spend are never summed as though they were separate expenses.

## Screen and definitions

[Reports wireframe](../wireframes.html#reports). Top filters: date range, currency,
timezone, customer/project and cash/invoiced basis. Cards show Invoices issued,
Invoices sent, Settled customer cash, Net bank cash and Outstanding. A second row
shows service value allocated, actual delivery expense and contribution margin.
Ranked tables show highest token/time/cost tasks and epics, plus unallocated work.
Clicking a metric opens the source invoice/payment/usage/time/expense rows.

Definitions are in [shared contracts](../contracts.md). Additional rules:

- Issued counts distinct successfully frozen invoices by issue date; sent counts
  invoices with confirmed send outcome, not delivery jobs or reminders.
- Settled cash uses receipt/settlement date minus settled refunds, with tax and
  surcharge components shown separately. Pending card/ACH payments are excluded.
- Net bank cash follows actual payout/bank movement minus provider fees; a payout
  is a transfer, never new customer revenue. Unsettled processor balance is separate.
- Receivables use issued base principal/tax less credits and allocated payments;
  optional unpaid card fee is not already owed when a bank option has no fee.
- Contribution margin = allocated service value on chosen basis − actual allocated
  delivery expenses/labor. Show API-equivalent cost as a separate planning measure.
- Human hours, agent runtime and token counts have separate columns/units.

## Allocation and schema

Invoice lines allocate to task/epic/project as F12 defines. On cash basis, apply
settled service principal to invoice lines proportionally unless the payment has
an explicit line allocation. Largest-remainder rounding preserves cents. Rollups
count each allocation once, using a stable ancestry snapshot for historical reports.
Moving a task between epics does not silently restate prior-period revenue; a
finance-approved reallocation appends a correction with reason.

`report_snapshot(id,tenant_id,filters_json,as_of_sequence,generated_at,schema_version,
source_watermarks)` is optional export evidence. Core metrics are derived from
append-only ledgers and dated rates, not hand-edited dashboard counters. Materialized
views may accelerate queries but must rebuild deterministically. Late-arriving
payments/corrections mark affected historical results revised and expose as_of.

## API, access and export

`GET /reports/finance`, `/reports/delivery`, `/reports/allocations`,
`GET /reports/{type}/rows?metric=&cursor=`,
`POST /report-exports` →202 operation, `GET /report-exports/{id}`.
Finance/admin see whole-tenant financial reports; regular internal users with
`cost.read` see permitted project delivery totals only. External users see their
own invoice balances through invoice endpoints, never this dashboard. Filter
authorization precedes aggregation; a restricted project cannot leak through
“all projects” totals. CSV export uses the same policy and escapes formula cells.

## Acceptance

Use a fixed ledger fixture with two currencies, three invoices, partial/overpayment,
tax, surcharge, refund, dispute, processor fee, bank payout, subscription expense,
human labor and unassigned tokens. Independently calculate expected totals and
assert exact cents and hours. Verify:

1. Invoice send retries do not increase sent count; failed delivery is distinct.
2. Card receipt and later bank payout are not counted twice as revenue.
3. Refunds/credits affect the correct period and never erase original evidence.
4. Allocation weights/rounding reconcile task→epic→project→tenant including unknown.
5. Estimated API cost is not added again to actual subscription/provider expense.
6. Customer and restricted-project denial holds for totals, drill-down and exports.
7. Rebuilding projections yields identical results; stale data is labeled, not
   presented as live. Test desktop/mobile tables, accessible chart equivalents
   and empty states without misleading zeroes for unavailable measures.

First release uses tables and a few simple trend charts, no predictive finance.
Accounting-system totals remain separately reconciled in F17; dashboard labels
must not imply formal revenue recognition or tax advice.
