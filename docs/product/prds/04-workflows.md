# F04 — Custom board columns and safe workflow

**Wave:** 2 · **Dependencies:** F01 · **Review:** delivery lead

## Outcome

Projects can rename, add, order and retire columns without losing claim, review
or completion rules. A team might use Ideas, Ready, Building, Waiting for review,
Customer review and Shipped. Column names and count are configurable; the four
execution phases remain stable compatibility semantics.

## UX

[Workflow settings](../wireframes.html#workflow). Board settings shows ordered
rows: drag handle, name, allowed phases, optional WIP limit, phase defaults and archive.
Add creates a new row; Save applies the entire configuration atomically. Keyboard
Move up/down is equivalent to drag. Editing a label never moves tasks.

Retiring an occupied column requires choosing a replacement and previewing the
number of affected tasks. Cross-phase moves require normal transition validation;
bulk reconfiguration cannot bypass evidence, dependencies or execution claims.
If any affected task cannot transition, reject the change with a bounded list of
blockers. Empty states let staff add work; customers only see shared cards. On
phone, a column picker or explicit board scroller keeps page width usable.

## Schema and invariants

`board_column(id,project_id,name,allowed_phases_json,sort_key,wip_limit,archived_at,
version)`. Name1–60 characters, case-insensitive unique among active columns.
Limit50 active columns as a safety bound, with a minimum of one. `task.column_id`
references an active column; `task.status` remains the canonical phase for v1
compatibility. `board_phase_default(project_id,phase,column_id)` maps each phase
to a destination; several phases may share one column. A column may allow several
phases, so one-column and two-column boards are valid. Show a small phase badge
when a column contains mixed phases. No fixed four-column minimum.

Maintain one atomic `(status,column_id)` change. Moving within the same phase only
changes column/rank and records an event. If the destination allows the existing
phase, preserve it. Otherwise a single allowed phase selects that transition;
multiple alternatives require an explicit phase choice. Moving across phases
invokes existing Service transition actions. Done still needs accepted evidence, review remains
unclaimed, and an active task requires an execution claim. WIP counts exclude
archived items; entering an over-limit column returns409 unless a project manager
provides an explicit override reason. Do not block completing a task to enforce WIP.

## API

- `GET /projects/{id}/columns`: configuration and version; external sees only
  public names/order, not internal capacity metrics.
- `PUT /projects/{id}/columns`: expected configuration version, rows and explicit
  retirement mapping; `project.manage` required. Validate all before commit.
- `POST /tasks/{id}/move`: `{expected_version,column_id,phase?,before_id?,after_id?,reason?}`;
  destination must be same project and compatible with the actor's workflow rights.
- `GET /tasks?project_id=&column_id=&phase=`: filters have stable cursor order.

Events: board.columns_changed and task.moved, including before/after column and
phase. A label change emits one configuration event, not thousands of fake task
updates/notifications. API v1 move-to-status chooses the phase's current default.
The CLI adds column IDs/names without interpreting labels as phases.

## Failure and migration

Backfill four columns per existing project using current labels/order and map
every task's status. Validate all tasks reference the correct project column.
Preserve task IDs and versions unless their logical state changes. A migration
rerun creates no duplicate columns. Stale board saves return409 and let the user
compare changes. Deleted neighbor rank targets fail safely; stable rank rebalance
is an internal transaction and never drops cards. Customers cannot infer counts
of internal cards from column headers, WIP indicators or pagination.

## Acceptance

1. Configure1,2 and7 columns, rename/reorder them, reload and use through CLI/API;
   all phases remain reachable and mixed-phase cards retain correct state.
2. v1 status moves land in the right default column after customization.
3. Cross-phase drag cannot bypass claim/evidence/dependency rules; same-phase
   reordering does not reset a session claim or generate a completion.
4. Concurrent moves enforce WIP and versions; duplicate request returns receipt.
5. Retiring an occupied column either migrates all valid cards atomically or
   leaves the board unchanged with actionable blockers.
6. Keyboard and390px board remain usable with50 columns and long names.
7. Old database fixture upgrades with identical task totals and no orphan columns.
