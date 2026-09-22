"""Provider snapshots → portable v1 bundles; unsupported data is never silent."""

import csv
import io
import re
from html import unescape

from .db import Error


def text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(text(x) for x in value)
    if not isinstance(value, dict):
        return str(value)
    if value.get("type") == "text":
        return value.get("text", "")
    if value.get("type") == "hardBreak":
        return "\n"
    result = text(value.get("content", []))
    if value.get("type") in (
        "paragraph",
        "heading",
        "listItem",
        "blockquote",
        "codeBlock",
    ):
        result += "\n"
    if value.get("type") == "mention":
        result = value.get("attrs", {}).get("text", "")
    return result


def html_text(value):
    return unescape(
        re.sub(
            "<[^>]+>", "", re.sub(r"(?i)<(?:br\s*/?|/p|/div|/li)>", "\n", value or "")
        )
    ).strip()


def phase(name, mapping, default="backlog"):
    return mapping.get(name, default)


def bundle(provider, account, project, records, warnings, complete=False):
    for r in records:
        raw = r.get("metadata", {})
        people = (
            r.get("assignee_source")
            or raw.get("assignees")
            or raw.get("idMembers")
            or raw.get("assignee")
            or []
        )
        if not isinstance(people, list):
            people = [people]
        r["source_assignees"] = [
            {
                "id": str(
                    p.get("accountId")
                    or p.get("id")
                    or p.get("emailAddress")
                    or p.get("name")
                ),
                "name": str(
                    p.get("displayName")
                    or p.get("name")
                    or p.get("emailAddress")
                    or p.get("id")
                ),
            }
            if isinstance(p, dict)
            else {"id": str(p), "name": str(p)}
            for p in people
            if p
        ]
    return {
        "schema_version": 1,
        "provider": provider,
        "account_id": str(account),
        "source_project": str(project),
        "records": records,
        "warnings": list(dict.fromkeys(warnings)),
        "complete": bool(complete),
    }


def normalize(provider, data, account="file", mapping=None):
    mapping = mapping or {}
    if isinstance(data, dict) and data.get("schema_version") == 1:
        return data
    if provider == "kanban":
        return kanban(data, account, mapping)
    if not isinstance(data, dict):
        raise Error(
            422, "provider_export", "The provider export must be a JSON object."
        )
    if provider == "jira":
        return jira(data, account, mapping)
    if provider == "trello":
        return trello(data, account, mapping)
    if provider == "basecamp":
        return basecamp(data, account, mapping)
    raise Error(422, "provider_export", "Unknown provider.")


def files(record, raw, warnings):
    result = []
    for a in raw:
        if a.get("sha256") and "size" in a:
            result.append(
                {
                    "id": str(a["id"]),
                    "filename": a.get("filename") or a.get("name") or "attachment",
                    "size": a["size"],
                    "sha256": a["sha256"],
                    "media_type": a.get("mimeType")
                    or a.get("media_type")
                    or "application/octet-stream",
                    "created_at": a.get("created") or a.get("created_at"),
                }
            )
        else:
            warnings.append(
                "Attachment "
                + str(a.get("id", "unknown"))
                + " on "
                + record
                + " has no verified bytes in this export. Its original metadata is retained."
            )
    return result


def jira(data, account, mapping):
    records = []
    warnings = []
    for issue in data.get("issues", []):
        f = issue["fields"]
        rid = str(issue.get("key") or issue["id"])
        status = f.get("status") or {}
        name = status.get("name", "Backlog")
        category = (status.get("statusCategory") or {}).get("key", "new")
        rawcomments = f.get("comment", {})
        comments = (
            rawcomments
            if isinstance(rawcomments, list)
            else rawcomments.get("comments", [])
        )
        if isinstance(rawcomments, dict) and rawcomments.get(
            "total", len(comments)
        ) != len(comments):
            warnings.append("Comments are incomplete for " + rid + ".")
        changelog = issue.get("changelog", {})
        history = (
            changelog.get("histories", changelog.get("values", []))
            if isinstance(changelog, dict)
            else changelog
        )
        if isinstance(changelog, dict) and changelog.get("total", len(history)) != len(
            history
        ):
            warnings.append("History is incomplete for " + rid + ".")
        original = text(f.get("description")).strip()
        title = f.get("summary", "Untitled")
        if len(title) > 120:
            warnings.append(
                "Title truncated to 120 characters for "
                + rid
                + "; full title retained in source metadata."
            )
        records.append(
            {
                "id": rid,
                "title": title[:120],
                "description": original,
                "type": (f.get("issuetype") or {}).get("name", "Task"),
                "status": name,
                "phase": phase(
                    name,
                    mapping,
                    {
                        "new": "backlog",
                        "indeterminate": "in_progress",
                        "done": "done",
                    }.get(category, "backlog"),
                ),
                "parent_id": (f.get("parent") or {}).get("key"),
                "created_at": f.get("created"),
                "updated_at": f.get("updated"),
                "assignee_source": f.get("assignee"),
                "reporter_source": f.get("reporter"),
                "metadata": f,
                "history": history,
                "comments": [
                    {
                        "id": str(c["id"]),
                        "body": text(c.get("body")).strip() or "(Empty source comment)",
                        "author": (c.get("author") or {}).get("displayName", "Unknown"),
                        "created_at": c.get("created"),
                        "updated_at": c.get("updated"),
                    }
                    for c in comments
                ],
                "attachments": files(rid, f.get("attachment", []), warnings),
            }
        )
    if data.get("total", len(records)) != len(records):
        warnings.append("Issue export is incomplete.")
    complete = data.get("_complete", False)
    if not complete:
        warnings.append(
            "File import: source inventory and history completeness have not been independently verified."
        )
    return bundle(
        "jira",
        account,
        data.get("project", {}).get("id", data.get("project_key", "export")),
        records,
        warnings,
        complete,
    )


def trello(data, account, mapping):
    records = []
    warnings = []
    lists = {x["id"]: x for x in data.get("lists", [])}
    actions = data.get("actions", [])
    if not data.get("_complete", False):
        warnings.append(
            "Trello board JSON contains only the most recent 1,000 actions. Full comment history requires the connected API import."
        )
    comments = {}
    for a in actions:
        if a.get("type") == "commentCard":
            cid = a.get("data", {}).get("card", {}).get("id")
            comments.setdefault(cid, []).append(
                {
                    "id": str(a["id"]),
                    "body": a["data"].get("text") or "(Empty source comment)",
                    "author": (a.get("memberCreator") or {}).get("fullName", "Unknown"),
                    "created_at": a.get("date"),
                }
            )
    for card in data.get("cards", []):
        name = lists.get(card.get("idList"), {}).get("name", "Backlog")
        if name not in mapping:
            warnings.append(
                "Column " + name + " maps to Backlog until you choose a phase."
            )
        desc = card.get("desc", "")
        checks = card.get(
            "checklists",
            [x for x in data.get("checklists", []) if x.get("idCard") == card["id"]],
        )
        for check in checks:
            desc += (
                "\n\n### "
                + check.get("name", "Checklist")
                + "\n"
                + "\n".join(
                    "- ["
                    + ("x" if x.get("state") == "complete" else " ")
                    + "] "
                    + x["name"]
                    for x in check.get("checkItems", [])
                )
            )
        title = card.get("name", "Untitled")
        if len(title) > 120:
            warnings.append(
                "Full title for " + card["id"] + " retained in source metadata."
            )
        records.append(
            {
                "id": str(card["id"]),
                "title": title[:120],
                "description": desc,
                "type": "card",
                "status": name,
                "phase": phase(name, mapping),
                "updated_at": card.get("dateLastActivity"),
                "archived_at": card.get("dateLastActivity")
                if card.get("closed")
                else None,
                "metadata": card,
                "checklists": checks,
                "comments": comments.get(card["id"], []),
                "attachments": files(card["id"], card.get("attachments", []), warnings),
            }
        )
    return bundle(
        "trello",
        account,
        data.get("id", "export"),
        records,
        warnings,
        data.get("_complete", False),
    )


def basecamp(data, account, mapping):
    records = []
    warnings = list(data.get("_warnings", []))
    for item in data.get("recordings", []):
        typ = item.get("type", "Todo")
        supported = typ in (
            "Todo",
            "Todolist",
            "Upload",
            "Message",
            "Document",
            "Schedule::Entry",
            "Question::Answer",
            "Kanban::Card",
            "Kanban::Column",
            "Kanban::Triage",
            "Kanban::NotNow",
        )
        if not supported:
            warnings.append(
                "Unsupported "
                + typ
                + " recording "
                + str(item.get("id"))
                + " remains in the source export."
            )
            continue
        if typ in ("Kanban::Column", "Kanban::Triage", "Kanban::NotNow"):
            continue
        rid = str(item["id"])
        if typ in (
            "Message",
            "Document",
            "Schedule::Entry",
            "Question::Answer",
            "Upload",
        ):
            warnings.append(
                typ
                + " "
                + rid
                + " is preserved as a task with its original type and raw metadata; provider-specific interactions are not reproduced."
            )
        parent = item.get("parent") or {}
        name = (
            item.get("_column_name")
            or (
                parent.get("title")
                if parent.get("type", "").startswith("Kanban::")
                else None
            )
            or ("Done" if item.get("completed") else "Backlog")
        )
        parent_id = str(parent["id"]) if parent.get("type") == "Todolist" else None
        title = item.get("title") or item.get("content") or "Untitled"
        title = html_text(title)
        if len(title) > 120:
            warnings.append("Full title for " + rid + " retained in source metadata.")
        comments = item.get("_comments", [])
        if item.get("comments_count", len(comments)) != len(comments):
            warnings.append("Comments incomplete for " + rid + ".")
        records.append(
            {
                "id": rid,
                "title": title[:120],
                "description": html_text(
                    item.get("description", item.get("content", ""))
                ),
                "type": "Epic" if typ == "Todolist" else typ,
                "status": name,
                "phase": phase(
                    name, mapping, "done" if item.get("completed") else "backlog"
                ),
                "parent_id": parent_id,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "archived_at": item.get("updated_at")
                if item.get("status") in ("archived", "trashed")
                else None,
                "metadata": item,
                "comments": [
                    {
                        "id": str(c["id"]),
                        "body": html_text(c.get("content", ""))
                        or "(Empty source comment)",
                        "author": (c.get("creator") or {}).get("name", "Unknown"),
                        "created_at": c.get("created_at"),
                    }
                    for c in comments
                ],
                "attachments": files(rid, item.get("_attachments", []), warnings),
            }
        )
    if not data.get("_complete", False):
        warnings.append(
            "Basecamp file export completeness has not been independently verified."
        )
    return bundle(
        "basecamp",
        account,
        data.get("id", "export"),
        records,
        warnings,
        data.get("_complete", False),
    )


def kanban(data, account, mapping):
    if isinstance(data, str):
        rows = list(csv.DictReader(io.StringIO(data)))
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("cards", data.get("records", []))
    else:
        raise Error(422, "kanban_file", "Supply CSV rows or JSON cards.")
    records = []
    warnings = ["Generic file import has no independently verified source inventory."]
    for row in rows:
        status = row.get("status") or row.get("column") or "Backlog"
        mapped = row.get("phase") or mapping.get(status)
        if not mapped:
            raise Error(
                422,
                "status_mapping",
                "Choose a phase for " + status + " or add a phase column.",
            )
        records.append(
            {
                "id": str(row.get("id", "")),
                "title": row.get("title", row.get("name", "")),
                "description": row.get("description", ""),
                "type": row.get("type", "Task"),
                "status": status,
                "phase": mapped,
                "parent_id": row.get("parent_id") or None,
                "created_at": row.get("created_at") or None,
                "updated_at": row.get("updated_at") or None,
                "metadata": row,
                "comments": row.get("comments", [])
                if isinstance(row.get("comments"), list)
                else [],
                "attachments": row.get("attachments", [])
                if isinstance(row.get("attachments"), list)
                else [],
            }
        )
    return bundle(
        "kanban",
        account,
        data.get("source_project", "export") if isinstance(data, dict) else "export",
        records,
        warnings,
        False,
    )
