# F02 — People and invitations

**Wave:** 1 · **Dependencies:** F01 · **Review:** tenant admin

## Outcome and scope

Admins invite staff, choose roles and remove access. Authorized staff invite a
customer to a particular project. Every invitation names its recipient and exact
access; copying a link to another person must not admit them. Support resend,
expiry, cancellation and acceptance history. No automatic email-domain membership.

## User flow

[People wireframe](../wireframes.html#people). Settings → People has Internal,
Customers and Pending tabs. “Invite” asks for email, internal/external, role for
internal people, and required project for external people. External invitations
optionally designate billing contact or scope approver; defaults are both off.
Review shows exactly what they can access before Send. Internal invitations say
they can see projects open to all internal members.

Landing on a link displays masked recipient and agency name. Signed-out users
request a magic link for the invitation's email. A different signed-in identity
sees “Sign in with the invited email”; it cannot change the invitation email.
Accepted invites land in the named project. Expired/revoked links offer request
new invitation without exposing participant names. Remove person shows affected
projects and credentials; old comments keep their author and a removed badge.

## Data and lifecycle

`invitation(id,token_hash,tenant_id,normalized_email,kind,role,customer_id,
project_ids,capability_grants,created_by,expires_at,status,accepted_by,accepted_at,
version)` lives in D1 with a domain application operation in tenant SQLite.
Statuses: pending→accepting→accepted, pending→expired/revoked. Hash a random
256-bit token; default48h expiry. Store only a masked link after creation.
An invitation never stores a password. Keep exact tenant/project IDs immutable.

External invite application requires active issuer authority, current project
customer match and all target projects still open for invitations. Create/update
membership and grants idempotently. If the cross-store step is interrupted, the
user remains unable to access projects until the operation completes; retry resumes
the same operation. Never consume the invite before durable acceptance intent.

## API and notifications

- `POST /invitations`: `{email,kind,role?,customer_id?,project_ids,grants?}`;
  permission `identity.manage` internal, `external.invite` external. Returns202
  with invitation ID and delivery status. Reject external invites with no project.
- `GET /invitations?status=`: scoped admin/issuer list, masked link only.
- `POST /invitations/{id}/resend`: expected version; revokes old secret, sends new
  expiry, enforces cooldown; repeated idempotency key sends at most one email.
- `POST /invitations/{id}/revoke`: invalidates token; no effect on accepted access.
- `POST /invitations/accept`: verified session and token; atomic logical operation.
- `DELETE /projects/{id}/participants/{membership_id}`: versioned revoke, distinct
  from removing a whole tenant membership.

Emit invitation.created/delivered/accepted/revoked/expired and membership/grant
events. F08 sends transactional invitation email; a provider failure leaves a
visible “Delivery failed” invitation with retry, not an apparently successful send.
Rate limit initial invitations to20/admin/hour and resend to1/invitation/5min,
configurable. Mask whether an email already belongs to another tenant.

## Permission and edge cases

An issuer cannot grant a capability it lacks or promote itself. Admin-only
capabilities remain non-delegable. Existing internal members need project grants
only for restricted projects; a duplicate invitation must not demote/promote them.
If an existing external member gains a second project, add precisely that grant.
Changing an invite's scope requires revocation and a new invitation. Removing the
issuer invalidates pending invitations unless an admin explicitly reissues them.
Invitation URLs are excluded from logs, analytics and referrer propagation.

## Acceptance and rollout

1. Staff invite/accept gives correct role and open-internal access; external
   invite gives only selected projects and selected billing/approval grants.
2. Wrong email, expired/revoked token, replay and concurrent double acceptance
   cannot grant unintended access or duplicate a membership.
3. Crash between membership and project-grant writes resumes safely, with no
   temporary broad access. Acceptance rollback does not affect other tenants.
4. Revoking project access removes downloads, search and notifications immediately.
5. Resend failure and duplicate worker delivery do not produce duplicate invites.
6. Phone/keyboard flow clearly names recipient, role and project before sending.

Migrate existing anonymous invites as legacy internal-member invitations only;
expire unused ones before external access release and let admins reissue addressed
invites. Never reinterpret an old bearer invite as customer authorization. Record
acceptance, denial and email-delivery evidence in a test tenant before rollout.
