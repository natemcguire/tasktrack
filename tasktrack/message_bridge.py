"""Personal macOS iMessage transport. No credentials or message text on stdout."""

import os
import subprocess
import time
import uuid

from .credentials import access_token
from .db import Error
from .remote import RemoteService

SCRIPT = """on run argv
tell application "Messages"
set targetService to first service whose service type = iMessage
set targetBuddy to buddy (item 1 of argv) of targetService
send (item 2 of argv) to targetBuddy
end tell
end run"""


def deliver_once(url):
    client = RemoteService(url, os.environ.get("TT_TOKEN") or access_token(url))

    def call(path, body):
        return client.mutate(
            "POST",
            "/api/v1/agent-deliveries/" + path,
            body,
            None,
            str(uuid.uuid4()),
            "personal-message-bridge",
            "cli",
        )[1]

    item = call("claim", {})["delivery"]
    if not item:
        return False
    status = "uncertain"
    try:
        call("check", {"id": item["id"], "lease_token": item["lease_token"]})
        subprocess.run(
            ["osascript", "-e", SCRIPT, item["recipient"], item["body"]],
            check=True,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        status = "sent"
    finally:
        call(
            "ack",
            {"id": item["id"], "lease_token": item["lease_token"], "status": status},
        )
    return True


def command(args):
    import sys

    if sys.platform != "darwin":
        raise Error(
            422, "mac_required", "The personal iMessage bridge runs on a signed-in Mac."
        )
    url = args.url or os.environ.get("TT_URL")
    if not url:
        raise Error(422, "url_required", "Set TT_URL or supply --url.")
    sent = 0
    while True:
        if deliver_once(url):
            sent += 1
        if args.once:
            return {"sent": sent}
        time.sleep(5)
