# Authorization policy foundation

`tasktrack.policy.decide()` evaluates one declared capability using current,
server-loaded membership, project, grant, resource and optional agent records.
It has no database, network or UI dependency. **Existing routes do not call this
module yet. This change does not enable customer access.**

```python
from tasktrack.policy import Membership, Project, Resource, decide

membership = Membership("member-1", "user-1", "tenant-1", "internal", "regular")
project = Project("project-1", "tenant-1")
decision = decide(
    "work.edit",
    tenant_id="tenant-1",
    membership=membership,
    project=project,
    resource=Resource(visibility="internal"),
)
if not decision.allowed:
    # The HTTP adapter chooses 403 or an existence-hiding 404.
    raise PermissionError(decision.message)
```

IDs are nonempty strings. Normalize stored IDs at the adapter boundary, not from
untrusted request claims. `ACTIONS` is the declared capability catalog; unknown
actions fail closed. Role/kind combinations are internal admin, internal billing,
internal regular and external regular. Legacy owner/member rows need an explicit
migration before integration, not a permissive fallback in this module.

## Rules

- Inactive memberships and tenant mismatches deny every action, including admin.
- All internal members can see an open internal project. Restricted projects need
  an active matching grant, except tenant admins.
- External members always need an individual participant grant and matching
  customer IDs on membership, project and grant. They see shared resources only;
  `project.read` authorizes the project shell, not its nested contents or counts.
- Internal regulars see aggregate project cost unless disabled, and their own time.
  Individual rates require billing/admin. Recording time or submitting expenses
  requires the resource owner to match the member; corrections use separate actions.
- Project manager grants allow project settings, accepting work, changing workflow
  and approving scope. Invitations and runner execution need separate explicit grants.
- Issuing invoices and financial reporting require billing/admin. Invoice adjustment
  additionally needs a grant for billing members. Surcharge settings remain admin-only.
- External invoice read/payment requires shared visibility, an issued invoice and
  the billing-contact flag. Scope approval requires its grant and a named approver.
- Agent credentials intersect the owner's permissions with explicit capability and
  project allowlists. Empty sets allow nothing; there is no wildcard. Inactive or
  mismatched-owner credentials deny access. Project allowlists cannot authorize a
  tenant-wide operation; those require a separately listed tenant capability.

`MEMBERSHIP_GRANTABLE` and `PROJECT_GRANTABLE` are closed catalogs, not custom-role
builders. A membership-level grant can enable project creation; other optional
permissions are project-bound. External members cannot receive these internal
capability grants. Deployment operators have no implicit role.

## Integration contract for the access feature

1. Authenticate the session/token, resolve tenant server-side, and load the current
   active membership. Do not trust actor headers, UI state, or roles embedded in
   request bodies. Resolve agent ownership from its stored credential.
2. Load the actual project and resource from that tenant's store. Derive resource
   visibility, ownership, issued-invoice state and named-approver status from storage.
   Resolve the project's current individual grant. A failed policy-store read must
   deny or abort the request, never substitute an admin or open-project default.
3. Evaluate the specific operation before reading private values or performing a
   write. Domain validation still enforces optimistic versions, invoice lifecycle,
   approval-request state, eligibility of mentions, ownership of nested objects,
   and the last-active-admin invariant. Permission alone is not a valid transition.
4. Apply policy before serializing search results, totals, attachments, previews,
   exports and participant lists. Authorizing a project shell does not authorize
   every item inside it. Use `notes.read`, `rates.read`, `time.read` and `cost.read`
   separately; redact private inputs before returning aggregates.
5. Re-load membership/grants and re-evaluate before queued email delivery, exports,
   downloads or agent callbacks. This pure module cannot detect a revoked grant in
   a stale object passed by a caller. It maintains no authorization cache.
6. If adapters cache decisions, include policy version, tenant, membership revision,
   project revision, resource identity/visibility/state, operation and credential
   scope/revocation revision. Invalidate on changes; a TTL alone is insufficient.
7. Route and migration work must prove real API denials and transactional revocation
   before enabling customer sessions. Last-admin protection and cross-store
   revocation are separate persistence requirements, not implemented here.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_policy.py'
```

Tests exhaust the declared capability catalog against all four roles, then check
cross-tenant IDs, revoked/suspended access, customer mismatches, internal visibility,
financial restrictions, ownership, explicit grants, malformed context, unknown
capabilities, and agent narrowing. These are unit-level policy proofs; API, browser,
queued-delivery and populated-migration tests remain required for integration.
