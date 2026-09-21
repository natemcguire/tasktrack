"""Admin UI for workspace service applications. Human verification binds every change."""

import json
import re
from html import escape

from workers import Response

from . import passkeys, service_apps
from .agent_routes import validate_scope
from .db import Error
from .hosted_pages import page


def app_page(identity):
    return page(
        "Internal apps",
        """<main class="account-content setup-page"><a href="/account">Account settings</a><nav class="setup-nav" aria-label="Setup"><a href="/agents">Agents</a><a href="/apps" aria-current="page">Internal apps</a><a href="/imports">Imports</a></nav><h1>Internal apps</h1><p class="setup-intro">Show project work inside your own apps. Choose the projects they can read; keep the credential on your server.</p><p id="app-message" role="status"></p><form id="app-form" class="account-section setup-primary"><p class="setup-eyebrow">New connection</p><h2>Connect an internal app</h2><label for="app-name">App name</label><input id="app-name" placeholder="e.g. SailScan team dashboard" required maxlength="80"><fieldset><legend>Projects this app can read</legend><div id="app-projects"></div></fieldset><label><input type="checkbox" id="app-notes"> Include comments and files</label><p>Your app must verify which signed-in users can see this information. Keep its secret on your server.</p><button class="button primary">Create with passkey</button></form><section id="app-secret" class="account-section setup-progress" hidden><h2>Save this credential now</h2><p>The secret is shown once. Store it in your app server's secret manager.</p><pre id="app-credential"></pre><button id="app-copy" class="button" type="button">Copy credential</button><p id="app-copy-status" role="status"></p><button id="app-hide" class="button">I saved it — hide secret</button></section><section class="account-section"><h2>Workspace apps</h2><div id="app-list"></div></section></main>""",
        '<meta name="tt-csrf" content="'
        + escape(identity["csrf"])
        + '"><meta name="tt-workspace" content="'
        + escape(identity["workspace_id"])
        + '"><script src="/service-apps.js" defer></script>',
    )


async def route(worker, request, accounts, identity, path, method, body_data):
    service_apps.administrator(accounts, identity)
    if path == "/apps" and method == "GET":
        return Response(
            app_page(identity), headers={"Content-Type": "text/html; charset=utf-8"}
        )
    if path == "/api/v1/service-apps" and method == "GET":
        rows = await accounts.many(
            "SELECT * FROM service_apps WHERE workspace_id=? ORDER BY created_at DESC LIMIT 200",
            identity["workspace_id"],
        )
        return Response.json({"items": [service_apps.public(r) for r in rows]})
    if method != "POST":
        raise Error(404, "not_found", "Application route unavailable.")
    accounts.csrf(request, identity)
    await accounts.limit("app-admin:" + identity["user_id"], 30, 3600)
    data = await body_data(request)
    if path in ("/api/v1/service-apps/challenge", "/api/v1/service-apps"):
        definition = service_apps.definition(data)
        await validate_scope(
            worker,
            identity,
            {
                "projects": json.dumps(definition["project_ids"]),
                "capabilities": json.dumps(definition["capabilities"]),
            },
        )
        binding = service_apps.binding("create", definition)
        if path.endswith("/challenge"):
            return Response.json(
                await passkeys.step_up_options(accounts, identity, binding)
            )
        await passkeys.step_up_verify(accounts, identity, binding, data)
        return Response.json(
            await service_apps.create(accounts, identity, definition), status=201
        )
    match = re.fullmatch(
        r"/api/v1/service-apps/([a-f0-9-]+)/(rotate|revoke)(/challenge)?", path
    )
    if match:
        row = await service_apps.get(accounts, identity, match[1])
        binding = service_apps.binding(
            match[2], {"id": row["id"], "revision": row["revision"]}
        )
        if match[3]:
            return Response.json(
                await passkeys.step_up_options(accounts, identity, binding)
            )
        await passkeys.step_up_verify(accounts, identity, binding, data)
        return Response.json(
            await service_apps.change(accounts, identity, row, match[2])
        )
    raise Error(404, "not_found", "Application route unavailable.")
