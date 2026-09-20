"""Small server-rendered account and customer pages; all user text is escaped."""

import json
from datetime import datetime, timezone
from html import escape as esc

from .asset_version import ASSET_VERSION

STATUS = {
    "backlog": "Backlog",
    "in_progress": "In progress",
    "review": "Review",
    "done": "Done",
}


def page(title, content, metadata="", script=False):
    result = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Tasktrack</title><meta name="robots" content="noindex,nofollow">
<link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/hosted.css">
{metadata}{'<script src="/hosted.js" defer></script>' if script else ""}
</head><body class="hosted-page"><header class="hosted-nav"><a class="brand" href="/"><span class="brand-mark">t.</span>tasktrack</a></header>{content}</body></html>"""

    for asset in (
        "style.css",
        "hosted.css",
        "hosted.js",
        "auth.js",
        "passkeys.js",
        "preview.js",
        "favicon.svg",
    ):
        result = result.replace(
            '"/' + asset + '"', '"/' + asset + "?v=" + ASSET_VERSION + '"'
        )
    return result


def auth_page(mode="login", next_path="/", message="", secret="", challenge=""):
    if secret:
        title, intro = "Signing you in…", "Opening your workspace."
        form = f'''<form id="magic-form" method="post" action="/auth/verify"><input type="hidden" name="token" value="{esc(secret)}"><button class="button primary" type="submit">Continue</button></form>'''
    elif challenge:
        title, intro = (
            "Check your inbox.",
            "Enter the six-digit code. If your device offers AutoFill, select it to sign in.",
        )
        form = f'''<form id="code-form" method="post" action="/auth/code"><input type="hidden" name="challenge" value="{esc(challenge)}"><label for="code">Sign-in code</label><input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" maxlength="6" required autofocus><button class="button primary">Sign in</button><p id="code-error" role="alert"></p></form><p class="small muted">Expires in 15 minutes. You can also use the link in the email.</p><a href="/login?next={esc(next_path)}">Send a new code</a>'''
    else:
        title = "Sign in."
        intro = "Enter your email to sign in."
        form = f'''<form method="post" action="/auth/link"><label for="email">Email address</label><input id="email" name="email" type="email" required autocomplete="email" placeholder="you@example.com" maxlength="254" autofocus><input type="hidden" name="next" value="{esc(next_path)}"><button class="button primary" type="submit">Email me a code</button></form><p class="small muted">Your first sign-in creates a private workspace.</p>'''
    if not secret and not challenge:
        form = (
            '<button type="button" class="button" id="passkey-login" hidden>Sign in with a passkey</button><p id="passkey-message" role="status"></p>'
            + form
        )
    return page(
        "Sign in",
        f'<main class="auth-card"><h1>{title}</h1><p class="muted">{esc(intro)}</p>{form}</main>',
        '<script src="/auth.js" defer></script><script src="/passkeys.js" defer></script>',
    )


def error_page(message, status=400):
    return page(
        "Something needs attention",
        f'<main class="auth-card"><h1>{"Link unavailable." if status == 404 else "Let’s try that again."}</h1><p>{esc(message)}</p><a class="button" href="/login">Sign in</a></main>',
    )


TIMEZONES = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Singapore",
    "Australia/Sydney",
)


def account_page(identity, overview):
    csrf = esc(identity["csrf"])
    options = "".join(
        f'<option value="{esc(w["id"])}" {"selected" if w["id"] == identity["workspace_id"] else ""}>{esc(w["name"])}</option>'
        for w in overview["workspaces"]
    )
    current_ws = next(
        (w for w in overview["workspaces"] if w["id"] == identity["workspace_id"]),
        None,
    )
    is_admin = bool(
        current_ws
        and current_ws.get("kind") == "internal"
        and current_ws.get("access_role") == "admin"
    )
    owner = identity["role"] == "owner"
    tokens = "".join(
        f'<li><span>{esc(t["name"])}<small>{esc(t["actor"])} · expires {datetime.fromtimestamp(t["expires_at"], timezone.utc).date()}</small></span><button class="button quiet" data-revoke-token="{esc(t["id"])}">Revoke</button></li>'
        for t in overview["tokens"]
    )
    invites = ""
    for invitation in overview["invites"]:
        expiry = datetime.fromtimestamp(invitation["expires_at"], timezone.utc).date()
        label = "Accepted" if invitation["used_by"] else "Expires " + str(expiry)
        revoke = (
            ""
            if invitation["used_by"]
            else f'<button class="button quiet" data-revoke-invite="{esc(invitation["token_hash"])}">Revoke</button>'
        )
        invites += f"<li><span>Invitation <small>{label}</small></span>{revoke}</li>"
    passkey_rows = "".join(
        f'<li><span>{esc(k["name"])}<small>Added {datetime.fromtimestamp(k["created_at"], timezone.utc).date()}</small></span><button class="button quiet" data-remove-passkey="{esc(k["id"])}">Remove passkey</button></li>'
        for k in overview["passkeys"]
    )
    ROLE_LABELS = {"admin": "Admin", "billing": "Billing", "regular": "Member"}
    member_rows = ""
    for m in overview["members"]:
        name = esc(m.get("name") or m["email"])
        role = m.get("access_role") or m.get("role") or "regular"
        kind = m.get("kind", "internal")
        status = m.get("status", "active")
        external = kind != "internal"
        self_row = m.get("id") == identity["user_id"]
        if is_admin and not external:
            role_select = (
                f'<select data-member-role="{esc(m["membership_id"])}" aria-label="Role for {name}">'
                + "".join(
                    f'<option value="{v}" {"selected" if v == role else ""}>{label}</option>'
                    for v, label in ROLE_LABELS.items()
                )
                + "</select>"
            )
            status_btn = (
                f'<button class="button quiet" data-member-status="{esc(m["membership_id"])}" data-status="{esc(status)}">{"Reactivate" if status == "suspended" else "Suspend"}</button>'
            )
            remove_btn = (
                f'<button class="button quiet" data-remove-member="{esc(m["id"])}">Remove</button>'
                if owner and not self_row
                else ""
            )
            controls = f'<span data-member-revision="{esc(m["membership_id"])}" data-revision="{m["revision"]}">{role_select}{status_btn}{remove_btn}</span>'
        else:
            controls = f'<small>{esc(ROLE_LABELS.get(role, role))}{" · external" if external else ""}{" · suspended" if status == "suspended" else ""}</small>'
        member_rows += f'<li data-member-row="{esc(m["membership_id"])}"><span>{name}<small>{esc(m["email"])}</small></span>{controls}</li>'

    tz_options = "".join(f"<option>{esc(z)}</option>" for z in TIMEZONES)
    create_workspace = f'''<section class="account-section"><h2>Create a workspace</h2><p class="muted">Start a separate, private workspace. You’ll be its admin.</p><form id="create-workspace"><label for="new-workspace-name">Name</label><input id="new-workspace-name" name="name" maxlength="80" placeholder="Acme Studio" required><label for="new-workspace-tz">Timezone</label><select id="new-workspace-tz" name="timezone">{tz_options}</select><button class="button primary">Create workspace</button></form><p id="create-workspace-message" role="status"></p></section>'''

    members_admin = (
        f'''<section class="account-section"><h2>Team</h2><p class="muted">Set who administers this workspace and who can see billing. Changing a role signs the affected person out.</p><ul class="account-list" id="member-list">{member_rows}</ul><p id="member-message" role="status"></p></section>'''
        if is_admin
        else f'''<section class="account-section"><h2>Team</h2><ul class="account-list">{member_rows}</ul></section>'''
    )

    content = f'''<main class="account-content"><a href="/">← Back to your board</a><h1>Your workspace</h1>
<p class="muted">Signed in as {esc(identity["email"])}</p>
<section class="account-section"><h2>Workspace</h2><form method="post" action="/account/switch"><input type="hidden" name="csrf" value="{csrf}"><label for="workspace">Open workspace</label><div class="account-row"><select id="workspace" name="workspace_id">{options}</select><button class="button">Open</button></div></form>
{f'<form id="rename-workspace"><label for="workspace-name">Workspace name</label><div class="account-row"><input id="workspace-name" name="name" value="{esc(identity["workspace_name"])}" maxlength="80" required><button class="button">Save name</button></div></form>' if owner else ""}</section>
{create_workspace}
{members_admin}
<section class="account-section"><h2>Passkeys</h2><p class="muted">Sign in with Touch ID, Face ID or your device’s screen lock. Your email code remains available for recovery.</p><ul class="account-list">{passkey_rows or "<li>No passkeys yet.</li>"}</ul><form id="add-passkey" hidden><label for="passkey-name">Passkey name</label><div class="account-row"><input id="passkey-name" maxlength="80" value="My passkey" required><button class="button">Add passkey</button></div></form><p id="passkey-message" role="status"></p></section>
{'<section class="account-section"><h2>Invitations</h2><p class="small muted">An invitation lets one person join this workspace. It expires in 48 hours.</p><button class="button" id="create-invite">Create invitation link</button><div id="invite-result"></div><ul class="account-list">' + invites + "</ul></section>" if owner else ""}
<section class="account-section"><h2>Agent access</h2><p><a class="button primary" href="/agents">Review requests and manage agents</a> <a class="button" href="/imports">Import work</a></p><p class="muted">Use device enrollment for scoped access and phone approval. Legacy manually issued tokens below expire after 90 days.</p>
{'<form id="create-token"><label for="token-name">Agent name</label><div class="account-row"><input id="token-name" name="name" placeholder="codex" maxlength="60" required><button class="button">Create token</button></div></form><div id="token-result"></div>' if owner else '<p class="small">Ask the workspace owner to create an agent token.</p>'}<ul class="account-list">{tokens or "<li>No tokens yet.</li>"}</ul></section>
<form method="post" action="/auth/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="button">Sign out</button></form><p id="account-message" role="status"></p></main>'''
    return page(
        "Account",
        content,
        f'<meta name="tt-csrf" content="{csrf}"><script src="/passkeys.js" defer></script>',
        script=True,
    )


def invite_page(name, secret, identity=None):
    body = f'<main class="auth-card"><p class="eyebrow">YOU’RE INVITED</p><h1>Join {esc(name)}</h1><p>You’ll be able to view and edit the projects in this workspace.</p>'
    if identity:
        body += f'<form method="post" action="/invite/{esc(secret)}"><input type="hidden" name="csrf" value="{esc(identity["csrf"])}"><button class="button primary">Join workspace</button></form>'
    else:
        body += f'<a class="button primary" href="/login?next=/invite/{esc(secret)}">Sign in to join</a>'
    return page("Join workspace", body + "</main>")


def share_page(share, task, origin):
    status = STATUS[task["status"]]
    url = origin + "/s/" + share["token"]
    title = f"{task['reference']} · {task['title']}"
    metadata = f'''<meta property="og:type" content="website"><meta property="og:site_name" content="Tasktrack">
<meta property="og:title" content="{esc(title + " · " + status)}"><meta property="og:description" content="{esc(share["summary"][:300])}">
<meta property="og:url" content="{esc(url)}"><meta property="og:image" content="{esc(url)}/preview.png"><meta property="og:image:type" content="image/png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="{esc(title)}"><meta name="twitter:card" content="summary_large_image">'''
    snapshot = json.loads(share["snapshot"])
    body = f'''<main class="shared-task"><p class="eyebrow">{esc(task["reference"])} / SHARED TASK</p><h1>{esc(task["title"])}</h1><span class="shared-status {esc(task["status"])}">{esc(status)}</span><section class="customer-update"><h2>Update</h2><p class="preserve-lines">{esc(share["summary"])}</p></section><p class="small muted">Current task status · Updated {esc(task["updated_at"][:10])}</p><details><summary>View the shared snapshot</summary><img class="share-image" src="{esc(url)}/preview.png" width="1200" height="630" alt="{esc(snapshot["title"])}"><p class="small muted">Snapshot from when this link was created.</p></details></main>'''
    return page(title, body, metadata)
