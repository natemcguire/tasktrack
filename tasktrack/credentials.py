"""Private CLI credential storage and serialized refresh; never print credentials."""

import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .db import Error
from .remote import NoRedirect, RemoteService


def credential_path(url):
    root = Path(os.environ.get("TT_CONFIG_DIR", Path.home() / ".config/tasktrack"))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise Error(
            403, "unsafe_config", "Credential directory must be private (mode 0700)."
        )
    return root / (hashlib.sha256(url.rstrip("/").encode()).hexdigest() + ".json")


def post(url, path, data):
    RemoteService(url, "validation-only")
    request = Request(
        url.rstrip("/") + path,
        data=json.dumps(data).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Tasktrack/1.0",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as r:
            return json.loads(r.read())
    except HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            raise Error(
                e.code, "auth_failed", "Authentication service unavailable."
            ) from None
    except (URLError, TimeoutError):
        raise Error(
            503,
            "auth_unavailable",
            "Could not reach authentication service. Start enrollment again if a credential response was lost.",
        ) from None


def save(path, data):
    fd = os.open(
        str(path) + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(path) + ".tmp", path)


def access_token(url):
    path = credential_path(url)
    with os.fdopen(
        os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w"
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise Error(
                403,
                "unsafe_credentials",
                "Credential file must be private (mode 0600).",
            )
        data = json.loads(path.read_text())
        if data["expires_at"] > time.time() + 60:
            return data["access_token"]
        try:
            updated = post(
                url,
                "/oauth/token",
                {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]},
            )
        except Error:
            path.unlink(missing_ok=True)
            raise
        if "error" in updated:
            path.unlink(missing_ok=True)
            raise Error(
                401,
                "reauthorize",
                "Agent access expired or was revoked. Run tt auth login again.",
            )
        updated["expires_at"] = int(time.time()) + updated["expires_in"]
        save(path, updated)
        return updated["access_token"]


def command(args):
    url = args.url or os.environ.get("TT_URL")
    if not url:
        raise Error(422, "url_required", "Supply --url or TT_URL.")
    RemoteService(url, "validation-only")
    path = credential_path(url)
    if args.operation == "logout":
        path.unlink(missing_ok=True)
        return {
            "signed_out": True,
            "note": "Local credentials removed. Revoke the agent in account settings to end server access.",
        }
    if args.operation == "status":
        if not path.exists():
            return {"signed_in": False}
        data = json.loads(path.read_text())
        return {
            "signed_in": True,
            "agent_id": data.get("agent_id"),
            "access_expires_at": data["expires_at"],
        }
    request = {
        "name": args.name,
        "workspace_id": args.workspace,
        "project_ids": args.project,
        "capabilities": args.capability
        or [
            "project.read",
            "work.read",
            "work.edit",
            "work.execute",
            "notes.read",
            "notes.write",
        ],
    }
    if args.owner_email:
        request["owner_email"] = args.owner_email
    result = post(url, "/oauth/device_authorization", request)
    if "error" in result:
        raise Error(400, "enrollment_failed", str(result["error"]))
    print(
        "Open "
        + result.get("verification_uri_complete", result["verification_uri"])
        + ". Review access and approve with your passkey. "
        + "Manual code: "
        + result["user_code"]
        + ".",
        file=sys.stderr,
        flush=True,
    )
    deadline = time.monotonic() + result["expires_in"]
    interval = result["interval"]
    while time.monotonic() < deadline:
        time.sleep(interval)
        answer = post(
            url,
            "/oauth/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": result["device_code"],
            },
        )
        if answer.get("error") == "authorization_pending":
            continue
        if answer.get("error") == "slow_down":
            interval += 5
            continue
        if "error" in answer:
            raise Error(
                401,
                "enrollment_ended",
                "Enrollment was denied or expired. Start again.",
            )
        answer["expires_at"] = int(time.time()) + answer["expires_in"]
        save(path, answer)
        return {
            "authenticated": True,
            "agent_id": answer["agent_id"],
            "credentials_saved": str(path),
        }
    raise Error(
        401, "enrollment_expired", "Enrollment expired. Run tt auth login again."
    )
