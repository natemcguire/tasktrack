# Navigation performance

## What changed

- Hosted HTML includes the initial project/task heading and a safely escaped
  JSON snapshot. The frontend hydrates that snapshot instead of starting with
  “Opening your workspace” and refetching the same data.
- Project boards use one `GET /api/v1/board?project_id=42&limit=20` request.
  It returns four paginated columns plus epic filter options. Card summaries omit
  PRDs, acceptance lists, inbox threads and result/checkpoint documents. Full task
  reads and existing API/CLI contracts retain those fields.
- Sidebar project data stays in memory for 30 seconds. Board snapshots stay in
  memory for 10 seconds; mutations invalidate them. In-flight board requests are
  deduplicated, and pointer hover can prefetch the next project. No private task
  data is persisted in a service worker or browser storage by this cache.
- Navigation generations reject late responses from an earlier project/task.
  Filters update the board without rebuilding the outer page. Task history loads
  as its section approaches the viewport. TRIAGE fetches 30 recent items at a
  time instead of draining every page.
- Static asset URLs contain their build-content digest and can use the browser
  cache across reloads. Tenant HTML and APIs remain `no-store`.
- Compact task serialization skips unused thread expansion and reuses project
  metadata. SQLite reads no longer issue redundant `last_insert_rowid()` calls.

## Evidence

The hosted browser suite verifies:

- Zero duplicate board/project data requests after a hydrated board reload.
- One compact request for an uncached project switch; zero for an immediate return.
- More than 90% smaller board payload for a fixture containing a long PRD.
- The real local Worker, D1, Durable Object SQLite and R2 remain in the test path.

These are request-count and payload checks, not a claim of a universal load-time
SLA. Network latency and Cloudflare runtime cold starts still affect first-byte
latency. API mutations still use server version checks; a short-lived board
snapshot is never authority for a write.
