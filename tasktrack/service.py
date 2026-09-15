"""The single authority for HTTP and CLI reads, validation and mutations."""

import base64
import hashlib
import json
import re
import uuid
from urllib.parse import quote, urlparse

from . import __version__
from .db import SCHEMA_VERSION, Error, encode, now

STATUSES = ("backlog", "in_progress", "review", "done")
PRIORITIES = ("low", "normal", "high", "urgent")
TASK_FIELDS = {
    "title",
    "description_markdown",
    "acceptance_criteria",
    "priority",
    "assignee",
    "parent_id",
    "dependency_ids",
    "thread_links",
}
ACTION_FIELDS = {
    "claim": set(),
    "resume": set(),
    "checkpoint": {"checkpoint"},
    "handoff": {"checkpoint", "to"},
    "reassign": {"assignee", "reason"},
    "block": {"reason"},
    "unblock": {"reason"},
    "submit": {"result"},
    "complete": {"acceptance_note"},
    "request-changes": {"reason"},
    "reopen": {"reason", "reopen_parent"},
    "archive": {"reason"},
    "restore": {"reason"},
    "move": {
        "status",
        "before_id",
        "reason",
        "checkpoint",
        "result",
        "acceptance_note",
        "reopen_parent",
    },
}


def invalid(field, message):
    raise Error(422, "validation", message, {field: message})


def fields(body, allowed):
    if not isinstance(body, dict):
        raise Error(400, "invalid_body", "Expected a JSON object.")
    unknown = set(body) - set(allowed)
    if unknown:
        invalid(sorted(unknown)[0], "Unknown field(s): " + ", ".join(sorted(unknown)))


def string(value, field, required=False, maximum=100000):
    if not isinstance(value, str):
        invalid(field, f"{field} must be text.")
    value = value.strip()
    if required and not value:
        invalid(field, f"{field} is required.")
    if len(value) > maximum:
        invalid(field, f"{field} must be at most {maximum} characters.")
    return value


def integer(value, field):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        invalid(field, f"{field} must be a positive integer.")
    return value


def actor_name(value, field="assignee"):
    return None if value is None else string(value, field, True, 200)


def texts(value, field):
    if not isinstance(value, list) or len(value) > 200:
        invalid(field, f"{field} must be a list of at most 200 entries.")
    return [string(x, field, True, 4000) for x in value]


def evidence(value, field="evidence", required=False):
    if not isinstance(value, list) or len(value) > 100 or (required and not value):
        invalid(
            field,
            f"{field} must contain {'at least one' if required else 'a list of'} evidence link(s).",
        )
    result = []
    for item in value:
        fields(item, {"label", "uri"})
        label = string(item.get("label"), field + ".label", True, 500)
        uri = string(item.get("uri"), field + ".uri", True, 4000)
        if urlparse(uri).scheme not in {"https", "http", "file"}:
            invalid(field, "Evidence links must use https, http or file URLs.")
        result.append({"label": label, "uri": uri})
    return result


def checkpoint(value):
    if not isinstance(value, dict):
        invalid(
            "checkpoint", "Supply a structured checkpoint with summary and next_action."
        )
    fields(
        value,
        {
            "summary",
            "next_action",
            "workspace",
            "branch",
            "commit",
            "not_applicable_reason",
            "acceptance_remaining",
            "evidence",
        },
    )
    result = {
        k: string(value.get(k, ""), "checkpoint." + k, k in {"summary", "next_action"})
        for k in (
            "summary",
            "next_action",
            "workspace",
            "branch",
            "commit",
            "not_applicable_reason",
        )
    }
    if not result["not_applicable_reason"] and (
        not result["workspace"] or not (result["branch"] or result["commit"])
    ):
        invalid(
            "checkpoint",
            "Supply a workspace and branch or commit for code work, or an explicit not_applicable_reason.",
        )
    result["acceptance_remaining"] = texts(
        value.get("acceptance_remaining", []), "acceptance_remaining"
    )
    result["evidence"] = evidence(value.get("evidence", []))
    return result


class Service:
    def __init__(self, store, base_url="http://127.0.0.1:7777"):
        self.store, self.base_url = store, base_url.rstrip("/")
        with store.connection() as c:
            self.instance_id = c.execute("SELECT id FROM instance").fetchone()[0]
        config = store.directory / "config.json"
        self.config = json.loads(config.read_text()) if config.exists() else {}

    def project(self, c, identifier):
        if str(identifier).isdigit():
            row = c.execute(
                "SELECT * FROM projects WHERE id=?", (int(identifier),)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT p.* FROM projects p JOIN project_aliases a ON a.project_id=p.id WHERE a.key=?",
                (str(identifier).upper(),),
            ).fetchone()
        if row is None:
            raise Error(404, "not_found", f"Project {identifier} does not exist.")
        value = dict(row)
        value["document_links"] = json.loads(value["document_links"])
        value["aliases"] = [
            r[0]
            for r in c.execute(
                "SELECT key FROM project_aliases WHERE project_id=? ORDER BY key",
                (value["id"],),
            )
        ]
        value["url"] = f"{self.base_url}/projects/{value['key']}"
        return value

    def task(self, c, identifier):
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9]{0,9})-(\d+)", str(identifier))
        project_id = self.project(c, match[1])["id"] if match else None
        task_id = (
            int(match[2])
            if match
            else int(identifier)
            if str(identifier).isdigit()
            else 0
        )
        row = (
            c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if 0 < task_id <= 9223372036854775807
            else None
        )
        if row is None or (project_id is not None and row["project_id"] != project_id):
            raise Error(404, "not_found", f"Task {identifier} does not exist.")
        return {**dict(row), **json.loads(row["data"])} | {"data": None}

    def public_task(self, c, identifier):
        task = self.task(c, identifier)
        task.pop("data", None)
        project = self.project(c, task["project_id"])
        task.update(
            instance_id=self.instance_id,
            project_key=project["key"],
            reference=f"{project['key']}-{task['id']}",
            url=f"{self.base_url}/tasks/{task['id']}",
        )
        dependencies = [
            dict(r)
            for r in c.execute(
                "SELECT t.id,t.status,t.archived_at,t.data FROM dependencies d JOIN tasks t ON t.id=d.prerequisite_id WHERE d.task_id=? ORDER BY t.id",
                (task["id"],),
            )
        ]
        task["dependency_ids"] = [r["id"] for r in dependencies]
        task["dependencies"] = [
            {
                "id": r["id"],
                "title": json.loads(r["data"])["title"],
                "status": r["status"],
                "archived_at": r["archived_at"],
            }
            for r in dependencies
        ]
        task["blockers"] = (
            [task["blocker_reason"]] if task["blocker_reason"] else []
        ) + [
            f"Waiting for {project['key']}-{r['id']}"
            for r in dependencies
            if r["status"] != "done"
        ]
        task["blocked"] = bool(task["blockers"])
        task["pickup_needed"] = (
            task["status"] == "in_progress"
            and task["kind"] == "task"
            and not task["execution"]
        )
        links = [
            dict(r)
            for r in c.execute(
                "SELECT source,project,thread_id,is_primary AS 'primary' FROM thread_links WHERE task_id=? ORDER BY is_primary DESC,thread_id,source,project",
                (task["id"],),
            )
        ]
        for link in links:
            link["primary"] = bool(link["primary"])
            template = (
                self.config.get("inbox_instances", {})
                .get(link["source"], {})
                .get("thread_url_template")
            )
            link["url"] = None
            if template and urlparse(template).scheme in {"http", "https"}:
                try:
                    link["url"] = template.format(
                        **{k: quote(str(v), safe="") for k, v in link.items()}
                    )
                except (ValueError, KeyError):
                    pass
        task["thread_links"] = links
        if task["parent_id"]:
            parent = self.task(c, task["parent_id"])
            task["epic"] = {
                "id": parent["id"],
                "title": parent["title"],
                "status": parent["status"],
            }
        else:
            task["epic"] = None
        if task["kind"] == "epic":
            rows = c.execute(
                "SELECT status,COUNT(*) FROM tasks WHERE parent_id=? AND archived_at IS NULL GROUP BY status",
                (task["id"],),
            ).fetchall()
            counts = dict(rows)
            task["progress"] = {
                "done": counts.get("done", 0),
                "total": sum(counts.values()),
            }
            task["children_url"] = (
                f"/api/v1/tasks?project_id={task['project_id']}&parent_id={task['id']}"
            )
        return task

    def event(self, c, kind, entity_id, project_id, operation, context, detail):
        c.execute(
            "INSERT INTO events(entity_type,entity_id,project_id,actor,session,via,operation,created_at,detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                kind,
                entity_id,
                project_id,
                context["actor"],
                context["session"],
                context["via"],
                operation,
                now(),
                encode(detail),
            ),
        )

    def check_version(self, body, record):
        version = integer(body.get("expected_version"), "expected_version")
        if version != record["version"]:
            raise Error(
                409,
                "version_conflict",
                f"Record {record['id']} changed. Fetch the current record before retrying.",
                current_version=record["version"],
                current_url=record.get(
                    "url", f"{self.base_url}/api/v1/tasks/{record['id']}"
                ),
            )

    def mutate(
        self,
        method,
        path,
        body,
        actor,
        request_key,
        session=None,
        via="api",
        upload=None,
    ):
        if not actor or not str(actor).strip():
            raise Error(
                400,
                "missing_actor",
                "Supply X-Actor or --as / TT_ACTOR for every mutation.",
            )
        actor = string(actor, "actor", True, 200)
        if not request_key:
            raise Error(
                400,
                "missing_idempotency_key",
                "Supply Idempotency-Key or --request-id.",
            )
        request_key = string(request_key, "Idempotency-Key", True, 200)
        if via not in {"api", "ui", "cli"}:
            invalid("via", "X-Via must be api, ui or cli.")
        if session is not None:
            session = string(session, "session", True, 200)
        if not isinstance(body, dict):
            raise Error(400, "invalid_body", "Expected a JSON object.")
        if upload is not None:
            if len(upload) > 10 * 1024 * 1024:
                raise Error(413, "upload_too_large", "Files must be 10 MiB or smaller.")
            body = {
                **body,
                "sha256": hashlib.sha256(upload).hexdigest(),
                "size": len(upload),
            }
        fingerprint = hashlib.sha256(
            encode(
                {
                    "method": method,
                    "path": path,
                    "body": body,
                    "session": session,
                    "via": via,
                }
            ).encode()
        ).hexdigest()
        context = {"actor": actor, "session": session, "via": via}
        with self.store.connection(write=True) as c:
            receipt = c.execute(
                "SELECT * FROM receipts WHERE actor=? AND request_key=?",
                (actor, request_key),
            ).fetchone()
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise Error(
                        409,
                        "idempotency_conflict",
                        "This request key was already used for different content or caller context.",
                    )
                return receipt["status"], json.loads(receipt["response"])
            status, result = self._mutate(c, method, path, body, context, upload)
            c.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?,?)",
                (actor, request_key, fingerprint, status, encode(result), now()),
            )
            return status, result

    def _mutate(self, c, method, path, body, context, upload):
        parts = path.strip("/").split("/")
        if parts[:2] != ["api", "v1"]:
            raise Error(404, "not_found", "Use the versioned /api/v1 routes.")
        parts = parts[2:]
        if parts == ["projects"] and method == "POST":
            return 201, self.write_project(c, None, body, context)
        if len(parts) == 2 and parts[0] == "projects" and method == "PATCH":
            return 200, self.write_project(c, self.project(c, parts[1]), body, context)
        if parts == ["tasks"] and method == "POST":
            return 201, self.create_task(c, body, context)
        if len(parts) in {2, 3} and parts[0] == "tasks":
            task = self.public_task(c, parts[1])
            if len(parts) == 2 and method == "PATCH":
                return 200, self.edit_task(c, task, body, context)
            if len(parts) == 3 and method == "POST":
                action = parts[2]
                if action in {"comments", "attachments"}:
                    return 201, self.append(c, task, action, body, context, upload)
                if action not in ACTION_FIELDS:
                    raise Error(404, "unknown_action", "Unknown task action: " + action)
                fields(body, ACTION_FIELDS[action] | {"expected_version"})
                self.check_version(body, task)
                return 200, self.action(c, task, action, body, context)
        raise Error(404, "not_found", "No matching write route.")

    def write_project(self, c, previous, body, context):
        fields(
            body,
            {"key", "name", "brief_markdown", "document_links"}
            | ({"expected_version"} if previous else set()),
        )
        if previous:
            self.check_version(body, previous)
        data = {
            k: body.get(
                k, previous[k] if previous else ("" if k != "document_links" else [])
            )
            for k in ("key", "name", "brief_markdown", "document_links")
        }
        data["key"] = string(data["key"], "key", True, 10).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", data["key"]):
            invalid("key", "Use 1–10 letters or numbers, starting with a letter.")
        data["name"] = string(data["name"], "name", True, 200)
        data["brief_markdown"] = string(data["brief_markdown"], "brief_markdown")
        data["document_links"] = evidence(data["document_links"], "document_links")
        alias = c.execute(
            "SELECT project_id FROM project_aliases WHERE key=?", (data["key"],)
        ).fetchone()
        if alias and (not previous or alias[0] != previous["id"]):
            raise Error(
                409,
                "key_conflict",
                "That project key or historical alias already belongs to another project.",
                {"key": "Choose an unused key."},
            )
        if previous and all(previous[k] == v for k, v in data.items()):
            return previous
        timestamp = now()
        if previous:
            project_id = previous["id"]
            c.execute(
                "UPDATE projects SET key=?,name=?,brief_markdown=?,document_links=?,version=version+1,updated_by=?,updated_via=?,updated_at=? WHERE id=?",
                (
                    data["key"],
                    data["name"],
                    data["brief_markdown"],
                    encode(data["document_links"]),
                    context["actor"],
                    context["via"],
                    timestamp,
                    project_id,
                ),
            )
        else:
            project_id = c.execute(
                "INSERT INTO projects(key,name,brief_markdown,document_links,version,created_by,created_via,created_at,updated_by,updated_via,updated_at) VALUES (?,?,?,?,1,?,?,?,?,?,?)",
                (
                    data["key"],
                    data["name"],
                    data["brief_markdown"],
                    encode(data["document_links"]),
                    context["actor"],
                    context["via"],
                    timestamp,
                    context["actor"],
                    context["via"],
                    timestamp,
                ),
            ).lastrowid
        c.execute(
            "INSERT OR IGNORE INTO project_aliases VALUES (?,?)",
            (data["key"], project_id),
        )
        result = self.project(c, project_id)
        operation = (
            "create"
            if previous is None
            else "rename"
            if previous["key"] != data["key"] or previous["name"] != data["name"]
            else "update"
        )
        self.event(
            c,
            "project",
            project_id,
            project_id,
            operation,
            context,
            {"before": previous, "after": result},
        )
        return result

    def task_metadata(self, c, data, project_id, kind, task_id=None):
        data["title"] = string(data.get("title", ""), "title", True, 120)
        data["description_markdown"] = string(
            data.get("description_markdown", ""), "description_markdown"
        )
        data["acceptance_criteria"] = texts(
            data.get("acceptance_criteria", []), "acceptance_criteria"
        )
        if data.get("priority", "normal") not in PRIORITIES:
            invalid("priority", "Choose low, normal, high or urgent.")
        data["priority"] = data.get("priority", "normal")
        data["assignee"] = actor_name(data.get("assignee"))
        parent_id = data.get("parent_id")
        if parent_id is not None:
            integer(parent_id, "parent_id")
            parent = self.task(c, parent_id)
            if (
                kind != "task"
                or parent["kind"] != "epic"
                or parent["project_id"] != project_id
                or parent["archived_at"]
            ):
                invalid(
                    "parent_id",
                    "An ordinary task may belong to one unarchived epic in the same project; epics cannot nest.",
                )
            if parent["status"] == "done" and (
                not task_id or self.task(c, task_id)["parent_id"] != parent_id
            ):
                invalid(
                    "parent_id", "Reopen the completed epic before adding children."
                )
        data["parent_id"] = parent_id
        dependencies = data.get("dependency_ids", [])
        if not isinstance(dependencies, list) or len(dependencies) > 200:
            invalid("dependency_ids", "Provide a list of at most 200 task IDs.")
        for dependency in dependencies:
            integer(dependency, "dependency_ids")
            target = self.task(c, dependency)
            if (
                dependency == task_id
                or target["kind"] != "task"
                or target["project_id"] != project_id
                or (target["archived_at"] and target["status"] != "done")
            ):
                invalid(
                    "dependency_ids",
                    "Dependencies must be other ordinary tasks in this project, available or already done.",
                )
            if (
                task_id
                and c.execute(
                    "WITH RECURSIVE ancestors(id) AS (SELECT prerequisite_id FROM dependencies WHERE task_id=? UNION SELECT d.prerequisite_id FROM dependencies d JOIN ancestors a ON d.task_id=a.id) SELECT 1 FROM ancestors WHERE id=?",
                    (dependency, task_id),
                ).fetchone()
            ):
                invalid("dependency_ids", "This dependency would create a cycle.")
        data["dependency_ids"] = sorted(set(dependencies))
        links = data.get("thread_links", [])
        if not isinstance(links, list) or len(links) > 100:
            invalid("thread_links", "Provide a list of at most 100 thread links.")
        cleaned = []
        for link in links:
            fields(link, {"source", "project", "thread_id", "primary"})
            item = {
                k: string(link.get(k), "thread_links." + k, True, 200)
                for k in ("source", "project", "thread_id")
            }
            try:
                uuid.UUID(item["source"])
            except ValueError:
                invalid(
                    "thread_links.source",
                    "Use the persistent UUID of the inbox database.",
                )
            if type(link.get("primary", False)) is not bool:
                invalid("thread_links.primary", "primary must be true or false.")
            item["primary"] = link.get("primary", False)
            cleaned.append(item)
        if sum(x["primary"] for x in cleaned) > 1 or len(
            {(x["source"], x["project"], x["thread_id"]) for x in cleaned}
        ) != len(cleaned):
            invalid(
                "thread_links",
                "Thread links must be unique, with at most one primary link.",
            )
        data["thread_links"] = sorted(
            cleaned,
            key=lambda x: (not x["primary"], x["thread_id"], x["source"], x["project"]),
        )
        return data

    def relations(self, c, task_id, data):
        c.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
        c.executemany(
            "INSERT INTO dependencies VALUES (?,?)",
            [(task_id, x) for x in data["dependency_ids"]],
        )
        c.execute("DELETE FROM thread_links WHERE task_id=?", (task_id,))
        c.executemany(
            "INSERT INTO thread_links VALUES (?,?,?,?,?)",
            [
                (task_id, x["source"], x["project"], x["thread_id"], x["primary"])
                for x in data["thread_links"]
            ],
        )

    def create_task(self, c, body, context):
        fields(body, TASK_FIELDS | {"project_id", "kind"})
        project_id = integer(body.get("project_id"), "project_id")
        self.project(c, project_id)
        kind = body.get("kind", "task")
        if kind not in ("task", "epic"):
            invalid("kind", "kind must be task or epic.")
        data = self.task_metadata(c, dict(body), project_id, kind)
        timestamp = now()
        data.update(
            execution=None,
            blocker_reason=None,
            checkpoint=None,
            result=None,
            completion=None,
            created_by=context["actor"],
            created_via=context["via"],
            created_at=timestamp,
            updated_by=context["actor"],
            updated_via=context["via"],
            updated_at=timestamp,
        )
        position = c.execute(
            "SELECT COALESCE(MAX(position),0)+1024 FROM tasks WHERE project_id=? AND status='backlog'",
            (project_id,),
        ).fetchone()[0]
        stored = self.content(data)
        task_id = c.execute(
            "INSERT INTO tasks(project_id,kind,parent_id,status,assignee,position,version,data) VALUES (?,?,?,'backlog',?,?,1,?)",
            (
                project_id,
                kind,
                data["parent_id"],
                data["assignee"],
                position,
                encode(stored),
            ),
        ).lastrowid
        self.relations(c, task_id, data)
        result = self.public_task(c, task_id)
        self.event(c, "task", task_id, project_id, "create", context, {"after": result})
        return result

    def content(self, task):
        return {
            k: task[k]
            for k in (
                "title",
                "description_markdown",
                "acceptance_criteria",
                "priority",
                "execution",
                "blocker_reason",
                "checkpoint",
                "result",
                "completion",
                "created_by",
                "created_via",
                "created_at",
                "updated_by",
                "updated_via",
                "updated_at",
            )
        }

    def save(self, c, before, after, operation, context, detail=None):
        if all(
            before.get(k) == after.get(k)
            for k in [
                *self.content(after),
                "parent_id",
                "status",
                "assignee",
                "position",
                "archived_at",
                "dependency_ids",
                "thread_links",
            ]
        ):
            return self.public_task(c, before["id"])
        after.update(
            updated_by=context["actor"], updated_via=context["via"], updated_at=now()
        )
        c.execute(
            "UPDATE tasks SET parent_id=?,status=?,assignee=?,position=?,archived_at=?,version=version+1,data=? WHERE id=?",
            (
                after["parent_id"],
                after["status"],
                after["assignee"],
                after["position"],
                after["archived_at"],
                encode(self.content(after)),
                before["id"],
            ),
        )
        result = self.public_task(c, before["id"])
        self.event(
            c,
            "task",
            before["id"],
            before["project_id"],
            operation,
            context,
            {"before": before, "after": result, **(detail or {})},
        )
        return result

    def edit_task(self, c, task, body, context):
        fields(body, TASK_FIELDS | {"expected_version"})
        self.check_version(body, task)
        if task["archived_at"]:
            invalid("archived_at", "Restore this task before editing it.")
        if (
            "assignee" in body
            and actor_name(body["assignee"]) != task["assignee"]
            and task["execution"]
        ):
            raise Error(
                409,
                "claim_conflict",
                "Use explicit reassign with a reason to change claimed ownership.",
            )
        after = dict(task)
        after["thread_links"] = [
            {k: v for k, v in x.items() if k != "url"} for x in after["thread_links"]
        ]
        after.update({k: v for k, v in body.items() if k != "expected_version"})
        after = self.task_metadata(
            c, after, task["project_id"], task["kind"], task["id"]
        )
        # Normalize presentation-only link URLs for no-op comparison.
        before = dict(
            task,
            thread_links=[
                {k: v for k, v in x.items() if k != "url"} for x in task["thread_links"]
            ],
        )
        self.relations(c, task["id"], after)
        return self.save(c, before, after, "update", context)

    def owner(self, task, context):
        execution = task["execution"]
        if execution and (
            execution["actor"] != context["actor"]
            or execution["session"] != context["session"]
        ):
            raise Error(
                409,
                "claim_conflict",
                "This action requires the claiming actor and session. Resume or explicitly reassign first.",
            )
        if not execution and task["assignee"] != context["actor"]:
            raise Error(
                409,
                "assignee_conflict",
                "The assignee must perform this action; use explicit reassignment first.",
            )

    def unblocked(self, task):
        if task["blocked"]:
            invalid(
                "blockers",
                "Resolve blockers before starting, submitting or completing work: "
                + "; ".join(task["blockers"]),
            )

    def ready(self, task):
        missing = {}
        if not task["description_markdown"].strip():
            missing["description_markdown"] = "Add a description before starting work."
        if not task["acceptance_criteria"]:
            missing["acceptance_criteria"] = (
                "Add at least one acceptance criterion before starting work."
            )
        if missing:
            raise Error(
                422,
                "not_ready",
                "This draft needs a description and acceptance criteria before work can start.",
                missing,
            )
        self.unblocked(task)

    def action(self, c, task, action, body, context):
        after = dict(task)
        if task["archived_at"] and action != "restore":
            invalid("archived_at", "Restore this task before changing it.")
        actual = action
        if action == "move":
            status = body.get("status")
            if status not in STATUSES:
                invalid("status", "Choose backlog, in_progress, review or done.")
            actual = {
                ("backlog", "in_progress"): "start",
                ("in_progress", "review"): "submit",
                ("review", "done"): "complete",
                ("review", "in_progress"): "request-changes",
                ("in_progress", "backlog"): "backlog",
                ("review", "backlog"): "backlog",
                ("done", "backlog"): "reopen",
            }.get(
                (task["status"], status),
                "reorder" if status == task["status"] else "invalid",
            )
        allowed = {
            "start": {"backlog"},
            "claim": {"backlog", "in_progress"},
            "resume": {"in_progress"},
            "checkpoint": {"in_progress"},
            "handoff": {"in_progress"},
            "submit": {"in_progress"},
            "complete": {"review"},
            "request-changes": {"review"},
            "backlog": {"in_progress", "review"},
            "reopen": {"done"},
        }
        if actual == "invalid" or (
            actual in allowed and task["status"] not in allowed[actual]
        ):
            invalid("status", f"Cannot {actual} work in {task['status']}.")
        if actual in {"claim", "resume"}:
            if task["kind"] == "epic":
                invalid(
                    "kind",
                    "Epics track child progress and cannot hold execution claims.",
                )
            if not context["session"]:
                invalid(
                    "session", "Supply X-Session or --session for a claim or resume."
                )
            if actual == "claim":
                if task["execution"]:
                    raise Error(
                        409,
                        "claim_conflict",
                        "This task is already claimed. The same actor may explicitly resume it.",
                    )
                if task["assignee"] not in {None, context["actor"]}:
                    raise Error(
                        409,
                        "assignee_conflict",
                        "This task is assigned to another actor. Explicit reassignment is required.",
                    )
                self.ready(task)
                after.update(
                    assignee=context["actor"],
                    status="in_progress",
                    execution={
                        "actor": context["actor"],
                        "session": context["session"],
                        "claimed_at": now(),
                    },
                )
            else:
                if (
                    not task["execution"]
                    or task["execution"]["actor"] != context["actor"]
                ):
                    raise Error(
                        409,
                        "claim_conflict",
                        "Only the existing claimant may resume. Unclaimed work needs claim.",
                    )
                if task["execution"]["session"] != context["session"]:
                    after["execution"] = {
                        **task["execution"],
                        "session": context["session"],
                        "resumed_at": now(),
                    }
        elif actual == "start":
            self.ready(task)
            if not task["assignee"]:
                invalid("assignee", "Assign this task before starting it manually.")
            after["status"] = "in_progress"
        elif actual in {"checkpoint", "handoff", "submit", "backlog"}:
            self.owner(task, context)
            if actual in {"checkpoint", "handoff", "backlog"}:
                after["checkpoint"] = checkpoint(body.get("checkpoint"))
            if actual == "handoff":
                after["execution"] = None
                if "to" in body:
                    after["assignee"] = actor_name(body["to"], "to")
            if actual == "backlog":
                string(body.get("reason"), "reason", True)
                after.update(status="backlog", execution=None)
            if actual == "submit":
                self.unblocked(task)
                result = body.get("result")
                if not isinstance(result, dict):
                    invalid(
                        "result",
                        "Supply a result summary and evidence before submitting for review.",
                    )
                fields(result, {"summary", "evidence"})
                after.update(
                    status="review",
                    execution=None,
                    result={
                        "summary": string(
                            result.get("summary"), "result.summary", True
                        ),
                        "evidence": evidence(result.get("evidence"), required=True),
                    },
                )
        elif actual == "complete":
            self.unblocked(task)
            if not task["result"] or not task["result"].get("evidence"):
                invalid("result", "Review requires retained result evidence.")
            if task["kind"] == "epic":
                children = c.execute(
                    "SELECT status,archived_at FROM tasks WHERE parent_id=?",
                    (task["id"],),
                ).fetchall()
                if not children or any(r["status"] != "done" for r in children):
                    invalid(
                        "children",
                        "An epic needs at least one child and all children done. Archiving unfinished work does not satisfy it.",
                    )
            after.update(
                status="done",
                completion={
                    "actor": context["actor"],
                    "acceptance_note": string(
                        body.get("acceptance_note"), "acceptance_note", True
                    ),
                    "completed_at": now(),
                },
            )
        elif actual in {"request-changes", "reopen"}:
            reason = string(body.get("reason"), "reason", True)
            if actual == "reopen" and task["parent_id"]:
                parent = self.public_task(c, task["parent_id"])
                if parent["status"] == "done":
                    if body.get("reopen_parent") is not True:
                        invalid(
                            "reopen_parent",
                            "Explicitly set reopen_parent: true to reopen this completed epic in the same transaction.",
                        )
                    if parent["archived_at"]:
                        invalid(
                            "parent_id",
                            "Restore the archived epic before reopening its child.",
                        )
                    self.save(
                        c,
                        parent,
                        dict(parent, status="backlog"),
                        "reopen",
                        context,
                        {"reason": reason, "reopened_by_child": task["id"]},
                    )
            after.update(
                status="in_progress" if actual == "request-changes" else "backlog",
                execution=None,
            )
        elif actual == "reassign":
            string(body.get("reason"), "reason", True)
            if "assignee" not in body:
                invalid("assignee", "Supply an assignee, or explicit null to unassign.")
            after.update(assignee=actor_name(body["assignee"]), execution=None)
        elif actual in {"block", "unblock"}:
            reason = string(body.get("reason"), "reason", True)
            after["blocker_reason"] = reason if actual == "block" else None
        elif actual in {"archive", "restore"}:
            string(body.get("reason"), "reason", True)
            if actual == "archive":
                if task["execution"]:
                    raise Error(
                        409,
                        "claim_conflict",
                        "Handoff or reassign claimed work before archiving.",
                    )
                if task["status"] != "done" and (
                    task["parent_id"]
                    or c.execute(
                        "SELECT 1 FROM dependencies WHERE prerequisite_id=?",
                        (task["id"],),
                    ).fetchone()
                ):
                    invalid(
                        "relationships",
                        "Resolve parent/dependency relationships before archiving unfinished work that other work relies on.",
                    )
                if (
                    task["kind"] == "epic"
                    and c.execute(
                        "SELECT 1 FROM tasks WHERE parent_id=? AND status!='done'",
                        (task["id"],),
                    ).fetchone()
                ):
                    invalid(
                        "children",
                        "Resolve unfinished children before archiving this epic.",
                    )
                after["archived_at"] = task["archived_at"] or now()
            else:
                after["archived_at"] = None
        elif actual != "reorder":
            invalid("action", "Unsupported action.")
        if task["status"] != after["status"] or (
            action == "move" and "before_id" in body
        ):
            self.order(c, task, after, body.get("before_id"), context)
        return self.save(
            c,
            task,
            after,
            action if action != "move" else actual,
            context,
            {"reason": body.get("reason")},
        )

    def order(self, c, before, after, anchor, context):
        if anchor is not None:
            integer(anchor, "before_id")
            target = self.task(c, anchor)
            if (
                anchor == before["id"]
                or target["project_id"] != before["project_id"]
                or target["status"] != after["status"]
                or target["archived_at"]
            ):
                invalid(
                    "before_id",
                    "The ordering anchor must be another unarchived task in this project and destination column.",
                )
        ids = [
            r[0]
            for r in c.execute(
                "SELECT id FROM tasks WHERE project_id=? AND status=? AND archived_at IS NULL ORDER BY position,id",
                (before["project_id"], after["status"]),
            )
            if r[0] != before["id"]
        ]
        index = ids.index(anchor) if anchor is not None else len(ids)
        ids.insert(index, before["id"])
        current = [
            r[0]
            for r in c.execute(
                "SELECT id FROM tasks WHERE project_id=? AND status=? AND archived_at IS NULL ORDER BY position,id",
                (before["project_id"], after["status"]),
            )
        ]
        if ids == current:
            return
        # Integer gaps avoid renumbering other cards on normal moves. Very dense
        # columns are rebased with an audited/versioned update for each changed row.
        positions = {
            r["id"]: r["position"]
            for r in c.execute(
                "SELECT id,position FROM tasks WHERE project_id=? AND status=?",
                (before["project_id"], after["status"]),
            )
        }
        left = positions[ids[index - 1]] if index else 0
        right = positions[ids[index + 1]] if index + 1 < len(ids) else left + 2048
        if right - left > 1:
            after["position"] = (left + right) // 2
        else:
            for i, identifier in enumerate(ids, 1):
                position = i * 1024
                if identifier == before["id"]:
                    after["position"] = position
                elif positions[identifier] != position:
                    item = self.public_task(c, identifier)
                    self.save(
                        c, item, dict(item, position=position), "order-rebase", context
                    )

    def append(self, c, task, action, body, context, upload):
        if action == "comments":
            fields(body, {"body"})
            text = string(body.get("body"), "body", True)
            identifier = c.execute(
                "INSERT INTO comments(task_id,actor,session,via,body,created_at) VALUES (?,?,?,?,?,?)",
                (
                    task["id"],
                    context["actor"],
                    context["session"],
                    context["via"],
                    text,
                    now(),
                ),
            ).lastrowid
            result = dict(
                c.execute("SELECT * FROM comments WHERE id=?", (identifier,)).fetchone()
            )
        else:
            fields(body, {"filename", "media_type", "comment_id", "sha256", "size"})
            if upload is None:
                invalid("file", "Upload a file using multipart form data.")
            filename = string(body.get("filename"), "filename", True, 500)
            media_type = string(
                body.get("media_type", "application/octet-stream"),
                "media_type",
                True,
                200,
            )
            comment_id = body.get("comment_id")
            if comment_id is not None:
                integer(comment_id, "comment_id")
                if not c.execute(
                    "SELECT 1 FROM comments WHERE id=? AND task_id=?",
                    (comment_id, task["id"]),
                ).fetchone():
                    invalid("comment_id", "The comment must belong to this task.")
            digest = self.store.put_blob(upload)
            identifier = c.execute(
                "INSERT INTO attachments(task_id,comment_id,sha256,stored_name,filename,size,media_type,actor,session,via,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task["id"],
                    comment_id,
                    digest,
                    digest,
                    filename,
                    len(upload),
                    media_type,
                    context["actor"],
                    context["session"],
                    context["via"],
                    now(),
                ),
            ).lastrowid
            result = dict(
                c.execute(
                    "SELECT * FROM attachments WHERE id=?", (identifier,)
                ).fetchone()
            )
            result["download_url"] = f"/api/v1/attachments/{identifier}/content"
        self.event(
            c,
            "task",
            task["id"],
            task["project_id"],
            "comment" if action == "comments" else "attachment",
            context,
            result,
        )
        return result

    def page_options(self, query, allowed):
        fields(query, set(allowed) | {"limit", "cursor"})
        try:
            limit = int(query.get("limit", 50))
        except (ValueError, TypeError):
            invalid("limit", "limit must be an integer from 1 to 200.")
        if not 1 <= limit <= 200:
            invalid("limit", "limit must be between 1 and 200.")
        return limit

    def page(self, items, query, scope, limit, extra=None):
        signature = hashlib.sha256(
            encode(
                {
                    "scope": scope,
                    "query": {
                        k: v for k, v in query.items() if k not in {"cursor", "limit"}
                    },
                }
            ).encode()
        ).hexdigest()[:16]
        offset = 0
        if query.get("cursor"):
            try:
                token = json.loads(base64.urlsafe_b64decode(query["cursor"]).decode())
                if token["source"] != self.instance_id:
                    raise Error(
                        409,
                        "wrong_source",
                        "This cursor belongs to another Tasktrack instance.",
                    )
                if (
                    token["query"] != signature
                    or type(token["offset"]) is not int
                    or token["offset"] < 0
                ):
                    raise ValueError()
                offset = token["offset"]
            except (ValueError, KeyError, TypeError):
                raise Error(
                    400,
                    "invalid_cursor",
                    "Use a continuation cursor from this same filtered list.",
                )
        end = offset + limit
        more = end < len(items)
        cursor = (
            base64.urlsafe_b64encode(
                encode(
                    {"source": self.instance_id, "query": signature, "offset": end}
                ).encode()
            ).decode()
            if more
            else None
        )
        return {
            "items": items[offset:end],
            "next_cursor": cursor,
            "has_more": more,
            "total": len(items),
            **(extra or {}),
        }

    def read(self, path, query=None, actor=None):
        query = query or {}
        with self.store.connection() as c:
            c.execute("BEGIN")  # Every compound read observes one coherent snapshot.
            parts = path.strip("/").split("/")
            if parts[:2] != ["api", "v1"]:
                raise Error(404, "not_found", "Use /api/v1.")
            parts = parts[2:]
            if parts == ["health"]:
                fields(query, set())
                return {
                    "version": __version__,
                    "schema_version": SCHEMA_VERSION,
                    "instance_id": self.instance_id,
                }
            if parts == ["projects"]:
                limit = self.page_options(query, set())
                return self.page(
                    [
                        self.project(c, r[0])
                        for r in c.execute("SELECT id FROM projects ORDER BY id")
                    ],
                    query,
                    path,
                    limit,
                )
            if len(parts) == 2 and parts[0] == "projects":
                fields(query, set())
                return self.project(c, parts[1])
            if len(parts) == 3 and parts[:2] == ["projects", "by-key"]:
                fields(query, set())
                return self.project(c, parts[2])
            if parts in (["tasks"], ["brief"]):
                is_brief = parts == ["brief"]
                allowed = {
                    "project_id",
                    "status",
                    "assignee",
                    "parent_id",
                    "priority",
                    "blocked",
                    "archived",
                    "q",
                    "kind",
                }
                limit = self.page_options(
                    query, allowed if not is_brief else {"project_id"}
                )
                if is_brief and not actor:
                    raise Error(
                        400, "missing_actor", "Supply X-Actor or --as for a brief."
                    )
                for key in ("status", "priority", "kind"):
                    choices = {
                        "status": STATUSES,
                        "priority": PRIORITIES,
                        "kind": ("task", "epic"),
                    }[key]
                    if key in query and query[key] not in choices:
                        invalid(key, f"{key} must be one of: {', '.join(choices)}.")
                for key in ("archived", "blocked"):
                    if key in query and query[key] not in {"true", "false"}:
                        invalid(key, f"{key} must be true or false.")
                if "parent_id" in query:
                    try:
                        integer(int(query["parent_id"]), "parent_id")
                    except (ValueError, TypeError):
                        invalid("parent_id", "parent_id must be a positive task ID.")
                where, params = (
                    [
                        "archived_at IS "
                        + ("NOT NULL" if query.get("archived") == "true" else "NULL")
                    ],
                    [],
                )
                if "project_id" in query:
                    project = self.project(c, query["project_id"])
                    where.append("project_id=?")
                    params.append(project["id"])
                total_where, total_params = list(where), list(params)
                if "status" in query:
                    total_where.append("status=?")
                    total_params.append(query["status"])
                project_total = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE " + " AND ".join(total_where),
                    total_params,
                ).fetchone()[0]
                for key in ("status", "assignee", "kind", "parent_id"):
                    if key in query:
                        where.append(key + "=?")
                        params.append(query[key])
                rows = c.execute(
                    "SELECT id FROM tasks WHERE "
                    + " AND ".join(where)
                    + " ORDER BY CASE status WHEN 'backlog' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'review' THEN 2 ELSE 3 END,position,id",
                    params,
                ).fetchall()
                items = []
                search = query.get("q", "").casefold().strip()
                for row in rows:
                    task = self.public_task(c, row[0])
                    if is_brief and (
                        task["status"] == "done"
                        or not (
                            task["assignee"] == actor
                            or (
                                task["execution"]
                                and task["execution"]["actor"] == actor
                            )
                        )
                    ):
                        continue
                    if "priority" in query and task["priority"] != query["priority"]:
                        continue
                    if "blocked" in query and task["blocked"] != (
                        query["blocked"] == "true"
                    ):
                        continue
                    aliases = self.project(c, task["project_id"])["aliases"]
                    haystack = " ".join(
                        [
                            task["title"],
                            task["description_markdown"],
                            *[f"{key}-{task['id']}" for key in aliases],
                        ]
                    ).casefold()
                    if search and search not in haystack:
                        continue
                    if is_brief:
                        task = {
                            k: task[k]
                            for k in (
                                "id",
                                "reference",
                                "title",
                                "kind",
                                "status",
                                "assignee",
                                "execution",
                                "version",
                                "blockers",
                                "checkpoint",
                                "result",
                                "thread_links",
                                "url",
                                "updated_at",
                            )
                        }
                    items.append(task)
                return self.page(
                    items,
                    query,
                    path + (":" + actor if is_brief else ""),
                    limit,
                    {"project_total": project_total, "instance_id": self.instance_id},
                )
            if len(parts) == 2 and parts[0] == "tasks":
                fields(query, set())
                return self.public_task(c, parts[1])
            if (
                len(parts) == 3
                and parts[0] == "tasks"
                and parts[2] in {"comments", "attachments", "history"}
            ):
                task = self.task(c, parts[1])
                limit = self.page_options(query, set())
                if parts[2] == "history":
                    rows = c.execute(
                        "SELECT * FROM events WHERE entity_type='task' AND entity_id=? ORDER BY sequence",
                        (task["id"],),
                    )
                else:
                    rows = c.execute(
                        f"SELECT * FROM {parts[2]} WHERE task_id=? ORDER BY id",
                        (task["id"],),
                    )
                items = [dict(r) for r in rows]
                for item in items:
                    if "detail" in item:
                        item["detail"] = json.loads(item["detail"])
                    if parts[2] == "attachments":
                        item["download_url"] = (
                            f"/api/v1/attachments/{item['id']}/content"
                        )
                return self.page(items, query, path, limit)
            if parts == ["events"]:
                limit = self.page_options(
                    query, {"source", "after", "project_id", "task_id"}
                )
                if "cursor" in query:
                    invalid(
                        "cursor",
                        "Events use source + after, separate from task-list cursors.",
                    )
                if query.get("source") != self.instance_id:
                    raise Error(
                        409,
                        "wrong_source",
                        "Supply the instance_id from /api/v1/health as source.",
                    )
                try:
                    after = int(query.get("after", 0))
                    if after < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    invalid("after", "after must be a nonnegative event sequence.")
                rows = c.execute(
                    "SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?",
                    (after, limit + 1),
                ).fetchall()
                examined = rows[:limit]
                items = []
                for row in examined:
                    if "project_id" in query and str(row["project_id"]) != str(
                        query["project_id"]
                    ):
                        continue
                    if "task_id" in query and (
                        row["entity_type"] != "task"
                        or str(row["entity_id"]) != str(query["task_id"])
                    ):
                        continue
                    items.append(dict(row, detail=json.loads(row["detail"])))
                cursor = examined[-1]["sequence"] if examined else after
                return {
                    "source": self.instance_id,
                    "items": items,
                    "after": cursor,
                    "next_cursor": cursor,
                    "has_more": len(rows) > limit,
                }
            raise Error(404, "not_found", "No matching read route.")

    def attachment(self, identifier):
        with self.store.connection() as c:
            row = c.execute(
                "SELECT * FROM attachments WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise Error(404, "not_found", "Attachment does not exist.")
            value = dict(row)
        path = self.store.blobs / value["sha256"]
        if not path.is_file():
            raise Error(
                503,
                "missing_blob",
                "Attachment bytes are unavailable; restore from backup.",
            )
        return value, path
