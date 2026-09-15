"""Small server-rendered account and customer pages; all user text is escaped."""

import json
from datetime import datetime, timezone
from html import escape as esc

STATUS = {
    "backlog": "Backlog",
    "in_progress": "In progress",
    "review": "Review",
    "done": "Done",
}


def page(title, content, metadata="", script=False):
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Tasktrack</title><meta name="robots" content="noindex,nofollow">
<link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/hosted.css">
{metadata}{'<script src="/hosted.js" defer></script>' if script else ""}
</head><body class="hosted-page"><header class="hosted-nav"><a class="brand" href="/"><span class="brand-mark">t.</span>tasktrack</a><span>A place for the work.</span></header>{content}</body></html>"""


def auth_page(mode="login", next_path="/", message="", secret=""):
    if secret:
        title = "You’re one click away."
        intro = "Continue to your Tasktrack workspace."
        form = f'''<form method="post" action="/auth/verify"><input type="hidden" name="token" value="{esc(secret)}"><button class="button primary" type="submit">Continue to Tasktrack</button></form>'''
    elif message:
        title, intro = "Check your inbox.", message
        form = '<a class="button" href="/login">Send another link</a>'
    else:
        title = "Make room for the work." if mode == "signup" else "Welcome back."
        intro = "Enter your email. We’ll send you a link to sign in."
        form = f'''<form method="post" action="/auth/link"><label for="email">Email address</label><input id="email" name="email" type="email" required autocomplete="email" placeholder="you@example.com" maxlength="254" autofocus><input type="hidden" name="next" value="{esc(next_path)}"><button class="button primary" type="submit">Email me a sign-in link</button></form><p class="small muted">Your first sign-in creates a private workspace.</p>'''
    return page(
        "Sign in",
        f'<main class="auth-card"><p class="eyebrow">YOUR WORK, TOGETHER</p><h1>{title}</h1><p class="muted">{esc(intro)}</p>{form}</main>',
    )


def error_page(message, status=400):
    return page(
        "Something needs attention",
        f'<main class="auth-card"><h1>{"Link unavailable." if status == 404 else "Let’s try that again."}</h1><p>{esc(message)}</p><a class="button" href="/login">Sign in</a></main>',
    )


def account_page(identity, overview):
    csrf = esc(identity["csrf"])
    options = "".join(
        f'<option value="{esc(w["id"])}" {"selected" if w["id"] == identity["workspace_id"] else ""}>{esc(w["name"])}</option>'
        for w in overview["workspaces"]
    )
    owner = identity["role"] == "owner"
    members = ""
    for member in overview["members"]:
        remove = (
            f'<button class="button quiet" data-remove-member="{esc(member["id"])}">Remove</button>'
            if owner and member["role"] != "owner"
            else ""
        )
        members += f"<li><span>{esc(member['email'])}<small>{esc(member['role'])}</small></span>{remove}</li>"
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
    content = f'''<main class="account-content"><a href="/">← Back to your board</a><h1>Your workspace</h1>
<p class="muted">Signed in as {esc(identity["email"])}</p>
<section class="account-section"><h2>Workspace</h2><form method="post" action="/account/switch"><input type="hidden" name="csrf" value="{csrf}"><label for="workspace">Open workspace</label><div class="account-row"><select id="workspace" name="workspace_id">{options}</select><button class="button">Open</button></div></form>
{f'<form id="rename-workspace"><label for="workspace-name">Workspace name</label><div class="account-row"><input id="workspace-name" name="name" value="{esc(identity["workspace_name"])}" maxlength="80" required><button class="button">Save name</button></div></form>' if owner else ""}</section>
<section class="account-section"><h2>People</h2><ul class="account-list">{members}</ul>{'<p class="small muted">An invitation lets one person join this workspace. It expires in 48 hours.</p><button class="button" id="create-invite">Create invitation link</button><div id="invite-result"></div><ul class="account-list">' + invites + "</ul>" if owner else ""}</section>
<section class="account-section"><h2>Agent access</h2><p class="muted">Give an agent access to this workspace from the CLI or API. Tokens expire after 90 days.</p>
{'<form id="create-token"><label for="token-name">Agent name</label><div class="account-row"><input id="token-name" name="name" placeholder="codex" maxlength="60" required><button class="button">Create token</button></div></form><div id="token-result"></div>' if owner else '<p class="small">Ask the workspace owner to create an agent token.</p>'}<ul class="account-list">{tokens or "<li>No tokens yet.</li>"}</ul></section>
<form method="post" action="/auth/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="button">Sign out</button></form><p id="account-message" role="status"></p></main>'''
    return page(
        "Account", content, f'<meta name="tt-csrf" content="{csrf}">', script=True
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
