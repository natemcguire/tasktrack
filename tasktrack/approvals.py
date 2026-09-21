"""Single-use approval manifests, stored with the protected workspace mutations."""

import hashlib
import json
import re
import time
import uuid

from .db import Error, encode, now

PROTECTED_TASK_ACTIONS = frozenset(
    {"archive", "reopen", "restore", "reassign", "backlog"}
)


def identity(service):
    if not service.access:
        raise Error(
            403,
            "hosted_required",
            "Human approvals require an authenticated hosted workspace.",
        )
    return service.access.identity


def row_for(service, c, rid):
    who = identity(service)
    row = c.execute("SELECT * FROM approval_requests WHERE id=?", (rid,)).fetchone()
    if (
        not row
        or row["owner_id"] != who["user_id"]
        or (
            who.get("bearer")
            and row["agent_id"] != who.get("agent_id", who.get("token_id"))
        )
    ):
        raise Error(404, "approval_missing", "Approval request unavailable.")
    return dict(row)


def public(row):
    result = {
        k: row[k]
        for k in (
            "id",
            "actor",
            "summary",
            "state",
            "expires_at",
            "created_at",
            "digest",
            "decided_by",
            "decided_at",
            "executed_at",
        )
    }
    result["manifest"] = json.loads(row["manifest"])
    if result["expires_at"] <= int(time.time()) and result["state"] in (
        "pending",
        "approved",
    ):
        result["state"] = "expired"
    return result


def read(service, c, parts, query):
    from .service import fields

    fields(query, set())
    who = identity(service)
    if len(parts) == 1:
        rows = c.execute(
            "SELECT * FROM approval_requests WHERE owner_id=? AND state='pending' AND expires_at>? ORDER BY created_at DESC LIMIT 100",
            (who["user_id"], int(time.time())),
        )
        return {
            "items": [
                public(dict(r))
                for r in rows
                if not who.get("bearer")
                or r["agent_id"] == who.get("agent_id", who.get("token_id"))
            ]
        }
    if len(parts) == 2:
        return public(row_for(service, c, parts[1]))
    raise Error(404, "not_found", "Approval route unavailable.")


def authorize(service, c, method, path, body):
    who = identity(service)
    if who.get("kind") != "internal":
        raise Error(403, "not_allowed", "Internal workspace access required.")
    if path == "/api/v1/approval-requests" and method == "POST":
        if not who.get("bearer"):
            raise Error(
                403, "agent_required", "Agents request approval; humans decide."
            )
        return
    rid = path.split("/")[4] if len(path.split("/")) > 4 else ""
    row_for(service, c, rid)


def write(service, c, method, parts, body, context):
    from .service import fields, integer, string

    who = identity(service)
    if len(parts) == 1 and method == "POST":
        fields(
            body,
            {
                "operation",
                "task_id",
                "expected_version",
                "reason",
                "assignee",
                "reopen_parent",
            },
        )
        if body.get("operation") not in {"task." + a for a in PROTECTED_TASK_ACTIONS}:
            raise Error(
                422,
                "unsupported_operation",
                "Choose task.archive, task.reopen, task.restore, task.reassign or task.backlog.",
            )
        task = service.public_task(c, integer(body.get("task_id"), "task_id"))
        service.check_version(body, task)
        action = body["operation"].split(".")[1]
        target = "/api/v1/tasks/" + str(task["id"]) + "/" + action
        payload = {
            "expected_version": task["version"],
            "reason": string(body.get("reason"), "reason", True, 2000),
        }
        if action == "reassign":
            if "assignee" not in body:
                raise Error(
                    422, "assignee_required", "Supply assignee (or null to clear it)."
                )
            payload["assignee"] = body["assignee"]
        if action == "reopen" and body.get("reopen_parent") is True:
            payload["reopen_parent"] = True
        service.access.write(service, c, "POST", target, payload)
        manifest = {
            "method": "POST",
            "path": target,
            "body": payload,
            "task_id": task["id"],
            "title": task["title"],
            "project_id": task["project_id"],
            "operation": body["operation"],
        }
        if payload.get("reopen_parent") and task.get("parent_id"):
            parent = service.public_task(c, task["parent_id"])
            manifest["parent"] = {
                "id": parent["id"],
                "version": parent["version"],
                "title": parent["title"],
            }
        rid = str(uuid.uuid4())
        digest = hashlib.sha256(encode(manifest).encode()).hexdigest()
        summary = {
            "archive": "Archive ",
            "reopen": "Reopen ",
            "restore": "Restore ",
            "reassign": "Reassign ",
            "backlog": "Return to backlog: ",
        }[action] + task["title"]
        c.execute(
            "INSERT INTO approval_requests(id,owner_id,agent_id,actor,manifest,digest,summary,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                rid,
                who["user_id"],
                who.get("agent_id", who.get("token_id")),
                who["actor"],
                encode(manifest),
                digest,
                summary,
                int(time.time()) + 900,
                now(),
            ),
        )
        return 201, public(row_for(service, c, rid))
    if len(parts) == 3 and method == "POST" and parts[2] == "decision":
        fields(body, {"decision"})
        row = row_for(service, c, parts[1])
        if who.get("bearer"):
            raise Error(403, "human_required", "A signed-in human must decide.")
        if row["state"] != "pending" or row["expires_at"] <= int(time.time()):
            raise Error(
                409, "approval_changed", "Request expired or was already decided."
            )
        if body.get("decision") not in ("approve", "deny"):
            raise Error(422, "decision_required", "Choose approve or deny.")
        manifest = json.loads(row["manifest"])
        service.access.write(
            service, c, manifest["method"], manifest["path"], manifest["body"]
        )
        if (
            body["decision"] == "approve"
            and who.get("human_approval_digest") != row["digest"]
        ):
            raise Error(
                403,
                "human_verification_required",
                "Confirm this exact request with your passkey.",
            )
        c.execute(
            "UPDATE approval_requests SET state=?,decided_by=?,decided_at=? WHERE id=?",
            (
                "approved" if body["decision"] == "approve" else "denied",
                who["user_id"],
                now(),
                row["id"],
            ),
        )
        return 200, public(row_for(service, c, row["id"]))
    if len(parts) == 3 and method == "POST" and parts[2] == "execute":
        fields(body, set())
        row = row_for(service, c, parts[1])
        if not who.get("bearer"):
            raise Error(
                403,
                "agent_required",
                "The requesting agent executes its approved operation.",
            )
        manifest = json.loads(row["manifest"])
        service.access.write(
            service, c, manifest["method"], manifest["path"], manifest["body"]
        )
        if row["state"] == "executed":
            return 200, json.loads(row["result"])
        if row["state"] != "approved" or row["expires_at"] <= int(time.time()):
            raise Error(
                403,
                "approval_required",
                "A current approval is required for this operation.",
            )
        if manifest.get("parent"):
            parent = service.public_task(c, manifest["parent"]["id"])
            if parent["version"] != manifest["parent"]["version"]:
                raise Error(
                    409,
                    "approval_changed",
                    "The affected parent changed. Request approval again.",
                )
        # Same SQLite transaction as the mutation and idempotency receipt.
        status, result = service._mutate(
            c,
            manifest["method"],
            manifest["path"],
            manifest["body"],
            dict(
                context,
                approved_task_action=(
                    manifest["task_id"],
                    manifest["operation"].split(".")[1],
                ),
            ),
            None,
        )
        c.execute(
            "UPDATE approval_requests SET state='executed',executed_at=?,result=? WHERE id=?",
            (now(), encode(result), row["id"]),
        )
        return status, result
    raise Error(404, "not_found", "Approval operation unavailable.")


def enforce(service, method, path):
    if (
        service.access
        and service.access.identity.get("bearer")
        and method == "POST"
        and re.fullmatch(
            r"/api/v1/tasks/[^/]+/(archive|reopen|restore|reassign|backlog)", path
        )
    ):
        raise Error(
            403,
            "approval_required",
            "Request human approval, then execute through /api/v1/approval-requests/{id}/execute.",
        )
