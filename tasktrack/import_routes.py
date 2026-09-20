"""Import wizard, source connections and verified blob uploads."""

import csv
import hashlib
import io
import json
import re
from html import escape
from urllib.parse import quote

from workers import Response

from . import integrations, passkeys, providers
from .accounts import timestamp
from .agent_auth import browser
from .db import Error, encode
from .hosted_pages import page


def import_page(identity):
    return page(
        "Import work",
        f"""<main class="account-content"><a href="/account">Account settings</a><h1>Import work</h1><p>Bring your projects, work, comments and files into {escape(identity["workspace_name"])}. Review every import before it changes your board.</p>
<p id="import-message" role="status" aria-live="polite"></p>
<section class="account-section"><h2>Connect a service</h2><div id="provider-buttons"></div><div id="connection-list"></div></section>
<section class="account-section"><h2>Or choose an export file</h2><label for="import-provider">Source</label><select id="import-provider"><option value="jira">Jira</option><option value="trello">Trello</option><option value="basecamp">Basecamp</option><option value="kanban">Kanban CSV or JSON</option></select><label for="import-file">Export file</label><input id="import-file" type="file" accept=".json,.csv"><button id="import-analyze" class="button">Review file</button><p>Files can be up to 2 MiB. Larger projects can use a connected service. File exports may omit history or attachments; the preview shows any gaps.</p></section>
<section class="account-section"><h2>Destination and mapping</h2><label for="import-project">Destination project</label><select id="import-project"></select><div id="import-mappings"></div><label><input id="import-visibility" type="checkbox"> I reviewed who can access the destination project and approve importing this source content there.</label><button id="import-preview" class="button primary" disabled>Prepare preview</button></section>
<section id="import-review" class="account-section" hidden><h2>Review import</h2><div id="import-summary"></div><div id="import-people"></div><button id="import-people-save" class="button" hidden>Save people mapping</button><label for="import-files">Original attachment files, if requested</label><input id="import-files" type="file" multiple><button id="import-upload" class="button">Verify files</button><button id="import-commit" class="button primary">Import reviewed work</button><button id="import-cancel" class="button">Cancel pending work</button><button id="import-report" class="button">Download report</button><button id="import-rollback" class="button">Roll back unchanged work</button></section>
<section class="account-section"><h2>Source fetches</h2><div id="import-fetches"></div></section><section class="account-section"><h2>Recent imports</h2><div id="import-jobs"></div></section></main>""",
        '<meta name="tt-csrf" content="'
        + escape(identity["csrf"])
        + '"><meta name="tt-workspace" content="'
        + escape(identity["workspace_id"])
        + '"><script src="/imports.js" defer></script>',
    )


def normalize(data):
    mapping = data.get("mapping", {})
    if not isinstance(mapping, dict) or any(
        v not in ("backlog", "in_progress", "review", "done") for v in mapping.values()
    ):
        raise Error(422, "mapping", "Choose valid destination phases.")
    raw = data.get("source")
    if (
        data.get("provider") == "kanban"
        and data.get("analyze")
        and not (isinstance(raw, dict) and raw.get("schema_version") == 1)
    ):
        rows = (
            list(csv.DictReader(io.StringIO(raw)))
            if isinstance(raw, str)
            else raw
            if isinstance(raw, list)
            else raw.get("cards", raw.get("records", []))
        )
        mapping = {
            r.get("status") or r.get("column") or "Backlog": "backlog" for r in rows
        } | mapping
    return providers.normalize(
        data.get("provider"), raw, data.get("account_id", "file"), mapping
    )


async def route(
    worker,
    request,
    accounts,
    identity,
    path,
    method,
    query,
    body_data,
    bounded_body,
    fetch_client=None,
    fetch_id=None,
):
    people_route = re.fullmatch(r"/api/v1/import-jobs/([a-f0-9-]+)/people", path)
    if people_route and method == "POST":
        browser(identity)
        accounts.owner(identity)
        accounts.csrf(request, identity)
        data = await body_data(request)
        _, job = await worker.task_call(
            identity, "/api/v1/import-jobs/" + people_route[1]
        )
        mapping = data.get("mapping")
        if not isinstance(mapping, dict) or len(mapping) > 500:
            raise Error(
                422, "people_mapping", "Supply a source-person to member mapping."
            )
        verified = {}
        for source_id, user_id in mapping.items():
            if user_id is None:
                verified[source_id] = None
                continue
            member = await accounts.member_identity(user_id, identity["workspace_id"])
            if not member or member["kind"] != "internal":
                raise Error(
                    422, "people_mapping", "Choose an active internal workspace member."
                )
            _, effective = await worker.task_call(
                member,
                "/api/v1/me/capabilities",
                query={"project_id": str(job["project_id"])},
            )
            if not effective["capabilities"].get("work.read"):
                raise Error(
                    422,
                    "people_mapping",
                    "The selected member cannot access the destination project.",
                )
            verified[source_id] = member["email"]
        status, result = await worker.task_call(
            dict(identity, verified_people_mapping=verified),
            path,
            "POST",
            {"digest": data.get("digest"), "mapping": verified},
            request_id=request.headers.get("Idempotency-Key"),
            via="ui",
        )
        return Response.json(result, status=status)
    if path == "/api/v1/import-people" and method == "GET":
        browser(identity)
        accounts.owner(identity)
        return Response.json(
            {
                "items": await accounts.many(
                    "SELECT u.id,u.name,u.email FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND m.kind='internal' AND m.status='active' ORDER BY u.name",
                    identity["workspace_id"],
                )
            }
        )
    file_route = re.fullmatch(
        r"/api/v1/import-jobs/([a-f0-9-]+)/(source|files/[a-f0-9]{64})", path
    )
    if not file_route:
        if path not in ("/api/v1/import-previews", "/api/v1/import-analyze"):
            browser(identity)
        accounts.owner(identity)
        if identity.get("bearer"):
            _, caps = await worker.task_call(identity, "/api/v1/me/capabilities")
            if not caps["capabilities"].get("integrations.manage"):
                raise Error(403, "not_allowed", "Import capability required.")
    elif method == "GET" and file_route[2] == "source":
        _, job = await worker.task_call(
            identity, "/api/v1/import-jobs/" + file_route[1]
        )
        sha = job.get("source_archive_sha256")
        if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{64}", sha):
            raise Error(404, "source_missing", "No source archive is attached.")
        blob = await worker.env.FILES.get(
            identity["workspace_id"] + "/import-sources/" + sha
        )
        if not blob:
            raise Error(404, "source_missing", "Source archive unavailable.")
        return Response(
            blob.body,
            headers={
                "Content-Type": "application/json",
                "Content-Disposition": 'attachment; filename="original-source.json"',
            },
        )
    if path == "/imports" and method == "GET":
        return Response(
            import_page(identity), headers={"Content-Type": "text/html; charset=utf-8"}
        )
    if path == "/api/v1/import-providers" and method == "GET":
        result = []
        for provider in integrations.SCOPES:
            try:
                integrations.config(accounts, provider)
                configured = True
            except Error:
                configured = False
            result.append({"provider": provider, "configured": configured})
        return Response.json({"items": result})
    if path == "/api/v1/import-connections" and method == "GET":
        rows = await accounts.many(
            "SELECT id,provider,accounts,expires_at FROM import_connections WHERE workspace_id=? AND user_id=? AND revoked_at IS NULL",
            identity["workspace_id"],
            identity["user_id"],
        )
        return Response.json(
            {"items": [dict(r, accounts=json.loads(r["accounts"])) for r in rows]}
        )
    callback = re.fullmatch(r"/integrations/(jira|trello|basecamp)/callback", path)
    if callback and method == "GET":
        await integrations.callback(accounts, identity, callback[1], query)
        return Response(None, status=303, headers={"Location": "/imports"})
    if method != "GET":
        accounts.csrf(request, identity)
    rollback = re.fullmatch(
        r"/api/v1/import-jobs/([a-f0-9-]+)/rollback/(challenge|decision)", path
    )
    if rollback and method == "POST":
        _, preview = await worker.task_call(
            identity, "/api/v1/import-jobs/" + rollback[1] + "/rollback"
        )
        if preview["conflicts"]:
            raise Error(
                409,
                "rollback_conflicts",
                "Some imported records changed. No work will be archived; review the report first.",
            )
        if rollback[2] == "challenge":
            return Response.json(
                await passkeys.step_up_options(
                    accounts, identity, "rollback:" + preview["digest"]
                )
            )
        data = await body_data(request)
        await passkeys.step_up_verify(
            accounts, identity, "rollback:" + preview["digest"], data
        )
        trusted = dict(identity, human_approval_digest=preview["digest"])
        status, result = await worker.task_call(
            trusted,
            "/api/v1/import-jobs/" + rollback[1] + "/rollback",
            "POST",
            {"digest": data.get("digest"), "manifest_digest": preview["digest"]},
            request_id=request.headers.get("Idempotency-Key"),
            via="ui",
        )
        return Response.json(result, status=status)
    authorize = re.fullmatch(
        r"/api/v1/import-connections/(jira|trello|basecamp)/authorize", path
    )
    if authorize and method == "POST":
        return Response.json(
            await integrations.authorize(accounts, identity, authorize[1])
        )
    connection = re.fullmatch(
        r"/api/v1/import-connections/([a-f0-9-]+)/(projects|statuses|snapshot|disconnect)",
        path,
    )
    if connection:
        if connection[2] == "disconnect" and method == "POST":
            await accounts.run(
                "UPDATE import_connections SET secret='',revoked_at=? WHERE id=? AND workspace_id=? AND user_id=?",
                timestamp(),
                connection[1],
                identity["workspace_id"],
                identity["user_id"],
            )
            return Response.json({"disconnected": True})
        data = await body_data(request) if method == "POST" else query
        row, secret = await integrations.connection(accounts, identity, connection[1])
        client = fetch_client or integrations.Client(
            row, secret, str(data.get("account_id", ""))
        )
        if connection[2] == "projects" and method == "GET":
            return Response.json({"items": await client.projects()})
        if connection[2] == "statuses" and method == "GET":
            return Response.json(
                {"statuses": await client.statuses(str(data.get("source_project", "")))}
            )
        if connection[2] == "snapshot" and method == "POST":
            if not fetch_id:
                await accounts.limit("snapshot:" + identity["user_id"], 10, 3600)
            source = await client.snapshot(str(data.get("source_project", "")))
            # Preserve the original complete export, including unsupported source tools.
            archive = encode(source).encode()
            archive_sha = hashlib.sha256(archive).hexdigest()
            await worker.env.FILES.put(
                identity["workspace_id"] + "/import-sources/" + archive_sha, archive
            )
            # Native file metadata is added only after exact provider bytes are available.
            downloads = []
            if row["provider"] == "jira":
                downloads = [
                    (
                        a,
                        client.base
                        + "/rest/api/3/attachment/content/"
                        + quote(str(a["id"]), safe="")
                        + "?redirect=false",
                    )
                    for issue in source["issues"]
                    for a in issue["fields"].get("attachment", [])
                ]
            elif row["provider"] == "trello":
                downloads = [
                    (a, a.get("url", ""))
                    for card in source.get("cards", [])
                    for a in card.get("attachments", [])
                    if a.get("isUpload", True)
                ]
                for a, _ in downloads:
                    a["size"] = a.get("bytes")
            else:
                for r in source.get("recordings", []):
                    if r.get("type") == "Upload" and r.get("download_url"):
                        a = {
                            "id": str(r["id"]),
                            "filename": r.get("filename")
                            or r.get("title", "Attachment"),
                            "size": r.get("byte_size"),
                            "created_at": r.get("created_at"),
                        }
                        r["_attachments"] = [a]
                        downloads.append((a, r["download_url"]))
            for a, url in downloads:
                try:
                    content = await client.download(url)
                    sha = hashlib.sha256(content).hexdigest()
                    if a.get("size") is not None and a["size"] != len(content):
                        raise Error(502, "attachment_size", "Attachment bytes changed.")
                    await worker.env.FILES.put(
                        identity["workspace_id"] + "/blobs/" + sha, content
                    )
                    a.update(sha256=sha, size=len(content))
                except Error:
                    pass  # Every absent verified file becomes a preview warning.
            dataset = providers.normalize(
                row["provider"], source, client.account, data.get("mapping", {})
            )
            dataset["source_archive_sha256"] = archive_sha
            # Recheck disconnect/revocation after all network requests, before staging.
            await integrations.connection(accounts, identity, connection[1])
            status, job = await worker.task_call(
                dict(identity, verified_source_archive=archive_sha),
                "/api/v1/import-jobs",
                "POST",
                {
                    "project_id": data.get("project_id"),
                    "dataset": dataset,
                    "allow_visibility_change": data.get("allow_visibility_change"),
                },
                request_id=("fetch:" + fetch_id)
                if fetch_id
                else request.headers.get("Idempotency-Key"),
                via="ui",
            )
            for a in job["attachments"]:
                if await worker.env.FILES.head(
                    identity["workspace_id"] + "/blobs/" + a["sha256"]
                ):
                    trusted = dict(
                        identity,
                        verified_import_blob={"sha256": a["sha256"], "size": a["size"]},
                    )
                    await worker.task_call(
                        trusted,
                        "/api/v1/import-jobs/" + job["id"] + "/blob",
                        "POST",
                        trusted["verified_import_blob"],
                        request_id="provider-file-" + job["id"] + "-" + a["sha256"],
                        via="ui",
                    )
            return Response.json(job, status=status)
    if path == "/api/v1/import-analyze" and method == "POST":
        data = await body_data(request)
        data["analyze"] = True
        dataset = normalize(data)
        return Response.json(
            {
                "total": len(dataset["records"]),
                "statuses": list(
                    {r["status"]: r["phase"] for r in dataset["records"]}.items()
                ),
                "warnings": dataset["warnings"],
            }
        )
    if path == "/api/v1/import-previews" and method == "POST":
        data = await body_data(request)
        dataset = normalize(data)
        archive = encode(data["source"]).encode()
        sha = hashlib.sha256(archive).hexdigest()
        await worker.env.FILES.put(
            identity["workspace_id"] + "/import-sources/" + sha, archive
        )
        dataset["source_archive_sha256"] = sha
        status, job = await worker.task_call(
            dict(identity, verified_source_archive=sha),
            "/api/v1/import-jobs",
            "POST",
            {
                "project_id": data.get("project_id"),
                "dataset": dataset,
                "allow_visibility_change": data.get("allow_visibility_change"),
            },
            request_id=request.headers.get("Idempotency-Key"),
            via="ui",
        )
        return Response.json(job, status=status)
    blob = re.fullmatch(r"/api/v1/import-jobs/([a-f0-9-]+)/files/([a-f0-9]{64})", path)
    if blob and method == "POST":
        _, job = await worker.task_call(identity, "/api/v1/import-jobs/" + blob[1])
        content = await bounded_body(request, 10 * 1024 * 1024)
        sha = hashlib.sha256(content).hexdigest()
        if sha != blob[2] or not any(
            a["sha256"] == sha and a["size"] == len(content) for a in job["attachments"]
        ):
            raise Error(
                422, "file_mismatch", "File bytes do not match the source manifest."
            )
        await worker.env.FILES.put(identity["workspace_id"] + "/blobs/" + sha, content)
        trusted = dict(
            identity, verified_import_blob={"sha256": sha, "size": len(content)}
        )
        status, result = await worker.task_call(
            trusted,
            "/api/v1/import-jobs/" + blob[1] + "/blob",
            "POST",
            trusted["verified_import_blob"],
            request_id=request.headers.get("Idempotency-Key"),
            via="ui",
        )
        return Response.json(result, status=status)
    return None
