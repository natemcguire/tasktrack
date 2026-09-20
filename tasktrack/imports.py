"""Resumable native ingestion. Source history never becomes a human acceptance."""

import hashlib
import json
import re
import uuid
from datetime import datetime

from .db import Error, encode, now


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def authorize(service, c, pid=None):
    if service.access:
        service.access.require(c, "integrations.manage")
        if pid is not None:
            service.access.require(c, "work.edit", pid)


def source_text(value, name, maximum=100000):
    from .service import string

    return string(value, name, True, maximum)


def source_time(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise Error(422, "source_date", "Source timestamps must be ISO 8601 strings.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise Error(
            422, "source_date", "Source timestamps must include a timezone."
        ) from None
    return value


def validate(dataset):
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise Error(422, "import_schema", "Use a version 1 import bundle.")
    if dataset.get("provider") not in ("jira", "trello", "basecamp", "kanban"):
        raise Error(422, "import_provider", "Choose Jira, Trello, Basecamp or Kanban.")
    dataset = json.loads(encode(dataset))
    for key in ("account_id", "source_project"):
        dataset[key] = source_text(dataset.get(key), key, 500)
    records = dataset.get("records")
    if not isinstance(records, list) or len(records) > 5000:
        raise Error(
            422, "import_records", "Supply up to 5,000 records per source project."
        )
    warnings = dataset.get("warnings", [])
    if (
        not isinstance(warnings, list)
        or any(not isinstance(w, str) or len(w) > 2000 for w in warnings)
        or len(warnings) > 1000
    ):
        raise Error(422, "import_warnings", "Invalid import warnings.")
    dataset["warnings"] = warnings
    if type(dataset.get("complete")) is not bool:
        raise Error(
            422, "import_completeness", "Declare whether the source export is complete."
        )
    seen = {}
    for r in records:
        if not isinstance(r, dict):
            raise Error(422, "import_record", "Each record must be an object.")
        if len(encode(r).encode()) > 500000:
            raise Error(
                413,
                "import_record_size",
                "An individual record exceeds 500 KB. Preserve large history in the source archive.",
            )
        rid = source_text(r.get("id"), "source ID", 500)
        if rid in seen:
            raise Error(422, "duplicate_source_id", "Source IDs must be unique.")
        seen[rid] = r
        r["title"] = source_text(r.get("title"), "title", 120)
        if (
            not isinstance(r.get("description", ""), str)
            or len(r.get("description", "")) > 100000
        ):
            raise Error(
                422,
                "import_description",
                "Description must be at most 100,000 characters.",
            )
        if r.get("phase") not in ("backlog", "in_progress", "review", "done"):
            raise Error(
                422, "status_mapping", "Map every source status to a Tasktrack phase."
            )
        r["status"] = source_text(r.get("status", r["phase"]), "status", 60)
        r["kind"] = "epic" if r.get("type", "").lower() == "epic" else "task"
        people = r.get("source_assignees", [])
        if (
            not isinstance(people, list)
            or len(people) > 100
            or any(
                not isinstance(p, dict)
                or not isinstance(p.get("id"), str)
                or not p["id"]
                or len(p["id"]) > 500
                or not isinstance(p.get("name"), str)
                or len(p["name"]) > 500
                for p in people
            )
        ):
            raise Error(
                422,
                "source_people",
                "Source assignees need bounded string IDs and names.",
            )
        for key in ("created_at", "updated_at", "archived_at"):
            r[key] = source_time(r.get(key))
        if not isinstance(r.get("comments", []), list) or not isinstance(
            r.get("attachments", []), list
        ):
            raise Error(
                422, "import_children", "Comments and attachments must be lists."
            )
        for field in ("comments", "attachments"):
            ids = set()
            for child in r.get(field, []):
                if not isinstance(child, dict):
                    raise Error(
                        422,
                        "import_child",
                        "Each comment and attachment must be an object.",
                    )
                cid = source_text(child.get("id"), field + " ID", 500)
                if cid in ids:
                    raise Error(
                        422, "duplicate_source_id", "Duplicate " + field + " ID."
                    )
                ids.add(cid)
                child["created_at"] = source_time(child.get("created_at"))
                if field == "comments":
                    child["body"] = source_text(child.get("body"), "comment body")
                    child["author"] = source_text(
                        child.get("author", "Unknown source author"), "author", 200
                    )
                else:
                    child["filename"] = source_text(
                        child.get("filename"), "filename", 500
                    )
                    if not isinstance(child.get("sha256"), str) or not re.fullmatch(
                        "[a-f0-9]{64}", child["sha256"]
                    ):
                        raise Error(
                            422, "file_hash", "Every attachment needs a SHA-256 hash."
                        )
                    if (
                        type(child.get("size")) is not int
                        or not 0 <= child["size"] <= 10 * 1024 * 1024
                    ):
                        raise Error(
                            422, "file_size", "Attachments must be at most 10 MiB."
                        )
    # Parents are topologically ordered; unsupported parent types remain provenance.
    ordered = []
    done = set()
    visiting = set()

    def visit(r):
        if r["id"] in done:
            return
        if r["id"] in visiting:
            raise Error(422, "parent_cycle", "Source hierarchy contains a cycle.")
        if len(visiting) > 100:
            raise Error(422, "parent_depth", "Source hierarchy exceeds 100 levels.")
        visiting.add(r["id"])
        parent = r.get("parent_id")
        if parent:
            if parent not in seen:
                raise Error(
                    422,
                    "missing_parent",
                    "Source parent " + str(parent) + " is missing.",
                )
            visit(seen[parent])
            if seen[parent]["kind"] != "epic" or r["kind"] == "epic":
                warning = (
                    "Parent relationship for "
                    + r["id"]
                    + " is retained in source metadata; native parents must be epics with task children."
                )
                if warning not in warnings:
                    warnings.append(warning)
        visiting.remove(r["id"])
        done.add(r["id"])
        ordered.append(r)

    for r in records:
        visit(r)
    dataset["records"] = ordered
    return dataset


def job(service, c, jid):
    row = c.execute("SELECT * FROM import_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        raise Error(404, "import_missing", "Import job unavailable.")
    authorize(service, c, row["project_id"])
    return dict(row)


def report(row):
    dataset = json.loads(row["dataset"])
    return {
        k: row[k]
        for k in (
            "id",
            "project_id",
            "provider",
            "account_id",
            "source_project",
            "digest",
            "state",
            "cursor",
            "created_at",
            "updated_at",
        )
    } | {
        "total": dataset.get("total", len(dataset.get("records", []))),
        "report": json.loads(row["report"]),
        "warnings": dataset["warnings"],
        "source_complete": dataset["complete"],
        "people": dataset.get("people", []),
        "people_mapping": dataset.get("people_mapping", {}),
        "attachments": dataset.get(
            "attachments",
            [a for r in dataset.get("records", []) for a in r.get("attachments", [])],
        ),
        "columns": dataset.get(
            "columns",
            list(dict.fromkeys(r["status"] for r in dataset.get("records", []))),
        ),
        "source_archive_sha256": dataset.get("source_archive_sha256"),
        "source_archive_url": "/api/v1/import-jobs/" + row["id"] + "/source"
        if dataset.get("source_archive_sha256")
        else None,
    }


def read(service, c, parts, query):
    from .service import fields

    fields(query, set())
    authorize(service, c)
    if len(parts) == 1:
        return {
            "items": [
                report(dict(r))
                for r in c.execute(
                    "SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT 100"
                )
                if not service.access
                or service.access.allowed(c, "work.edit", r["project_id"])
            ]
        }
    if len(parts) == 2:
        return report(job(service, c, parts[1]))
    if len(parts) == 3 and parts[2] == "rollback":
        return rollback_preview(service, c, job(service, c, parts[1]))
    raise Error(404, "not_found", "Import route unavailable.")


def write(service, c, method, parts, body, context):
    from .service import fields, integer

    authorize(service, c)
    if parts == ["import-jobs"] and method == "POST":
        fields(body, {"project_id", "dataset", "allow_visibility_change"})
        pid = integer(body.get("project_id"), "project_id")
        authorize(service, c, pid)
        service.project(c, pid)
        dataset = validate(body.get("dataset"))
        if (
            dataset.get("source_archive_sha256")
            and service.access
            and service.access.identity.get("verified_source_archive")
            != dataset["source_archive_sha256"]
        ):
            raise Error(
                403,
                "unverified_source_archive",
                "Upload the source through the import preview endpoint.",
            )
        statuses = {}
        for record in dataset["records"]:
            old = statuses.setdefault(record["status"].lower(), record["phase"])
            if old != record["phase"]:
                raise Error(
                    422, "status_mapping", "A source column must map to one phase."
                )
        existing = c.execute(
            "SELECT name,allowed_phases_json FROM board_columns WHERE project_id=? AND archived_at IS NULL",
            (pid,),
        ).fetchall()
        if len(set(statuses) | {r["name"].lower() for r in existing}) > 50:
            raise Error(
                422,
                "column_limit",
                "Map source statuses to at most 50 destination columns.",
            )
        for r in existing:
            if r["name"].lower() in statuses and statuses[
                r["name"].lower()
            ] not in json.loads(r["allowed_phases_json"]):
                raise Error(
                    409,
                    "column_mapping_conflict",
                    "Existing column "
                    + r["name"]
                    + " does not allow that imported phase.",
                )
        access = c.execute(
            "SELECT internal_access FROM project_access WHERE project_id=?", (pid,)
        ).fetchone()
        if (
            access
            and access[0] == "all"
            and body.get("allow_visibility_change") is not True
        ):
            raise Error(
                409,
                "import_visibility",
                "Destination is visible to internal workspace members. Explicitly review and acknowledge that access, or choose a restricted project.",
            )
        jid = str(uuid.uuid4())
        digest = fingerprint({"project_id": pid, "dataset": dataset})
        owner = (
            service.access.identity["user_id"] if service.access else context["actor"]
        )
        records = dataset.pop("records")
        for record in records:
            record.pop("mapped_assignee", None)
        dataset["people"] = list(
            {
                str(p["id"]): p for r in records for p in r.get("source_assignees", [])
            }.values()
        )
        dataset.update(
            total=len(records),
            columns=list(dict.fromkeys(r["status"] for r in records)),
            attachments=[a for r in records for a in r.get("attachments", [])],
        )
        c.execute(
            "INSERT INTO import_jobs(id,project_id,owner_id,provider,account_id,source_project,digest,dataset,created_at,updated_at,report) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                jid,
                pid,
                owner,
                dataset["provider"],
                dataset["account_id"],
                dataset["source_project"],
                digest,
                encode(dataset),
                now(),
                now(),
                encode(
                    {
                        "created": 0,
                        "unchanged": 0,
                        "comments": 0,
                        "attachments": 0,
                        "conflicts": [],
                    }
                ),
            ),
        )
        c.executemany(
            "INSERT INTO import_items VALUES(?,?,?)",
            [(jid, i, encode(r)) for i, r in enumerate(records)],
        )
        return 201, report(job(service, c, jid))
    if len(parts) != 3 or method != "POST":
        raise Error(404, "not_found", "Import operation unavailable.")
    row = job(service, c, parts[1])
    action = parts[2]
    if action == "people":
        fields(body, {"digest", "mapping"})
        if (
            row["state"] != "preview"
            or row["cursor"] != 0
            or body.get("digest") != row["digest"]
        ):
            raise Error(
                409,
                "import_changed",
                "People can only be mapped on the current uncommitted preview.",
            )
        mapping = body.get("mapping")
        if not isinstance(mapping, dict) or (
            service.access
            and mapping != service.access.identity.get("verified_people_mapping")
        ):
            raise Error(
                403,
                "people_mapping",
                "Choose verified workspace members through the import wizard.",
            )
        dataset = json.loads(row["dataset"])
        source_ids = {p["id"] for p in dataset.get("people", [])}
        if not set(mapping) <= source_ids or any(
            v is not None and (not isinstance(v, str) or len(v) > 254)
            for v in mapping.values()
        ):
            raise Error(
                422,
                "people_mapping",
                "Choose valid source people and destination members.",
            )
        records = []
        for item in c.execute(
            "SELECT position,data FROM import_items WHERE job_id=? ORDER BY position",
            (row["id"],),
        ).fetchall():
            record = json.loads(item["data"])
            mapped = list(
                dict.fromkeys(
                    mapping[p["id"]]
                    for p in record.get("source_assignees", [])
                    if mapping.get(p["id"])
                )
            )
            if len(mapped) > 1:
                raise Error(
                    422,
                    "multiple_assignees",
                    "Tasktrack has one assignee per task. Map multiple source assignees to one person or leave extra people unmapped.",
                )
            record["mapped_assignee"] = mapped[0] if mapped else None
            c.execute(
                "UPDATE import_items SET data=? WHERE job_id=? AND position=?",
                (encode(record), row["id"], item["position"]),
            )
            records.append(record)
        dataset["people_mapping"] = mapping
        revised = fingerprint(
            {"project_id": row["project_id"], "dataset": dataset, "records": records}
        )
        c.execute(
            "UPDATE import_jobs SET dataset=?,digest=?,updated_at=? WHERE id=?",
            (encode(dataset), revised, now(), row["id"]),
        )
        return 200, report(job(service, c, row["id"]))
    if action == "rollback":
        fields(body, {"digest", "manifest_digest"})
        if body.get("digest") != row["digest"]:
            raise Error(409, "preview_changed", "Use the reviewed import digest.")
        preview = rollback_preview(service, c, row)
        if preview["conflicts"]:
            raise Error(
                409,
                "rollback_conflicts",
                "Imported work has changed. Review conflicts; no work was archived.",
                conflicts=preview["conflicts"],
            )
        if preview["digest"] != body.get("manifest_digest"):
            raise Error(
                409, "rollback_changed", "Rollback targets changed. Review them again."
            )
        if service.access and (
            service.access.identity.get("bearer")
            or service.access.identity.get("human_approval_digest") != preview["digest"]
        ):
            raise Error(
                403,
                "human_verification_required",
                "Confirm rollback with your passkey.",
            )
        for tid in preview["task_ids"]:
            c.execute(
                "UPDATE tasks SET archived_at=?,version=version+1 WHERE id=?",
                (now(), tid),
            )
            service.event(
                c,
                "task",
                tid,
                row["project_id"],
                "import.rollback",
                context,
                {"job_id": row["id"], "manifest_digest": preview["digest"]},
            )
        c.execute(
            "UPDATE import_jobs SET state='rolled_back',updated_at=? WHERE id=?",
            (now(), row["id"]),
        )
        return 200, report(job(service, c, row["id"]))
    if action == "blob":
        fields(body, {"sha256", "size"})
        if row["state"] not in ("preview", "running", "cancelled"):
            raise Error(409, "import_state", "Files cannot be staged for this job.")
        if (
            service.access
            and service.access.identity.get("verified_import_blob") != body
        ):
            raise Error(
                403,
                "unverified_blob",
                "Upload file bytes through the import upload route.",
            )
        files = report(row)["attachments"]
        if not any(
            a["sha256"] == body.get("sha256") and a["size"] == body.get("size")
            for a in files
        ):
            raise Error(
                422, "file_manifest", "File does not match this import manifest."
            )
        c.execute(
            "INSERT OR IGNORE INTO import_blobs VALUES(?,?,?)",
            (row["id"], body["sha256"], body["size"]),
        )
        return 200, {"verified": True}
    fields(body, {"digest"})
    if body.get("digest") != row["digest"]:
        raise Error(409, "preview_changed", "Use the digest of the reviewed preview.")
    if action == "cancel":
        if row["state"] in ("preview", "running"):
            c.execute(
                "UPDATE import_jobs SET state='cancelled',updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        return 200, report(job(service, c, row["id"]))
    if action not in ("commit", "resume"):
        raise Error(404, "not_found", "Unknown import action.")
    if row["state"] == "rolled_back":
        raise Error(
            409,
            "import_rolled_back",
            "This import was rolled back. Restore individual records to keep the source mapping intact.",
        )
    if row["state"] in ("complete", "partial"):
        return 200, report(row)
    if row["state"] == "cancelled" and action != "resume":
        raise Error(409, "import_cancelled", "Explicitly resume this cancelled job.")
    dataset = json.loads(row["dataset"])
    summary = json.loads(row["report"])
    total = report(row)["total"]
    # Check every file before first commit, then each resumed batch under current authority.
    for a in report(row)["attachments"]:
        if not c.execute(
            "SELECT 1 FROM import_blobs WHERE job_id=? AND sha256=? AND size=?",
            (row["id"], a["sha256"], a["size"]),
        ).fetchone():
            raise Error(
                409,
                "files_missing",
                "Upload and verify all manifest files before committing.",
            )
    end = min(row["cursor"] + 25, total)
    for item in c.execute(
        "SELECT data FROM import_items WHERE job_id=? AND position>=? AND position<? ORDER BY position",
        (row["id"], row["cursor"], end),
    ).fetchall():
        record = json.loads(item["data"])
        ingest(service, c, row, record, context, summary)
    state = (
        "running"
        if end < total
        else (
            "complete"
            if dataset["complete"]
            and not dataset["warnings"]
            and not summary["conflicts"]
            else "partial"
        )
    )
    c.execute(
        "UPDATE import_jobs SET cursor=?,state=?,updated_at=?,report=? WHERE id=?",
        (end, state, now(), encode(summary), row["id"]),
    )
    return 200, report(job(service, c, row["id"]))


def rollback_preview(service, c, row):
    ids = [
        r[0]
        for r in c.execute(
            "SELECT task_id FROM import_records WHERE job_id=? ORDER BY task_id",
            (row["id"],),
        )
    ]
    conflicts = []
    for tid in ids:
        task = c.execute("SELECT version FROM tasks WHERE id=?", (tid,)).fetchone()
        events = c.execute(
            "SELECT count(*) FROM events WHERE entity_type='task' AND entity_id=?",
            (tid,),
        ).fetchone()[0]
        outside_children = [
            r[0]
            for r in c.execute("SELECT id FROM tasks WHERE parent_id=?", (tid,))
            if r[0] not in ids
        ]
        dependencies = c.execute(
            "SELECT 1 FROM dependencies WHERE task_id=? OR prerequisite_id=? LIMIT 1",
            (tid, tid),
        ).fetchone()
        if (
            not task
            or task["version"] != 1
            or events != 1
            or outside_children
            or dependencies
        ):
            conflicts.append(tid)
    manifest = {
        "job_id": row["id"],
        "cursor": row["cursor"],
        "state": row["state"],
        "task_ids": ids,
        "conflicts": conflicts,
        "action": "archive unchanged imported work",
    }
    return manifest | {"digest": fingerprint(manifest)}


def ingest(service, c, job, record, context, summary):
    key = (
        job["provider"],
        job["account_id"],
        job["source_project"],
        record["id"],
        job["project_id"],
    )
    previous = c.execute(
        "SELECT * FROM import_records WHERE provider=? AND account_id=? AND source_project=? AND source_id=? AND project_id=?",
        key,
    ).fetchone()
    if previous:
        if previous["source_hash"] == fingerprint(record):
            summary["unchanged"] += 1
        else:
            summary["conflicts"].append(
                {
                    "source_id": record["id"],
                    "task_id": previous["task_id"],
                    "reason": "Source changed. Existing destination is preserved; review and merge manually.",
                }
            )
        return
    pid = job["project_id"]
    parent_id = None
    if record.get("parent_id") and record["kind"] == "task":
        parent = c.execute(
            "SELECT t.id,t.kind FROM import_records i JOIN tasks t ON t.id=i.task_id WHERE i.provider=? AND i.account_id=? AND i.source_project=? AND i.source_id=? AND i.project_id=?",
            key[:3] + (record["parent_id"], pid),
        ).fetchone()
        if parent and parent["kind"] == "epic":
            parent_id = parent["id"]
    column = c.execute(
        "SELECT id,allowed_phases_json FROM board_columns WHERE project_id=? AND lower(name)=lower(?) AND archived_at IS NULL",
        (pid, record["status"]),
    ).fetchone()
    if column and record["phase"] not in json.loads(column["allowed_phases_json"]):
        raise Error(
            409,
            "column_mapping_conflict",
            "Existing column "
            + record["status"]
            + " does not allow the imported phase.",
        )
    if not column:
        column_id = c.execute(
            "INSERT INTO board_columns(project_id,name,allowed_phases_json,sort_key) SELECT ?,?,?,COALESCE(MAX(sort_key),0)+1 FROM board_columns WHERE project_id=?",
            (pid, record["status"], encode([record["phase"]]), pid),
        ).lastrowid
        c.execute(
            "UPDATE project_board_configurations SET version=version+1 WHERE project_id=?",
            (pid,),
        )
    else:
        column_id = column["id"]
    source = {k: v for k, v in record.items() if k != "description"}
    source.update(
        provider=job["provider"],
        account_id=job["account_id"],
        source_project=job["source_project"],
        job_id=job["id"],
    )
    data = {
        "title": record["title"],
        "description_markdown": record.get("description", ""),
        "acceptance_criteria": [],
        "priority": record.get("priority")
        if record.get("priority") in ("low", "normal", "high", "urgent")
        else "normal",
        "execution": None,
        "blocker_reason": None,
        "checkpoint": None,
        "result": None,
        "completion": None,
        "created_by": context["actor"],
        "created_via": context["via"],
        "created_at": record.get("created_at") or now(),
        "updated_by": context["actor"],
        "updated_via": context["via"],
        "updated_at": record.get("updated_at") or now(),
        "import_source": source,
    }
    position = c.execute(
        "SELECT COALESCE(MAX(position),0)+1024 FROM tasks WHERE project_id=? AND column_id=?",
        (pid, column_id),
    ).fetchone()[0]
    tid = c.execute(
        "INSERT INTO tasks(project_id,kind,parent_id,status,assignee,position,archived_at,version,data,column_id) VALUES(?,?,?,?,?,?,?,1,?,?)",
        (
            pid,
            record["kind"],
            parent_id,
            record["phase"],
            record.get("mapped_assignee"),
            position,
            record.get("archived_at"),
            encode(data),
            column_id,
        ),
    ).lastrowid
    for comment in record.get("comments", []):
        c.execute(
            "INSERT INTO comments(task_id,actor,session,via,body,created_at) VALUES(?,?,NULL,?,?,?)",
            (
                tid,
                "source:" + job["provider"] + ":" + comment["author"],
                "api",
                comment["body"],
                comment.get("created_at") or now(),
            ),
        )
        summary["comments"] += 1
    for a in record.get("attachments", []):
        c.execute(
            "INSERT INTO attachments(task_id,sha256,stored_name,filename,size,media_type,actor,via,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                tid,
                a["sha256"],
                a["sha256"],
                a["filename"],
                a["size"],
                a.get("media_type", "application/octet-stream"),
                context["actor"],
                "api",
                a.get("created_at") or now(),
            ),
        )
        summary["attachments"] += 1
    c.execute(
        "INSERT INTO import_records VALUES(?,?,?,?,?,?,?,?)",
        key + (tid, fingerprint(record), job["id"]),
    )
    summary["created"] += 1
    service.event(
        c,
        "task",
        tid,
        pid,
        "import",
        context,
        {
            "source_id": record["id"],
            "job_id": job["id"],
            "phase": record["phase"],
            "source_hash": fingerprint(record),
            "historical_state": True,
        },
    )
