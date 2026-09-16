# F18 — Asynchronous runner queue and isolation

**Wave:** 5 · **Dependencies:** F01, F10, F17 · **Review:** security + operations

## Outcome and non-goals

An authorized team member queues project work for an optional “third engineer.”
It may run slowly and resume later; customer submissions feel like email. The
web app remains available without a runner. This PRD designs the control plane,
not an immediately provisioned VPS or an unrestricted autonomous production agent.

## User flow

[Runners wireframe](../wireframes.html#runners). Staff choose task, approved provider,
work mode (answer draft/code change/review), budget and expected output. Show what
repositories and tools the job can access. Queue displays waiting/running/needs
approval/auth expired/failed/completed/cancelled with last checkpoint and next step.
Default one running job per tenant, one writer per task and conversation lane.
Customer can send Comms, but cannot select a privileged runner or shell command.

## State machine and leases

`run(id,tenant_id,project_id,task_id,requested_by,mode,provider_profile_id,
budget_json,status,version,created_at)`; `run_attempt(id,run_id,runner_id,
lease_epoch,lease_expires_at,last_heartbeat_at,started_at,ended_at,result_digest)`;
`approval(id,run_id,action_type,payload_digest,requested_by,approved_by,expires_at)`.

Queued→leased→running→waiting_approval/succeeded/failed/cancelled. Auth/quota
problems use blocked reason and resumable checkpoint. Lease90s, heartbeat every20s;
the server increments a fencing epoch when reassigning. Every callback includes
attempt+epoch; stale runners cannot publish output or spend new budget. On lost
heartbeat, quarantine the old attempt and verify its process stopped before
starting another writer. If not provable, require operator resolution rather than
two agents editing the same branch. Task claim remains separate and consistent.

## Hosting decision and security boundary

Cloudflare owns auth, domain storage, queue/outbox, encrypted artifacts and broker.
A Linux VPS worker makes outbound authenticated requests to claim jobs; no public
runner API is required. Use a supported LTS OS, named unprivileged operator,
key-only SSH, private administration network, firewall, automatic security updates,
resource limits and encrypted backups. No database/payment/admin credentials are
placed in the job environment. No public source repository contains actual host
IP, SSH key, tenant name or provider account details.

Each tenant/project gets an isolated sandbox and least-privilege Git/service
credentials. Arbitrary code must not share a host account, home directory, Docker
socket or credential volume across unrelated tenants. A small initial VPS is
**single-tenant only**; public multi-tenant execution requires stronger isolation
qualification or separate hosts/Cloudflare sandboxes. Containers isolate jobs but
are not assumed to make a shared kernel safe for mutually hostile tenants.

## API and authority

`POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/cancel`,
`POST /runners/claim-next`, `/run-attempts/{id}/heartbeat`, `/checkpoint`, `/finish`,
`POST /approvals/{id}/approve|deny`, admin `POST /runners/enroll|revoke`.
Enrollment is one-use, short-lived and produces a bounded runner identity.
Callbacks can append artifacts/usage for that run, not arbitrary project writes.
Job payload contains IDs and approved instruction text, never raw long-lived secrets.

Customer-visible replies, invitations, invoices, payments, provider credential
changes, merges and deployments require separate capabilities and explicit approval
bound to payload digest/version. Approval expires or invalidates when content
changes. An instruction inside Comms, README or model output cannot authorize it.
Default runner only drafts comments and creates review branches/PRs.

## Budgets, cancellation and recovery

Enforce max walltime, turns, token/spend estimate, disk and process count. Stop at
quota and save a checkpoint; never silently switch from subscription to API billing
or another provider. Cancellation sends a process-group stop, waits a bounded grace
period, then kills the sandbox; record consumed usage and dirty worktree evidence.
Do not label an unconfirmed stop as cancelled. Outbox/queue dedupe protects run
creation; orphan attempts appear in internal TRIAGE.

## Acceptance

Use fake providers before real subscriptions. Test lease loss, stale callbacks,
double claims, worker restart, auth expiry, quota, cancellation during Git writes,
disk full and provider timeout. Verify no cross-tenant files/secrets, no root or
Docker socket in jobs, and no privileged action without current digest-bound
approval. Restore an interrupted run from a checkpoint without replaying completed
side effects. Measure one sequential job on the proposed host; record memory,
disk, duration and monthly estimate before any purchase/scale decision.
