"""Browser-owned OAuth provider connections; encrypted credentials never leave the Worker."""

import base64
import hashlib
import json
import re
import uuid
from urllib.parse import quote, unquote, urlencode, urlsplit

from .accounts import digest, timestamp, token
from .agent_auth import browser
from .db import Error, encode

SCOPES = {
    "jira": "read:jira-work read:jira-user offline_access",
    "trello": "read:member:trello read:board:trello read:card:trello read:list:trello read:action:trello read:checklist:trello read:attachment:trello offline_access",
    "basecamp": "",
}


def module():
    from workers import import_from_javascript

    return import_from_javascript("provider-crypto.mjs")


def key(accounts):
    value = getattr(accounts.env, "IMPORT_ENCRYPTION_KEY", "")
    if not value and accounts.local:
        value = base64.b64encode(
            hashlib.sha256(b"tasktrack-local-only-import-key").digest()
        ).decode()
    if not value:
        raise Error(
            503,
            "provider_unconfigured",
            "Provider connections are not enabled on this deployment yet. File imports are available.",
        )
    return value


def config(accounts, provider):
    if provider not in SCOPES:
        raise Error(422, "provider", "Unsupported provider.")
    prefix = provider.upper()
    client = getattr(accounts.env, prefix + "_CLIENT_ID", "")
    secret = getattr(accounts.env, prefix + "_CLIENT_SECRET", "")
    if not client or not secret:
        raise Error(
            503,
            "provider_unconfigured",
            provider.title()
            + " connection is not configured yet. You can import an export file.",
        )
    key(accounts)
    return client, secret


async def raw(url, headers=None, body=None, binary=False, allowed=None):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (allowed and not allowed(url))
    ):
        raise Error(
            422,
            "provider_url",
            "Provider returned an unsupported download or pagination address.",
        )
    try:
        result = json.loads(
            await module().request(
                encode(
                    {
                        "url": url,
                        "headers": headers or {},
                        "method": "POST" if body is not None else "GET",
                        **({"body": encode(body)} if body is not None else {}),
                        "binary": binary,
                    }
                )
            )
        )
    except Exception:
        raise Error(
            503,
            "provider_unavailable",
            "Provider request failed. Retry the import; no source data was changed.",
        ) from None
    if result["status"] == 429:
        raise Error(
            429,
            "provider_rate_limit",
            "Provider rate limit reached. Retry after "
            + result["headers"].get("retry-after", "60")
            + " seconds.",
        )
    if result["status"] in (401, 403):
        raise Error(
            403,
            "provider_access",
            "Provider access was denied. Reconnect or review source permissions.",
        )
    if result["status"] >= 400:
        raise Error(
            502,
            "provider_failed",
            "Provider returned HTTP " + str(result["status"]) + ".",
        )
    return result


async def json_request(url, headers=None, body=None, allowed=None):
    r = await raw(url, headers, body, allowed=allowed)
    if r["status"] >= 300:
        raise Error(502, "provider_redirect", "Unexpected provider API redirect.")
    try:
        return json.loads(r["body"]), r["headers"]
    except ValueError:
        raise Error(
            502, "provider_response", "Provider returned an invalid JSON response."
        ) from None


async def authorize(accounts, identity, provider):
    browser(identity)
    accounts.owner(identity)
    await accounts.limit("import-connect:" + identity["user_id"], 10, 3600)
    client, _ = config(accounts, provider)
    state = token()
    verifier = token()
    await accounts.run(
        "INSERT INTO import_oauth_states VALUES(?,?,?,?,?,?,?)",
        digest(state),
        provider,
        identity["user_id"],
        identity["workspace_id"],
        identity["session_hash"],
        verifier,
        timestamp() + 600,
    )
    callback = accounts.origin + "/integrations/" + provider + "/callback"
    params = {"client_id": client, "redirect_uri": callback, "state": state}
    if provider == "basecamp":
        params["type"] = "web_server"
        url = "https://launchpad.37signals.com/authorization/new"
    else:
        url = "https://auth.atlassian.com/authorize"
        params.update(scope=SCOPES[provider], response_type="code", prompt="consent")
        if provider == "jira":
            params["audience"] = "api.atlassian.com"
        else:
            params.update(
                code_challenge=base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                )
                .decode()
                .rstrip("="),
                code_challenge_method="S256",
            )
    return {"url": url + "?" + urlencode(params)}


async def callback(accounts, identity, provider, query):
    browser(identity)
    accounts.owner(identity)
    state = query.get("state", "")
    code = query.get("code", "")
    if not isinstance(code, str) or not code or len(code) > 4000:
        raise Error(
            400, "oauth_denied", "Provider authorization was cancelled or incomplete."
        )
    row = await accounts.one(
        "DELETE FROM import_oauth_states WHERE state_hash=? AND provider=? AND user_id=? AND workspace_id=? AND session_hash=? AND expires_at>? RETURNING *",
        digest(state),
        provider,
        identity["user_id"],
        identity["workspace_id"],
        identity["session_hash"],
        timestamp(),
    )
    if not row:
        raise Error(
            400,
            "oauth_state",
            "Connection request expired or belongs to another browser. Start again.",
        )
    client, secret = config(accounts, provider)
    payload = {
        "client_id": client,
        "client_secret": secret,
        "code": code,
        "redirect_uri": accounts.origin + "/integrations/" + provider + "/callback",
    }
    if provider == "basecamp":
        payload["type"] = "web_server"
        endpoint = "https://launchpad.37signals.com/authorization/token"
    else:
        payload["grant_type"] = "authorization_code"
        endpoint = "https://auth.atlassian.com/oauth/token"
        if provider == "trello":
            payload["code_verifier"] = row["verifier"]
    credentials, _ = await json_request(
        endpoint, {"Content-Type": "application/json"}, payload
    )
    if not isinstance(credentials.get("access_token"), str):
        raise Error(
            502, "oauth_exchange", "Provider did not return access. Start again."
        )
    headers = {
        "Authorization": "Bearer " + credentials["access_token"],
        "User-Agent": "Tasktrack (support@eastbayprojects.com)",
    }
    if provider == "jira":
        resources, _ = await json_request(
            "https://api.atlassian.com/oauth/token/accessible-resources", headers
        )
        resources = [
            {"id": str(r["id"]), "name": r["name"]}
            for r in resources
            if "read:jira-work" in r.get("scopes", [])
        ]
    elif provider == "basecamp":
        info, _ = await json_request(
            "https://launchpad.37signals.com/authorization.json", headers
        )
        resources = [
            {"id": str(r["id"]), "name": r["name"]}
            for r in info["accounts"]
            if r["product"] == "bc3"
        ]
    else:
        info, _ = await json_request(
            "https://trello.com/1/members/me?fields=id,username", headers
        )
        resources = [{"id": str(info["id"]), "name": info.get("username", "Trello")}]
    cid = str(uuid.uuid4())
    binding = (
        identity["workspace_id"]
        + ":"
        + identity["user_id"]
        + ":"
        + provider
        + ":"
        + cid
    )
    sealed = await module().seal(key(accounts), binding, encode(credentials))
    await accounts.run(
        "INSERT INTO import_connections(id,provider,user_id,workspace_id,secret,accounts,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
        cid,
        provider,
        identity["user_id"],
        identity["workspace_id"],
        sealed,
        encode(resources),
        timestamp() + int(credentials.get("expires_in", 3600)),
        timestamp(),
    )
    return cid


async def connection(accounts, identity, cid):
    browser(identity)
    accounts.owner(identity)
    row = await accounts.one(
        "SELECT * FROM import_connections WHERE id=? AND user_id=? AND workspace_id=? AND revoked_at IS NULL",
        cid,
        identity["user_id"],
        identity["workspace_id"],
    )
    if not row:
        raise Error(404, "connection_missing", "Connection unavailable.")
    binding = (
        identity["workspace_id"]
        + ":"
        + identity["user_id"]
        + ":"
        + row["provider"]
        + ":"
        + cid
    )
    secret = json.loads(await module().unseal(key(accounts), binding, row["secret"]))
    if row["expires_at"] <= timestamp() + 60:
        if not secret.get("refresh_token"):
            raise Error(401, "reconnect", "Reconnect the provider to continue.")
        locked = await accounts.one(
            "UPDATE import_connections SET refresh_lock=? WHERE id=? AND refresh_lock<? AND revoked_at IS NULL RETURNING id",
            timestamp() + 60,
            cid,
            timestamp(),
        )
        if not locked:
            raise Error(
                409,
                "connection_busy",
                "Connection refresh is in progress. Retry shortly.",
            )
        client, client_secret = config(accounts, row["provider"])
        payload = {
            "client_id": client,
            "client_secret": client_secret,
            "refresh_token": secret["refresh_token"],
        }
        endpoint = "https://auth.atlassian.com/oauth/token"
        if row["provider"] == "basecamp":
            payload["type"] = "refresh"
            endpoint = "https://launchpad.37signals.com/authorization/token"
        else:
            payload["grant_type"] = "refresh_token"
        try:
            renewed, _ = await json_request(
                endpoint, {"Content-Type": "application/json"}, payload
            )
            if not renewed.get("access_token"):
                raise ValueError()
            secret.update(renewed)
            await accounts.run(
                "UPDATE import_connections SET secret=?,expires_at=?,refresh_lock=0 WHERE id=? AND revoked_at IS NULL",
                await module().seal(key(accounts), binding, encode(secret)),
                timestamp() + int(secret.get("expires_in", 3600)),
                cid,
            )
        except Exception:
            await accounts.run(
                "UPDATE import_connections SET revoked_at=?,secret='' WHERE id=?",
                timestamp(),
                cid,
            )
            raise Error(
                401,
                "reconnect",
                "Provider refresh failed. Reconnect to continue safely.",
            ) from None
    return row, secret


class Client:
    def __init__(self, row, secret, account):
        if account not in [x["id"] for x in json.loads(row["accounts"])]:
            raise Error(
                403, "source_account", "Choose an account from this connection."
            )
        self.provider = row["provider"]
        self.account = account
        self.base = {
            "jira": "https://api.atlassian.com/ex/jira/" + quote(account, safe=""),
            "trello": "https://trello.com",
            "basecamp": "https://3.basecampapi.com/" + quote(account, safe=""),
        }[self.provider]
        self.headers = {
            "Authorization": "Bearer " + secret["access_token"],
            "User-Agent": "Tasktrack (support@eastbayprojects.com)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def allowed(self, url):
        parsed, base = urlsplit(url), urlsplit(self.base)
        path = unquote(unquote(parsed.path))
        return (
            parsed.scheme == base.scheme
            and parsed.hostname == base.hostname
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and "\\" not in path
            and not any(segment in (".", "..") for segment in path.split("/"))
            and path.startswith(base.path + "/")
        )

    async def get(self, path, body=None):
        url = path if path.startswith("https://") else self.base + path
        return await json_request(url, self.headers, body, self.allowed)

    async def download(self, url):
        # Credentials stay on the exact provider API origin. Redirected signed
        # object-storage downloads are credential-free and bounded at every hop.
        if not self.allowed(url):
            raise Error(422, "download_origin", "Unsupported provider download origin.")
        for hop in range(4):
            headers = self.headers if self.allowed(url) else {}

            def allowed(target):
                host = urlsplit(target).hostname or ""
                return self.allowed(target) or (
                    urlsplit(target).port in (None, 443)
                    and (
                        host
                        in {
                            "trello-attachments.s3.amazonaws.com",
                            "attachments.trello.com",
                            "bc3-production.s3.amazonaws.com",
                            "storage.googleapis.com",
                        }
                        or host.endswith(".cloudfront.net")
                        or host.endswith(".blob.core.windows.net")
                    )
                )

            result = await raw(url, headers, binary=True, allowed=allowed)
            if result["status"] == 200:
                return base64.b64decode(result["body"])
            if result["status"] not in (301, 302, 303, 307, 308):
                break
            url = result["headers"].get("location", "")
        raise Error(
            502,
            "download_redirect",
            "Provider download did not reach a supported file location.",
        )

    async def pages(self, path):
        result = []
        visited = set()
        while path:
            if path in visited or len(visited) > 200:
                raise Error(502, "pagination", "Provider pagination did not finish.")
            visited.add(path)
            items, headers = await self.get(path)
            if not isinstance(items, list):
                raise Error(
                    502, "provider_response", "Expected a paginated source list."
                )
            result += items
            link = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("link", ""))
            path = link[1] if link else None
        return result

    async def projects(self):
        if self.provider == "jira":
            result = []
            start = 0
            while True:
                page, _ = await self.get(
                    "/rest/api/3/project/search?maxResults=100&status=live&status=archived&startAt="
                    + str(start)
                )
                result += page["values"]
                if page.get("isLast", True):
                    return result
                start += len(page["values"])
        if self.provider == "trello":
            return (
                await self.get("/1/members/me/boards?filter=all&fields=id,name,closed")
            )[0]
        return await self.pages("/projects.json") + await self.pages(
            "/projects.json?status=archived"
        )

    async def statuses(self, project):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
            raise Error(422, "source_project", "Choose a source project.")
        if self.provider == "trello":
            rows, _ = await self.get("/1/boards/" + project + "/lists?filter=all")
            return [(r["name"], "backlog") for r in rows]
        if self.provider == "jira":
            rows, _ = await self.get("/rest/api/3/project/" + project + "/statuses")
            return list(
                {
                    r["name"]: {
                        "new": "backlog",
                        "indeterminate": "in_progress",
                        "done": "done",
                    }.get(r.get("statusCategory", {}).get("key"), "backlog")
                    for group in rows
                    for r in group["statuses"]
                }.items()
            )
        return [("Backlog", "backlog"), ("Done", "done")]

    async def jira_issues(self, project, fields):
        result = []
        cursor = None
        while True:
            body = {
                "jql": 'project = "' + project + '" ORDER BY key ASC',
                "maxResults": 100,
                "fields": fields,
            }
            if cursor:
                body["nextPageToken"] = cursor
            page, _ = await self.get("/rest/api/3/search/jql", body)
            result += page["issues"]
            if page.get("isLast", True):
                return result
            if not page.get("nextPageToken") or page["nextPageToken"] == cursor:
                raise Error(502, "pagination", "Jira returned an invalid cursor.")
            cursor = page["nextPageToken"]

    async def jira_children(self, key, kind):
        result = []
        start = 0
        while True:
            page, _ = await self.get(
                "/rest/api/3/issue/"
                + quote(key, safe="")
                + "/"
                + kind
                + "?maxResults=100&startAt="
                + str(start)
            )
            items = page.get("comments", page.get("values", []))
            result += items
            start += len(items)
            if start >= page.get("total", start):
                return result
            if not items:
                raise Error(502, "pagination", "Jira returned an incomplete page.")

    async def snapshot(self, project):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
            raise Error(422, "source_project", "Choose a valid source project ID.")
        if self.provider == "jira":
            issues = await self.jira_issues(project, ["*all"])
            for issue in issues:
                comments = await self.jira_children(issue["key"], "comment")
                issue["fields"]["comment"] = {
                    "comments": comments,
                    "total": len(comments),
                }
                history = await self.jira_children(issue["key"], "changelog")
                issue["changelog"] = {"histories": history, "total": len(history)}
            after = await self.jira_issues(project, ["updated"])
            if {x["id"]: x["fields"].get("updated") for x in issues} != {
                x["id"]: x["fields"].get("updated") for x in after
            }:
                raise Error(
                    409,
                    "source_changed",
                    "Jira changed during export. Retry to take a consistent snapshot.",
                )
            return {"project": {"id": project}, "issues": issues, "_complete": True}
        if self.provider == "trello":
            board, _ = await self.get(
                "/1/boards/"
                + project
                + "?fields=id,name,dateLastActivity&lists=all&cards=all&card_fields=all&card_attachments=true&checklists=all"
            )
            actions = []
            before = None
            seen = set()
            while True:
                page, _ = await self.get(
                    "/1/boards/"
                    + project
                    + "/actions?filter=commentCard&limit=1000"
                    + ("&before=" + before if before else "")
                )
                if not page:
                    break
                if page[-1]["id"] in seen:
                    raise Error(502, "pagination", "Trello repeated an action page.")
                seen.add(page[-1]["id"])
                actions += page
                if len(page) < 1000:
                    break
                before = page[-1]["id"]
            after, _ = await self.get(
                "/1/boards/" + project + "?fields=dateLastActivity"
            )
            if after.get("dateLastActivity") != board.get("dateLastActivity"):
                raise Error(
                    409,
                    "source_changed",
                    "Trello changed during export. Retry the snapshot.",
                )
            board["actions"] = actions
            board["_complete"] = True
            return board
        project_info, _ = await self.get("/projects/" + project + ".json")
        records = []
        for typ in (
            "Todo",
            "Todolist",
            "Kanban::Card",
            "Kanban::Step",
            "Message",
            "Document",
            "Upload",
            "Schedule::Entry",
            "Question::Answer",
            "Door",
            "Vault",
        ):
            for status in ("active", "archived", "trashed"):
                records += await self.pages(
                    "/projects/recordings.json?"
                    + urlencode({"type": typ, "bucket": project, "status": status})
                )
        for r in records:
            if r.get("type") in ("Todo", "Todolist", "Kanban::Card") and r.get(
                "comments_url"
            ):
                r["_comments"] = await self.pages(r["comments_url"])
        project_info["recordings"] = records
        project_info["_complete"] = False
        project_info["_warnings"] = [
            "Basecamp snapshot inventories available recording types; unsupported tools and embedded files remain in the source archive. Recheck source changes before cleanup."
        ]
        return project_info
