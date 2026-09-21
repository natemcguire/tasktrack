"""Bounded provider fetching with durable response checkpoints and explicit retry."""

import hashlib
import json
import uuid

from . import integrations
from .accounts import timestamp
from .agent_auth import browser
from .db import Error, encode

CHECKPOINT_LIMIT = 32 * 1024 * 1024


class PauseFetch(Exception):
    pass


class CheckpointClient(integrations.Client):
    def __init__(self, row, secret, account, cache, bucket, prefix, budget=12):
        super().__init__(row, secret, account)
        self.cache_bytes = len(encode(cache).encode())
        self.cache, self.bucket, self.prefix, self.budget = (
            cache,
            bucket,
            prefix,
            budget,
        )

    def take(self):
        if self.budget <= 0:
            raise PauseFetch()
        self.budget -= 1

    def remember(self, key, value):
        size = len(encode({key: value}).encode())
        if self.cache_bytes + size > CHECKPOINT_LIMIT:
            raise Error(
                422,
                "snapshot_limit",
                "Source snapshot exceeds the 32 MiB checkpoint limit. Split the source project or use a prepared bundle.",
            )
        self.cache[key] = value
        self.cache_bytes += size

    async def get(self, path, body=None):
        key = hashlib.sha256(encode([path, body]).encode()).hexdigest()
        if key not in self.cache:
            self.take()
            result = await super().get(path, body)
            self.remember(key, {"json": result})
        # Callers enrich records in place; never mutate the saved provider response.
        return json.loads(encode(self.cache[key]["json"]))

    async def download(self, url):
        key = hashlib.sha256(("file:" + url).encode()).hexdigest()
        if key not in self.cache:
            self.take()
            content = await super().download(url)
            blob_key = self.prefix + "/file-" + key
            await self.bucket.put(blob_key, content)
            self.remember(key, {"blob": blob_key})
            return content
        blob = await self.bucket.get(self.cache[key]["blob"])
        if not blob:
            raise Error(
                409,
                "fetch_file_missing",
                "Staged source file is missing. Start a new snapshot.",
            )
        return bytes((await blob.arrayBuffer()).to_py())


def public(row):
    return {
        k: row[k] for k in ("id", "state", "requests_done", "created_at", "updated_at")
    } | {"result": json.loads(row["result"]) if row.get("result") else None}


async def route(
    worker, request, accounts, identity, path, method, body_data, bounded_body
):
    from workers import Response

    from . import import_routes

    browser(identity)
    accounts.owner(identity)
    if path == "/api/v1/import-fetches" and method == "GET":
        rows = await accounts.many(
            "SELECT * FROM import_fetches WHERE workspace_id=? AND user_id=? ORDER BY created_at DESC LIMIT 50",
            identity["workspace_id"],
            identity["user_id"],
        )
        return Response.json({"items": [public(r) for r in rows]})
    if method != "POST":
        raise Error(404, "not_found", "Fetch operation unavailable.")
    accounts.csrf(request, identity)
    data = await body_data(request)
    if path == "/api/v1/import-fetches":
        cid = data.get("connection_id")
        row, _ = await integrations.connection(accounts, identity, cid)
        descriptor = {
            k: data.get(k)
            for k in (
                "account_id",
                "source_project",
                "project_id",
                "mapping",
                "allow_visibility_change",
            )
        }
        await worker.task_call(
            identity, "/api/v1/projects/" + str(descriptor["project_id"])
        )
        fid = str(uuid.uuid4())
        await accounts.run(
            "INSERT INTO import_fetches(id,workspace_id,user_id,connection_id,request,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            fid,
            identity["workspace_id"],
            identity["user_id"],
            row["id"],
            encode(descriptor),
            timestamp(),
            timestamp(),
        )
        return Response.json(
            {"id": fid, "state": "pending", "requests_done": 0}, status=201
        )
    parts = path.split("/")
    if len(parts) != 6 or parts[5] not in ("resume", "cancel"):
        raise Error(404, "not_found", "Fetch operation unavailable.")
    fid = parts[4]
    row = await accounts.one(
        "SELECT * FROM import_fetches WHERE id=? AND workspace_id=? AND user_id=?",
        fid,
        identity["workspace_id"],
        identity["user_id"],
    )
    if not row:
        raise Error(404, "fetch_missing", "Source fetch unavailable.")
    if parts[5] == "cancel":
        await accounts.run(
            "UPDATE import_fetches SET state='cancelled',lease=NULL,lease_expires=0,updated_at=? WHERE id=? AND state!='complete'",
            timestamp(),
            fid,
        )
        return Response.json({"id": fid, "state": "cancelled"})
    if row["state"] == "complete":
        return Response.json(public(row))
    if row["state"] in ("cancelled", "failed"):
        raise Error(409, "fetch_cancelled", "Start a new source snapshot.")
    if row["created_at"] < timestamp() - 86400:
        raise Error(
            409,
            "fetch_expired",
            "Snapshot checkpoints expire after 24 hours. Start a new snapshot.",
        )
    lease = str(uuid.uuid4())
    held = await accounts.one(
        "UPDATE import_fetches SET lease=?,lease_expires=?,state='fetching',updated_at=? WHERE id=? AND lease_expires<? AND state NOT IN ('cancelled','complete') RETURNING id",
        lease,
        timestamp() + 600,
        timestamp(),
        fid,
        timestamp(),
    )
    if not held:
        raise Error(
            409,
            "fetch_busy",
            "Another fetch request is active. Retry after it finishes.",
        )
    try:
        cache = {}
        if row["cache_key"]:
            blob = await worker.env.FILES.get(row["cache_key"])
            if not blob:
                raise Error(
                    409, "fetch_missing", "Source checkpoint missing; start again."
                )
            cache = json.loads(await blob.text())
        descriptor = json.loads(row["request"])
        connection, secret = await integrations.connection(
            accounts, identity, row["connection_id"]
        )
        prefix = identity["workspace_id"] + "/import-fetches/" + fid
        client = CheckpointClient(
            connection,
            secret,
            str(descriptor["account_id"]),
            cache,
            worker.env.FILES,
            prefix,
        )
    except Exception:
        await accounts.run(
            "UPDATE import_fetches SET lease=NULL,lease_expires=0,state='pending' WHERE id=? AND lease=?",
            fid,
            lease,
        )
        raise

    async def saved_body(_request):
        return descriptor

    failure = None
    result = None
    state = "pending"
    try:
        response = await import_routes.route(
            worker,
            request,
            accounts,
            identity,
            "/api/v1/import-connections/" + row["connection_id"] + "/snapshot",
            "POST",
            {},
            saved_body,
            bounded_body,
            fetch_client=client,
            fetch_id=fid,
        )
        result = json.loads(await response.text())
        state = "complete"
    except PauseFetch:
        pass
    except Error as e:
        failure = e
        state = (
            "failed"
            if e.payload["error"]["code"] in ("source_changed", "snapshot_limit")
            else "pending"
        )
    except Exception:
        failure = Error(
            503,
            "fetch_interrupted",
            "Source fetch interrupted. Resume to retry its last request.",
        )
    content = encode(cache)
    if len(content.encode()) > 32 * 1024 * 1024:
        failure = Error(
            422,
            "snapshot_limit",
            "Source snapshot exceeds the 32 MiB checkpoint limit. Split the source project or use a prepared bundle.",
        )
        state = "failed"
    cache_key = prefix + "/checkpoint-" + lease
    await worker.env.FILES.put(cache_key, content)
    changed = await accounts.one(
        "UPDATE import_fetches SET cache_key=?,requests_done=?,state=?,result=?,lease=NULL,lease_expires=0,updated_at=? WHERE id=? AND lease=? RETURNING id",
        cache_key,
        len(cache),
        state,
        encode(result) if result else None,
        timestamp(),
        fid,
        lease,
    )
    if not changed:
        raise Error(
            409,
            "fetch_cancelled",
            "Fetch was cancelled; no further batches will be scheduled.",
        )
    if row["cache_key"] and row["cache_key"] != cache_key:
        try:
            await worker.env.FILES.delete(row["cache_key"])
        except Exception:
            pass  # The new checkpoint is committed; cleanup must not undo it.
    if failure:
        raise failure
    return Response.json(
        {"id": fid, "state": state, "requests_done": len(cache), "result": result}
    )
