# F20 — Project code, Git sync and conversation continuity

**Wave:** 5 · **Dependencies:** F18, F19 · **Review:** engineering workflow review

## Outcome

The third engineer has a reliable project checkout and can continue a conversation
after a lost session without overwriting another engineer's work. Git remains the
source of truth for code; Tasktrack holds execution context, approvals and evidence.
Do not treat a long model transcript as the only project memory.

## UX and records

[Code/continuity wireframe](../wireframes.html#code). Project → Code shows repository,
base branch, latest fetched commit, active worktree, dirty/conflict state and linked
PR. Conversation lanes show provider/model, exact session ID, last summary, last
checkpoint and next action. “Resume” previews the task and repository revision.
“New conversation” starts a fresh lane with a reviewed concise context packet.

`repository(id,project_id,remote_url,default_branch,credential_ref,allowed_paths,
status)`; `checkout(id,repository_id,runner_id,path,base_commit,branch,status)`;
`conversation_lane(id,project_id,provider_profile_id,provider_session_id,
active_attempt_id,last_checkpoint_id,version)`;
`checkpoint(id,run_id,task_id,workspace_ref,base_commit,head_commit,branch,
dirty_patch_asset_id,summary,next_action,acceptance_remaining,evidence_json,
usage_cursor,provider_cursor,created_at,digest)`.

## Git synchronization contract

1. Fetch only the configured origin with a short-lived, repository-scoped credential.
   Validate host/remote/path against admin configuration; no shell interpolation.
2. Create one isolated worktree/branch from the approved base commit per task/run.
   Record the exact SHA before any changes. Never reuse a dirty engineer checkout.
3. Obtain a task execution claim and file/resource reservations where an inbox
   integration is configured. A task claim is not a file lease.
4. Save incremental checkpoints and a content-addressed dirty patch before stop.
   Untracked files require explicit inclusion; exclude secrets, vendor caches,
   credentials and unrelated project data.
5. Run project-approved checks and produce a diff/PR with task links and evidence.
   Pushing a review branch may be a granted action; merging main or deploying
   requires separate approval. Never force-push another person's branch.
6. Before merge, fetch current base and resolve/test conflicts in the isolated
   worktree. If unresolved, save the conflict and ask for review, not reset --hard.

## Context packet and resume

Packet includes project brief/current approved scope, current task PRD and version,
latest decision/checkpoint, relevant conversation excerpts, repo commit and allowed
actions. Default budget12k input tokens, configurable. Summarize with links and
explicit omissions; retain source IDs/digests so a new agent can retrieve details.
Do not claim the summary contains all history. Internal and external context
projections remain separate; a customer message never receives raw provider traces.

Resume requires exact tenant/project/task/lane/provider session binding plus a
valid execution lease. Verify files/commit match checkpoint; if they differ, show
a reconciliation step. If provider session cannot be restored, create a new one
from the packet and record continuity provenance. Switching providers starts a
new native session; it cannot pretend to resume another provider's hidden state.
One lane writer prevents two resumptions racing; independent tasks get separate
lanes to avoid context contamination.

## API and integrations

`POST /projects/{id}/repositories` (admin), `POST /repositories/{id}/fetch`,
`POST /runs/{id}/checkpoints`, `GET /projects/{id}/context?task_id=`,
`POST /conversation-lanes/{id}/resume`, `GET /runs/{id}/diff`,
`POST /runs/{id}/propose-change` and approval-gated merge/deploy requests.
Agent Inboxes links use stable instance/task/thread IDs as documented in
`docs/integration.md`. An unavailable inbox adapter does not corrupt Git or discard
a checkpoint; fail the particular reservation step visibly before editing shared files.

## Acceptance and recovery

Simulate process death with dirty files, changed main, conflicting local edits,
expired Git credential, missing provider session, duplicate resume, offline origin
and revoked project access. Recover the exact task/branch/evidence without secret
files or duplicate external actions. Prove a stale runner cannot push after losing
its fence/approval. Verify a fresh provider can continue from the context packet
and identify omitted history. Test branch/PR links, mobile checkpoint readability,
diff access permissions and removal of temporary worktrees only after evidence is
durable. Backups preserve metadata and encrypted patches, not live credential homes.
