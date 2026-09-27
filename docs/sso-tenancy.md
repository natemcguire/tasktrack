# Nate's Software SSO

The canonical deployment is https://tasks.nates-software.com. The existing https://tasks.eastbayprojects.com address is a white-label hostname on the **same** `tasktrack` Worker, D1 database, Durable Object namespace and R2 bucket. It retains its hostname and uses host-only cookies; either hostname can start SSO. There is no duplicate instance or synchronization layer.

The `AUTH_HUB` service binding points to Worker `auth`. `SSO_REQUIRED=1` disables independent public signup, email links and passkeys. SSO membership must be active in tenant `nates-software`, with product `tasktrack`. The organization owner is `nate.mcguire@gmail.com`. Invite users and grant Tasktrack at the auth hub; an invitation to a Tasktrack workspace alone cannot grant SSO access.

Browser login uses a random state bound to a secure HttpOnly cookie and consumed once in D1. The hub's fragment token is posted to the same-origin callback and exchanged server-side. The long-lived hub session is stored in D1, linked to the existing host-only Tasktrack session. Every protected request introspects the hub session. Shared task links and private previews require the same access.

Verified email links existing users to their current IDs and workspaces, preserving all task data. A newly invited product user joins the owner's first workspace as a regular member. Existing project, membership, agent and service-token scopes remain in force. API tokens check the owner's current SSO entitlement via the private service binding; service-app tokens check their creator's entitlement. Revocation fails closed, including hub outages.

Deployment adds migration `0013_sso.sql` without deleting or rewriting existing data. Local standalone development remains available when `SSO_REQUIRED` is unset. To exercise standalone hosted browser fixtures, explicitly override it to `0`.

The deployment script prefers the ignored `wrangler.private.json`; keep its canonical route, aliases, `SSO_REQUIRED`, and `AUTH_HUB` binding aligned with `wrangler.jsonc`. Existing `preview.eastbayprojects.com` remains an isolated preview origin, whose document reads recheck the associated SSO session.
