# F10 — Token usage and cost attribution

**Wave:** 3 · **Dependencies:** F01 · **Review:** delivery economics + adapter review

## Outcome

Staff can explain which tasks/epics consume tokens and money. Target10–15% error
for supported estimated token/cost sources, and use authoritative provider usage
when available. Never describe subscription tokens as a real per-token bill or
claim an accuracy guarantee when the source exposes only a usage percentage.

## UX and measures

[Usage wireframe](../wireframes.html#usage). Task → Internal usage shows provider,
model, input/cached/output tokens, observed/estimated/unavailable badge, source,
rate effective date, API-equivalent cost and allocated actual subscription spend.
Roll up task→epic→project without double counting. Unassigned work appears in an
explicit bucket with an attribution action; it must not disappear from totals.
Regular staff with `cost.read` see permitted project totals, never personal labor
rates or another tenant's provider account.

## Event schema and collection

`usage_event(id,source_id,source_event_id,run_id,attempt_id,turn_id,project_id,
task_id?,epic_id?,principal_id,provider,model,occurred_at,input_tokens,
cached_input_tokens,cache_write_tokens,output_tokens,other_usage_json,
measurement_kind,estimator_version?,raw_digest,confidence,correction_of_id?)`.
Unique `(source_id,source_event_id)` prevents retries/duplicate collectors. Capture
task binding before execution. Resuming a run keeps the task but assigns a new
attempt; turn usage is incremental, not accumulated conversation totals replayed
as new usage. If a provider emits cumulative counters, collector records the
previous counter and emits only a nonnegative delta, with reset detection.

Default one active task per run. Splits require explicit integer weights summing
10,000bp, retaining original event and allocation history. Ancestor rollups use
allocation rows, never both a child's and parent's raw counters. Failed/cancelled
attempts still consume usage. Do not count displayed reasoning text as a new output
category if it is already inside the provider's output-token total.

## Rates and actual cost

`price_version(provider,model,category,currency,per_million_decimal,effective_from,
effective_to,source_url,verified_at)`; immutable once used. Missing rates produce
unknown cost, not zero. Store integer micro-currency cost and calculation version.
Apply cached input, cache-write, tool fees and batch/priority multipliers only when
the source reports them. A provider invoice/usage reconciliation can append a
correction; do not overwrite historical evidence or silently reprice old work.

`subscription_cost(period,provider_account_ref,amount_minor,currency,evidence_id)`
is actual spend, allocated by documented usage weights across all known work plus
an unallocated pool. It is separate from API-equivalent cost. No API call is made
simply to obtain an equivalent price. A flat subscription has no authoritative
per-token price; show its effective allocation rate as derived, not contractual.

## API and permission

`POST /usage-events/batch` (max200, scoped ingest credential), `GET /usage?task_id=`,
`POST /usage/{id}/allocations`, `POST /usage/{id}/corrections`,
`GET /price-versions`, `POST /price-versions` (billing/admin),
`POST /subscription-costs` (billing/admin). Every ingest binds source to tenant,
provider and allowed projects. Return per-record accepted/duplicate/rejected status
with atomicity explicitly `per_record`; no ambiguous partial success. A batch
receipt contains the durable IDs so a collector can advance its checkpoint safely.

Events: usage.recorded/corrected/allocated, usage.coverage_degraded,
budget.threshold_crossed. External serializers and notifications omit all usage.

## Accuracy and acceptance

Qualification uses at least30 representative runs per provider/model/collector
including cached prompts, long context, retries and cancellation. Compare with
provider counters/export where available. Report weighted absolute percentage error
`sum(abs(estimate-actual))/sum(actual)`, coverage and p90 per-run error. Target≤15%
weighted token and priced-cost error and≤15% p90 for qualified estimates; observed
sources must reconcile exactly aside from documented provider rounding. Tiny/zero
runs use absolute error, not division by zero. If no ground truth exists, label
unverified and do not claim the target passed.

Acceptance additionally requires duplicate/cumulative/resume fixtures, exact
allocation totals, known cached-token pricing examples, missing-rate states,
permission denial through every API/export and a report that includes failed and
unassigned work. Migration must not infer historical token counts from task text.
Historical manual imports are labeled estimated with source and date. Release
collectors separately; one unknown provider must not block other measured work.
