"""Offline-capable CLI: no running HTTP or inbox service required."""

import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path

from .db import Error, Store
from .service import ACTION_FIELDS, Service


def common(parser):
    for flag, kwargs in [
        ("--data-dir", {}),
        ("--json", {"action": "store_true"}),
        ("--as", {"dest": "actor"}),
        ("--session", {}),
        ("--request-id", {}),
    ]:
        parser.add_argument(flag, default=argparse.SUPPRESS, **kwargs)


def parser():
    root = argparse.ArgumentParser(
        prog="tt", description="Local work board for people and coding agents."
    )
    common(root)
    commands = root.add_subparsers(dest="command", required=True)

    def sub(parent, name, help_text=""):
        p = parent.add_parser(name, help=help_text)
        common(p)
        return p

    auth = sub(commands, "auth", "Enroll an agent with human approval").add_subparsers(
        dest="operation", required=True
    )
    for name in ("login", "logout", "status"):
        p = sub(auth, name)
        p.add_argument("--url")
        if name == "login":
            p.add_argument("--workspace", required=True)
            p.add_argument("--name", required=True)
            p.add_argument("--project", type=int, action="append", required=True)
            p.add_argument("--capability", action="append")
            p.add_argument("--owner-email")
    bridge = sub(
        commands,
        "message-bridge",
        "Deliver your queued iMessages from this signed-in Mac",
    )
    bridge.add_argument("--url")
    bridge.add_argument("--once", action="store_true")
    approvals = sub(
        commands, "approval", "Request and execute human-approved actions"
    ).add_subparsers(dest="operation", required=True)
    sub(approvals, "list")
    p = sub(approvals, "request")
    p.add_argument(
        "action",
        choices=[
            "task.archive",
            "task.reopen",
            "task.restore",
            "task.reassign",
            "task.backlog",
        ],
    )
    p.add_argument("task_id", type=int)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--assignee")
    p.add_argument("--reopen-parent", action="store_true")
    for name in ("get", "execute"):
        sub(approvals, name).add_argument("id")
    imports = sub(
        commands, "import", "Preview and import provider exports"
    ).add_subparsers(dest="operation", required=True)
    for name in ("normalize", "preview"):
        p = sub(imports, name)
        p.add_argument(
            "--provider",
            choices=["jira", "trello", "basecamp", "kanban"],
            required=True,
        )
        p.add_argument("--file", required=True)
        p.add_argument("--mapping-file")
        p.add_argument("--account-id", default="file")
        if name == "normalize":
            p.add_argument("--output", required=True)
        else:
            p.add_argument("--project", required=True)
            p.add_argument("--approve-destination-access", action="store_true")
    sub(imports, "list")
    for name in ("get", "commit", "resume", "cancel"):
        p = sub(imports, name)
        p.add_argument("id")
        if name != "get":
            p.add_argument("--digest", required=True)
    p = sub(imports, "upload")
    p.add_argument("id")
    p.add_argument("files", nargs="+")

    server = sub(commands, "serve", "Serve the browser UI and API")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=7777)
    sub(commands, "health")
    backup = sub(commands, "backup", "Create a complete live snapshot")
    backup.add_argument("destination")
    restore = sub(commands, "restore", "Restore a backup into a new directory")
    restore.add_argument("source")
    restore.add_argument("destination")
    for name in ("brief", "events"):
        p = sub(commands, name)
        p.add_argument("--project")
        p.add_argument("--limit", type=int)
        if name == "events":
            p.add_argument("--source", required=True)
            p.add_argument("--after", type=int, default=0)
            p.add_argument("--task-id", type=int)
        else:
            p.add_argument("--cursor")
    project = sub(commands, "project").add_subparsers(dest="operation", required=True)
    sub(project, "list").add_argument("--cursor")
    sub(project, "get").add_argument("id")
    p = sub(project, "create")
    p.add_argument("key")
    p.add_argument("name")
    p.add_argument("--brief-file")
    p.add_argument("--file", help="JSON object for additional project fields")
    p = sub(project, "update")
    p.add_argument("id")
    p.add_argument("--version", type=int, required=True)
    for flag in ("key", "name", "brief-file", "file"):
        p.add_argument("--" + flag)
    p = sub(project, "columns", "View or update board columns")
    p.add_argument("id")
    p.add_argument("--version", "--expected-version", dest="version", type=int)
    p.add_argument("--file", help="JSON file containing columns configuration")
    task = sub(commands, "task").add_subparsers(dest="operation", required=True)
    p = sub(task, "create")
    p.add_argument("project")
    p.add_argument("--file", required=True)
    p = sub(task, "list")
    for flag in (
        "project",
        "status",
        "assignee",
        "parent-id",
        "priority",
        "blocked",
        "archived",
        "q",
        "kind",
        "cursor",
        "column-id",
        "phase",
    ):
        p.add_argument("--" + flag)
    p.add_argument("--limit", type=int)
    for operation in ("get", "history", "comments", "attachments"):
        p = sub(task, operation)
        p.add_argument("id")
        if operation != "get":
            p.add_argument("--cursor")
            p.add_argument("--limit", type=int)
    p = sub(task, "update")
    p.add_argument("id")
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--file")
    for flag in ("title", "description", "assignee", "priority", "parent-id"):
        p.add_argument("--" + flag)
    p = sub(task, "comment")
    p.add_argument("id")
    p.add_argument("--reply-to", type=int)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--body")
    group.add_argument("--file")
    p = sub(task, "attach")
    p.add_argument("id")
    p.add_argument("file")
    p.add_argument("--comment-id", type=int)
    p = sub(task, "download")
    p.add_argument("attachment_id", type=int)
    p.add_argument("destination")
    for operation in ACTION_FIELDS:
        p = sub(task, operation)
        p.add_argument("id")
        p.add_argument("--version", type=int, required=True)
        p.add_argument(
            "--file",
            help="JSON action body; checkpoint/handoff also accept a bare checkpoint",
        )
        for field in sorted(
            ACTION_FIELDS[operation] - {"checkpoint", "result", "reopen_parent"}
        ):
            p.add_argument(
                "--" + field.replace("_", "-"),
                type=int if field in {"before_id", "after_id", "column_id"} else str,
            )
        if "reopen_parent" in ACTION_FIELDS[operation]:
            p.add_argument("--reopen-parent", action="store_true", default=None)
    return root


def execute(args):
    if args.command == "auth":
        from .credentials import command

        return command(args)
    if args.command == "message-bridge":
        from .message_bridge import command

        return command(args)
    remote_url = os.environ.get("TT_URL")
    if remote_url and args.command in {"serve", "backup", "restore"}:
        raise Error(
            422,
            "local_command",
            "Unset TT_URL to use local serve, backup or restore commands.",
        )
    if args.command == "restore":
        return Store.restore(args.source, args.destination)
    if remote_url:
        from .credentials import access_token
        from .remote import RemoteService

        service = RemoteService(
            remote_url, os.environ.get("TT_TOKEN") or access_token(remote_url)
        )
    else:
        store = Store(getattr(args, "data_dir", None))
        if args.command == "backup":
            return store.backup(args.destination)
        service = Service(store)
    if args.command == "serve":
        from .http import serve

        serve(service, args.host, args.port)
        return None
    actor = getattr(args, "actor", None) or os.environ.get("TT_ACTOR")
    session = getattr(args, "session", None) or os.environ.get("TT_SESSION")

    def read(path, query=None):
        return service.read(
            "/api/v1/" + path,
            {k: str(v) for k, v in (query or {}).items() if v is not None},
            actor,
        )

    def write(path, data, method="POST", upload=None):
        return service.mutate(
            method,
            "/api/v1/" + path,
            data,
            actor,
            getattr(args, "request_id", None) or str(uuid.uuid4()),
            session,
            "cli",
            upload,
        )[1]

    def file_data():
        value = (
            json.loads(Path(args.file).read_text())
            if getattr(args, "file", None)
            else {}
        )
        if not isinstance(value, dict):
            raise Error(400, "invalid_file", "The JSON file must contain an object.")
        return value

    if args.command == "health":
        return read("health")
    if args.command == "approval":
        if args.operation == "list":
            return read("approval-requests")
        if args.operation == "get":
            return read("approval-requests/" + args.id)
        if args.operation == "execute":
            return write("approval-requests/" + args.id + "/execute", {})
        return write(
            "approval-requests",
            {
                "operation": args.action,
                **(
                    {"assignee": None if args.assignee == "null" else args.assignee}
                    if args.action == "task.reassign"
                    else {}
                ),
                **({"reopen_parent": True} if args.reopen_parent else {}),
                "task_id": args.task_id,
                "expected_version": args.version,
                "reason": args.reason,
            },
        )
    if args.command == "import":
        from .providers import normalize

        if args.operation == "list":
            return read("import-jobs")
        if args.operation == "get":
            return read("import-jobs/" + args.id)
        if args.operation in ("commit", "resume", "cancel"):
            return write(
                "import-jobs/" + args.id + "/" + args.operation, {"digest": args.digest}
            )
        if args.operation == "upload":
            import hashlib

            result = []
            for filename in args.files:
                content = Path(filename).read_bytes()
                sha = hashlib.sha256(content).hexdigest()
                if len(content) > 10 * 1024 * 1024:
                    raise Error(413, "file_size", "Files must be 10 MiB or smaller.")
                if remote_url:
                    result.append(
                        service.request(
                            "/api/v1/import-jobs/" + args.id + "/files/" + sha,
                            "POST",
                            content,
                            {
                                "Idempotency-Key": str(uuid.uuid4()),
                                "Content-Type": "application/octet-stream",
                            },
                        )[1]
                    )
                else:
                    service.store.put_blob(content)
                    result.append(
                        write(
                            "import-jobs/" + args.id + "/blob",
                            {"sha256": sha, "size": len(content)},
                        )
                    )
            return {"files": result}
        raw = Path(args.file).read_text()
        source = raw if Path(args.file).suffix.lower() == ".csv" else json.loads(raw)
        mapping = (
            json.loads(Path(args.mapping_file).read_text()) if args.mapping_file else {}
        )
        if args.operation == "normalize":
            from .imports import validate

            dataset = validate(
                normalize(args.provider, source, args.account_id, mapping)
            )
            Path(args.output).write_text(json.dumps(dataset, indent=2))
            return {
                "output": args.output,
                "records": len(dataset["records"]),
                "warnings": dataset["warnings"],
            }
        pid = read("projects/" + args.project)["id"]
        if remote_url:
            return write(
                "import-previews",
                {
                    "provider": args.provider,
                    "source": source,
                    "mapping": mapping,
                    "account_id": args.account_id,
                    "project_id": pid,
                    "allow_visibility_change": args.approve_destination_access,
                },
            )
        return write(
            "import-jobs",
            {
                "dataset": normalize(args.provider, source, args.account_id, mapping),
                "project_id": pid,
                "allow_visibility_change": args.approve_destination_access,
            },
        )
    if args.command in {"brief", "events"}:
        query = {
            k: getattr(args, k, None)
            for k in ("limit", "cursor", "source", "after", "task_id")
        }
        if args.project:
            query["project_id"] = read("projects/" + args.project)["id"]
        return read(args.command, query)
    operation = args.operation
    if args.command == "project":
        if operation == "list":
            return read("projects", {"cursor": args.cursor})
        if operation == "get":
            return read("projects/" + args.id)
        if operation == "columns":
            if (
                getattr(args, "file", None)
                or getattr(args, "version", None) is not None
            ):
                body = file_data()
                if getattr(args, "version", None) is not None:
                    body["expected_version"] = args.version
                return write("projects/" + args.id + "/columns", body, "PUT")
            return read("projects/" + args.id + "/columns")
        body = file_data()
        for key in ("key", "name"):
            if getattr(args, key, None) is not None:
                body[key] = getattr(args, key)
        if args.brief_file:
            body["brief_markdown"] = Path(args.brief_file).read_text()
        if operation == "create":
            return write("projects", body)
        body["expected_version"] = args.version
        return write("projects/" + args.id, body, "PATCH")
    if operation == "list":
        query = {
            k: getattr(args, k, None)
            for k in (
                "status",
                "assignee",
                "parent_id",
                "priority",
                "blocked",
                "archived",
                "q",
                "kind",
                "limit",
                "cursor",
                "column_id",
                "phase",
            )
        }
        if args.project:
            query["project_id"] = read("projects/" + args.project)["id"]
        return read("tasks", query)
    if operation in {"get", "history", "comments", "attachments"}:
        path = "tasks/" + args.id + ("/" + operation if operation != "get" else "")
        return read(path, {k: getattr(args, k, None) for k in ("cursor", "limit")})
    if operation == "download":
        metadata, source = service.attachment(args.attachment_id)
        # Exclusive creation avoids accidentally replacing a local source file.
        with Path(args.destination).open("xb") as out:
            out.write(source.read_bytes())
        return {
            "path": args.destination,
            "sha256": metadata["sha256"],
            "size": metadata["size"],
        }
    if operation == "comment":
        return write(
            "tasks/" + args.id + "/comments",
            {
                "body": Path(args.file).read_text() if args.file else args.body,
                **({"parent_id": args.reply_to} if args.reply_to is not None else {}),
            },
        )
    if operation == "attach":
        file = Path(args.file)
        if file.stat().st_size > 10 * 1024 * 1024:
            raise Error(413, "upload_too_large", "Files must be 10 MiB or smaller.")
        return write(
            "tasks/" + args.id + "/attachments",
            {
                "filename": file.name,
                "media_type": mimetypes.guess_type(file.name)[0]
                or "application/octet-stream",
                "comment_id": args.comment_id,
            },
            upload=file.read_bytes(),
        )
    body = file_data()
    if operation == "create":
        body["project_id"] = read("projects/" + args.project)["id"]
        return write("tasks", body)
    if operation == "update":
        for key in ("title", "description", "assignee", "priority", "parent_id"):
            value = getattr(args, key, None)
            if value is not None:
                body["description_markdown" if key == "description" else key] = (
                    None
                    if key in {"assignee", "parent_id"} and value == "null"
                    else int(value)
                    if key == "parent_id"
                    else value
                )
        body["expected_version"] = args.version
        return write("tasks/" + args.id, body, "PATCH")
    if operation in {"checkpoint", "handoff"} and "summary" in body:
        body = {"checkpoint": body}
    for key in ACTION_FIELDS[operation]:
        value = getattr(args, key, None)
        if value is not None:
            body[key] = None if key in {"to", "assignee"} and value == "null" else value
    body["expected_version"] = args.version
    return write("tasks/" + args.id + "/" + operation, body)


def human(value):
    if isinstance(value, dict) and "columns" in value and "phase_defaults" in value:
        lines = [f"Board Columns (version {value.get('version', 0)}):"]
        defaults_by_col = {}
        for phase, col_id in value.get("phase_defaults", {}).items():
            defaults_by_col.setdefault(col_id, []).append(phase)
        for col in value["columns"]:
            phases = ",".join(col.get("allowed_phases", []))
            defs = defaults_by_col.get(col["id"], [])
            def_str = f" [default: {','.join(defs)}]" if defs else ""
            wip = (
                f" (wip: {col['wip_limit']})"
                if col.get("wip_limit") is not None
                else ""
            )
            cnt = f" - {col['task_count']} tasks" if "task_count" in col else ""
            lines.append(
                f"  #{col['id']:<3} {col['name']:<20} phases: [{phases}]{def_str}{wip}{cnt}"
            )
        return "\n".join(lines)
    if "items" in value:
        lines = []
        for item in value["items"]:
            label = (
                item.get("reference")
                or item.get("key")
                or str(item.get("sequence", item.get("id", "")))
            )
            title = (
                item.get("title")
                or item.get("name")
                or item.get("operation")
                or item.get("body")
                or item.get("filename", "")
            )
            lines.append(
                f"{label:12} {title}"
                + (
                    f"  [{item['status']}]  {item.get('assignee') or 'unassigned'}  v{item['version']}"
                    if "status" in item
                    else ""
                )
            )
            if item.get("checkpoint"):
                lines.append("             Next: " + item["checkpoint"]["next_action"])
            if item.get("blockers"):
                lines.append("             Blocked: " + "; ".join(item["blockers"]))
        lines.append(
            f"{len(value['items'])} shown"
            + (f" of {value['total']}" if "total" in value else "")
        )
        if value.get("has_more"):
            lines.append(
                "More available. Continue with "
                + ("--after " if "source" in value else "--cursor ")
                + str(value["next_cursor"])
            )
        return "\n".join(lines)
    return json.dumps(value, indent=2, ensure_ascii=False)


def main():
    args = parser().parse_args()
    try:
        value = execute(args)
        if value is not None:
            print(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if getattr(args, "json", False)
                else human(value)
            )
    except Error as exc:
        print(
            json.dumps(exc.payload) if getattr(args, "json", False) else str(exc),
            file=sys.stderr,
        )
        raise SystemExit(3 if exc.status == 409 else 4 if exc.status == 503 else 2)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"error": {"code": "input_error", "message": str(exc), "fields": {}}}
            )
            if getattr(args, "json", False)
            else str(exc),
            file=sys.stderr,
        )
        raise SystemExit(2)
