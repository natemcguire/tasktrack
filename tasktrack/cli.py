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
                type=int if field == "before_id" else str,
            )
        if "reopen_parent" in ACTION_FIELDS[operation]:
            p.add_argument("--reopen-parent", action="store_true", default=None)
    return root


def execute(args):
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
        from .remote import RemoteService

        service = RemoteService(remote_url, os.environ.get("TT_TOKEN"))
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
