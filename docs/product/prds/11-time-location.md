# F11 — Time and work-location evidence

**Wave:** 3 · **Dependencies:** F01 · **Review:** delivery lead + privacy review

## Outcome

Track human effort and agent runtime against tasks without equating elapsed
computer time to billable labor. Record where a person says they worked, the
network-derived region when available, and runner hosting region as distinct facts.
This is supporting operational evidence, not a determination of tax treatment.

## UX

[Time and location wireframe](../wireframes.html#time). A task has Start timer,
Stop and Add time. Manual entry asks date, duration, description and optional work
region. Show an overlap warning and allow a documented correction. A running timer
is visible globally so closing a tab does not hide it. After prolonged inactivity,
ask the person to confirm duration rather than silently recording an overnight day.

Location shows “Declared work region,” “Network region” and “Runner region” on
separate rows, each with source/time/confidence. VPN, proxy, missing and conflicting
observations show Unknown/Needs review. Staff can correct their declaration with
a reason; the old declaration remains in restricted audit history. Do not relabel
a remote server IP as the human's location. Customers cannot see these records.

## Data and calculations

`time_entry(id,project_id,task_id,principal_id,kind,start_at,end_at,duration_seconds,
description,billable_flag,status,source,version,correction_of_id)`; kind human or
agent_runtime. A single human timer per membership is enforced by a unique active
timer record; agent attempts have their own monotonic runtime measurement.
Persist timer start server-side. Timezone affects display/date grouping, not UTC
duration. Manual duration is integer seconds; round only when displaying or using
an explicitly versioned billing policy. Fixed-price invoices do not multiply time
automatically. Internal labor-cost rates are effective-dated and finance-only.

`location_evidence(id,time_entry_id,kind,region_code,country_code,source,
observed_at,accuracy_class,declared_by,correction_of_id,retention_until)`.
Capture country/subdivision from a trusted edge provider field, not arbitrary
forwarded headers. Default do not retain raw IP; if an admin enables short-lived
diagnostic retention, encrypt separately, restrict to admin, disclose to staff and
expire within30 days. Coarse derived evidence follows tenant policy. No GPS,
keystroke tracking or continuous desktop surveillance.

## API, policy and events

- `POST /timers/start`: task ID; server rejects another active human timer409.
- `POST /timers/{id}/stop`: expected version; repeat returns same duration.
- `POST/PATCH /time-entries`: own entries, versioned; locked/approved periods
  require finance correction rather than overwrite.
- `POST /time-entries/{id}/location-declarations`: region/country/reason.
- `GET /time-entries?project_id=&principal_id=`: own or granted internal totals;
  private rates/location need separate capabilities.
- `GET /projects/{id}/location-summary`: finance/admin only, separate human/runner
  source categories and unknown count. Never present an inferred tax conclusion.

Events: timer.started/stopped, time.recorded/corrected, location.declared/corrected.
Location observations do not trigger customer notifications. `location.read`
defaults admin/billing; staff can read/correct their own entries. Runner credentials
may append their runtime/region, never a human location declaration.

## Acceptance

1. Concurrent timer starts allow one; stop/retry and browser reload preserve one
   correct interval. DST and timezone changes do not alter duration.
2. Manual overlaps are visible and totals follow explicit included/excluded status.
3. Agent waiting/runtime and human labor remain separate in reports and invoices.
4. VPN or missing geolocation stays unknown; a runner in one region and a person
   declaring another are both represented without automatic “correction.”
5. External API/export/search cannot obtain location, time or labor rates.
6. Correction preserves original evidence, recalculates permitted totals and marks
   affected reports as revised. Raw-IP retention deletion is verified.
7. Mobile timer/manual entry and screen-reader labels are usable without a map.

Migration creates no fabricated historical location or elapsed time. Import old
time only with source labels. Roll out location collection separately, after staff
notice and retention settings are visible; its absence never blocks task work.
