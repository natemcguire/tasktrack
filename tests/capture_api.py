"""Execute the documented API workflow and capture real responses.

Run: python3 tests/capture_api.py. Uses a disposable service and database.
"""

import hashlib
import json
import os
import select
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = []
    with tempfile.TemporaryDirectory(prefix="tasktrack-api-") as temporary:
        env = {
            **os.environ,
            "TT_DATA_DIR": str(Path(temporary) / "data"),
            "PYTHONPATH": str(ROOT),
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "tasktrack", "serve", "--port", "0"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            if not select.select([process.stdout], [], [], 10)[0]:
                raise RuntimeError("Disposable API server did not start.")
            url = process.stdout.readline().split("listening on ")[1].split(" ")[0]

            def call(
                title,
                method,
                path,
                body=None,
                actor="nate",
                session=None,
                key=None,
                expected=200,
            ):
                headers = {"X-Actor": actor, "X-Via": "api"}
                if session:
                    headers["X-Session"] = session
                if method != "GET":
                    headers.update(
                        {
                            "Content-Type": "application/json",
                            "Idempotency-Key": key or title.lower().replace(" ", "-"),
                        }
                    )
                request = Request(
                    url + path,
                    method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers,
                )
                try:
                    response = urlopen(request, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    result = json.loads(response.read())
                    assert response.status == expected, result
                    records.append(
                        {
                            "title": title,
                            "request": {
                                "method": method,
                                "url": url + path,
                                "headers": headers,
                                "body": body,
                            },
                            "status": response.status,
                            "response": result,
                        }
                    )
                return result

            health = call("Instance identity", "GET", "/api/v1/health")
            project = call(
                "Create project",
                "POST",
                "/api/v1/projects",
                {
                    "key": "HBR",
                    "name": "Harbor",
                    "brief_markdown": "# Reliable checkout\nOne order and receipt for every checkout, including retries.",
                },
                expected=201,
            )
            epic = call(
                "Create epic",
                "POST",
                "/api/v1/tasks",
                {
                    "project_id": project["id"],
                    "kind": "epic",
                    "title": "Checkout reliability",
                    "description_markdown": "Recover disconnected checkouts.",
                    "acceptance_criteria": ["All retry cases pass."],
                    "assignee": "nate",
                },
                expected=201,
            )
            body = {
                "project_id": project["id"],
                "kind": "task",
                "parent_id": epic["id"],
                "title": "Make checkout retries safe",
                "description_markdown": "A disconnected client retries checkout. Preserve the original order and receipt.",
                "acceptance_criteria": [
                    "Repeating a checkout request creates one order.",
                    "A retry returns the original receipt.",
                ],
                "assignee": "codex@harbor",
                "priority": "high",
                "dependency_ids": [],
            }
            task = call(
                "Create task",
                "POST",
                "/api/v1/tasks",
                body,
                key="task-create-001",
                expected=201,
            )
            repeated = call(
                "Retry task creation",
                "POST",
                "/api/v1/tasks",
                body,
                key="task-create-001",
                expected=201,
            )
            assert repeated == task
            call(
                "Rename project",
                "PATCH",
                f"/api/v1/projects/{project['id']}",
                {"expected_version": 1, "key": "HARBOR", "name": "Harbor checkout"},
            )
            call("Resolve earlier reference", "GET", f"/api/v1/tasks/HBR-{task['id']}")
            route = f"/api/v1/tasks/{task['id']}"
            task = call(
                "Claim task",
                "POST",
                route + "/claim",
                {"expected_version": 1},
                actor="codex@harbor",
                session="checkout-1",
                key="task-claim-001",
            )
            assert (
                call(
                    "Retry claim after version advanced",
                    "POST",
                    route + "/claim",
                    {"expected_version": 1},
                    actor="codex@harbor",
                    session="checkout-1",
                    key="task-claim-001",
                )
                == task
            )
            call(
                "Reject changed request key reuse",
                "POST",
                route + "/claim",
                {"expected_version": 2},
                actor="codex@harbor",
                session="checkout-1",
                key="task-claim-001",
                expected=409,
            )
            call(
                "Reject stale edit",
                "PATCH",
                route,
                {"expected_version": 1, "title": "A stale title"},
                expected=409,
            )
            cp = json.loads((ROOT / "docs/example-checkpoint.json").read_text())
            task = call(
                "Save checkpoint",
                "POST",
                route + "/checkpoint",
                {"expected_version": task["version"], "checkpoint": cp},
                actor="codex@harbor",
                session="checkout-1",
            )
            task = call(
                "Handoff to another actor",
                "POST",
                route + "/handoff",
                {"expected_version": task["version"], "checkpoint": cp, "to": "nate"},
                actor="codex@harbor",
                session="checkout-1",
            )
            call(
                "Recover context from brief",
                "GET",
                f"/api/v1/brief?project_id={project['id']}",
            )
            task = call(
                "Claim handed off work",
                "POST",
                route + "/claim",
                {"expected_version": task["version"]},
                session="review-prep",
            )
            result = {
                "summary": "Disconnected retries preserve the original order and receipt.",
                "evidence": [
                    {
                        "label": "Retry regression",
                        "uri": "https://example.org/harbor/runs/43",
                    }
                ],
            }
            task = call(
                "Submit for review",
                "POST",
                route + "/submit",
                {"expected_version": task["version"], "result": result},
                session="review-prep",
            )
            task = call(
                "Accept completed work",
                "POST",
                route + "/complete",
                {
                    "expected_version": task["version"],
                    "acceptance_note": "Both retry cases pass, and the receipt stays the same.",
                },
                actor="reviewer",
            )
            comment = call(
                "Append a review note",
                "POST",
                route + "/comments",
                {"body": "Accepted after checking the retry evidence."},
                actor="reviewer",
                expected=201,
            )
            blob = b"Tasktrack attachment roundtrip\nExact bytes: \x00\xff\n"
            boundary = "tasktrack-example-boundary"
            multipart = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="comment_id"\r\n\r\n{comment["id"]}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="evidence.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                + blob
                + f"\r\n--{boundary}--\r\n".encode()
            )
            headers = {
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "X-Actor": "reviewer",
                "Idempotency-Key": "attachment-001",
            }
            with urlopen(
                Request(url + route + "/attachments", data=multipart, headers=headers)
            ) as response:
                attachment = json.loads(response.read())
                assert response.status == 201
                records.append(
                    {
                        "title": "Upload attachment",
                        "request": {
                            "method": "POST",
                            "url": url + route + "/attachments",
                            "headers": headers,
                            "multipart": {
                                "filename": "evidence.bin",
                                "comment_id": comment["id"],
                                "size": len(blob),
                                "sha256": hashlib.sha256(blob).hexdigest(),
                            },
                        },
                        "status": response.status,
                        "response": attachment,
                    }
                )
            with urlopen(url + attachment["download_url"]) as response:
                downloaded = response.read()
                assert downloaded == blob
                records.append(
                    {
                        "title": "Download exact attachment bytes",
                        "request": {
                            "method": "GET",
                            "url": url + attachment["download_url"],
                            "headers": {},
                        },
                        "status": response.status,
                        "response": {
                            "size": len(downloaded),
                            "sha256": hashlib.sha256(downloaded).hexdigest(),
                            "content_type": response.headers["Content-Type"],
                            "content_disposition": response.headers[
                                "Content-Disposition"
                            ],
                            "exact_bytes_equal": True,
                        },
                    }
                )
            history = call("Read chronological history", "GET", route + "/history")
            assert len([e for e in history["items"] if e["operation"] == "create"]) == 1
            assert len([e for e in history["items"] if e["operation"] == "claim"]) == 2
            assert call("Final record", "GET", route)["version"] == task["version"]
            (ROOT / "docs/api-transcript.json").write_text(
                json.dumps(
                    {"instance_id": health["instance_id"], "records": records}, indent=2
                )
                + "\n"
            )
            lines = [
                "# Executed API examples",
                "",
                "Captured from a disposable running Tasktrack instance. All data is synthetic.",
                "The port, UUIDs, IDs and versions below are actual values from that run.",
                "Use your server URL and returned IDs when reproducing it.",
                "",
                "[Full unabridged transcript](api-transcript.json). Regenerate with",
                "`python3 tests/capture_api.py`. This script asserts retry equality, rejection of",
                "stale writes, handoff ownership, review/completion, and exact upload/download bytes.",
                "",
            ]
            for entry in records:
                if entry["title"] in {"Read chronological history", "Final record"}:
                    continue
                request = entry["request"]
                command = (
                    "curl -sS -X "
                    + request["method"]
                    + " "
                    + shlex.quote(request["url"])
                )
                for key, value in request["headers"].items():
                    if key != "Content-Type" or "multipart" not in request:
                        command += " \\\n  -H " + shlex.quote(key + ": " + value)
                if "multipart" in request:
                    command += (
                        " \\\n  -F 'file=@evidence.bin' -F 'comment_id="
                        + str(request["multipart"]["comment_id"])
                        + "'"
                    )
                elif request.get("body") is not None:
                    command += " \\\n  -d " + shlex.quote(json.dumps(request["body"]))
                result = entry["response"]
                if "title" in result:
                    result = {
                        k: result[k]
                        for k in (
                            "id",
                            "reference",
                            "title",
                            "status",
                            "assignee",
                            "execution",
                            "version",
                            "checkpoint",
                            "result",
                            "completion",
                        )
                    }
                lines.extend(
                    [
                        "## " + entry["title"],
                        "",
                        "```sh",
                        command,
                        "```",
                        "",
                        f"HTTP **{entry['status']}** (selected fields for task records):",
                        "",
                        "```json",
                        json.dumps(result, indent=2),
                        "```",
                        "",
                    ]
                )
            (ROOT / "docs/api-examples.md").write_text("\n".join(lines))
            print(
                f"Captured {len(records)} real requests, with workflow assertions and byte verification."
            )
        finally:
            process.terminate()
            _, error = process.communicate(timeout=10)
            if error:
                raise RuntimeError(error)


if __name__ == "__main__":
    main()
