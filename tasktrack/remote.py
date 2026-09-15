"""HTTP transport for the existing CLI contracts."""

import hashlib
import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .db import Error


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class Download:
    def __init__(self, content):
        self.content = content

    def read_bytes(self):
        return self.content


class RemoteService:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise Error(
                422,
                "remote_url",
                "TT_URL must use HTTPS (or loopback HTTP for development).",
            )
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise Error(
                422,
                "remote_url",
                "TT_URL must be the site origin, without credentials or a path.",
            )
        if not token:
            raise Error(
                401,
                "missing_token",
                "Set TT_TOKEN to an agent token from Account → Agent access.",
            )
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, path, method="GET", body=None, headers=None, binary=False):
        request = Request(
            self.url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "User-Agent": "Tasktrack/" + __version__,
                "Accept": "application/json",
                "X-Via": "cli",
                **(headers or {}),
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read()
                return response.status, data if binary else json.loads(data)
        except HTTPError as exc:
            try:
                data = json.loads(exc.read())["error"]
            except (ValueError, KeyError):
                raise Error(
                    exc.code,
                    "remote_error",
                    "The hosted service returned an unexpected response.",
                ) from None
            raise Error(
                exc.code,
                data["code"],
                data["message"],
                data.get("fields"),
                **{
                    k: v
                    for k, v in data.items()
                    if k not in {"code", "message", "fields"}
                },
            ) from None
        except (URLError, TimeoutError):
            raise Error(
                503,
                "connection_error",
                "Could not reach Tasktrack. Retry with the same request ID.",
            ) from None

    def read(self, path, query=None, actor=None):
        return self.request(path + ("?" + urlencode(query) if query else ""))[1]

    def mutate(self, method, path, body, actor, request_key, session, via, upload=None):
        headers = {"Idempotency-Key": request_key}
        if session:
            headers["X-Session"] = session
        if upload is None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(body).encode()
        else:
            boundary = "tasktrack" + uuid.uuid4().hex
            filename = (
                body["filename"].replace('"', "_").replace("\r", "").replace("\n", "")
            )
            raw = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {body["media_type"]}\r\n\r\n'.encode()
                + upload
                + b"\r\n"
            )
            if body.get("comment_id") is not None:
                raw += f'--{boundary}\r\nContent-Disposition: form-data; name="comment_id"\r\n\r\n{body["comment_id"]}\r\n'.encode()
            raw += f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        return self.request(path, method, raw, headers)

    def attachment(self, identifier):
        _, content = self.request(
            f"/api/v1/attachments/{identifier}/content", binary=True
        )
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }, Download(content)
