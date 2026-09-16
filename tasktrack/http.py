"""Loopback-only HTTP transport. All application writes go through Service."""

import json
import re
import socket
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .db import Error, encode

STATIC = Path(__file__).with_name("static")
MAX_BODY = 10 * 1024 * 1024 + 65536


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service):
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise Error(
                422,
                "loopback_required",
                "Tasktrack v1 serves only 127.0.0.1 or localhost.",
            )
        self.service = service
        super().__init__(address, Handler)
        self.service.base_url = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "Tasktrack/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, message, *args):
        # Avoid logging task content or caller-controlled query strings.
        if self.server.service.config.get("access_log", False):
            super().log_message(message, *args)

    def send(
        self,
        status,
        content,
        content_type="application/json; charset=utf-8",
        headers=None,
    ):
        if not isinstance(content, bytes):
            content = encode(content).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def boundary(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts |= {"127.0.0.1", "localhost"}
        if self.headers.get("Host") not in hosts:
            raise Error(400, "foreign_host", "Use the local Tasktrack host and port.")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + h for h in hosts}:
            raise Error(
                400, "foreign_origin", "Foreign browser origins are not allowed."
            )
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise Error(
                400, "foreign_origin", "Cross-site browser requests are not allowed."
            )

    def handle_request(self):
        try:
            self.boundary()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            params = parse_qs(parsed.query, keep_blank_values=True)
            if any(len(v) != 1 for v in params.values()):
                raise Error(
                    400, "duplicate_filter", "Supply each query parameter only once."
                )
            query = {k: v[0] for k, v in params.items()}
            service = self.server.service
            if self.command == "GET":
                match = re.fullmatch(r"/api/v1/attachments/(\d+)/content", path)
                if match:
                    if query:
                        raise Error(
                            422,
                            "unknown_filter",
                            "Attachment downloads do not accept filters.",
                        )
                    metadata, blob = service.attachment(int(match[1]))
                    self.send(
                        200,
                        blob.read_bytes(),
                        "application/octet-stream",
                        {
                            "Content-Disposition": "attachment; filename=\"download\"; filename*=UTF-8''"
                            + quote(metadata["filename"], safe="")
                        },
                    )
                elif path.startswith("/api/"):
                    self.send(
                        200, service.read(path, query, self.headers.get("X-Actor"))
                    )
                elif path in {"/app.js", "/style.css"}:
                    self.send(
                        200,
                        (STATIC / path[1:]).read_bytes(),
                        "text/javascript; charset=utf-8"
                        if path.endswith(".js")
                        else "text/css; charset=utf-8",
                    )
                elif path in {"/", "/triage"} or re.fullmatch(
                    r"/(projects/[A-Za-z0-9]+(?:/settings)?|tasks/[A-Za-z0-9-]+)", path
                ):
                    self.send(
                        200,
                        (STATIC / "index.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                else:
                    raise Error(404, "not_found", "Page not found.")
                return
            if query:
                raise Error(
                    422,
                    "unknown_filter",
                    "Write routes do not accept query parameters.",
                )
            if self.headers.get("Transfer-Encoding"):
                raise Error(
                    400,
                    "invalid_length",
                    "Send a bounded Content-Length; chunked uploads are not supported.",
                )
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                raise Error(400, "invalid_length", "Content-Length must be an integer.")
            if length < 0:
                raise Error(400, "invalid_length", "Supply Content-Length.")
            if length > MAX_BODY:
                raise Error(413, "upload_too_large", "Files must be 10 MiB or smaller.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Error(
                    400,
                    "interrupted_upload",
                    "Request ended before its declared body was received.",
                )
            content_type = self.headers.get("Content-Type", "")
            upload = None
            if path.endswith("/attachments") and self.command == "POST":
                if not content_type.lower().startswith("multipart/form-data;"):
                    raise Error(
                        400,
                        "content_type",
                        "Attachments require multipart/form-data with a boundary.",
                    )
                message = BytesParser(policy=policy.default).parsebytes(
                    (
                        "Content-Type: "
                        + content_type
                        + "\r\nMIME-Version: 1.0\r\n\r\n"
                    ).encode()
                    + raw
                )
                if not message.is_multipart() or message.defects:
                    raise Error(400, "invalid_multipart", "Malformed multipart upload.")
                body, seen = {}, set()
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if (
                        name not in {"file", "comment_id"}
                        or name in seen
                        or part.defects
                    ):
                        raise Error(
                            400,
                            "invalid_multipart",
                            "Use one file and an optional comment_id field.",
                        )
                    seen.add(name)
                    if name == "file":
                        upload = part.get_payload(decode=True)
                        body.update(
                            filename=part.get_filename(),
                            media_type=part.get_content_type(),
                        )
                    else:
                        try:
                            body["comment_id"] = int(
                                part.get_payload(decode=True).decode()
                            )
                        except (ValueError, UnicodeError):
                            raise Error(
                                422,
                                "validation",
                                "comment_id must be an integer.",
                                {
                                    "comment_id": "Use a comment ID belonging to this task."
                                },
                            )
                if upload is None:
                    raise Error(
                        422, "validation", "Upload a file.", {"file": "Required."}
                    )
            else:
                if content_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise Error(
                        400, "content_type", "Mutations require application/json."
                    )
                if length > 1024 * 1024:
                    raise Error(
                        413, "body_too_large", "JSON requests must be 1 MiB or smaller."
                    )
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise Error(
                        400, "invalid_json", "Request body must be valid UTF-8 JSON."
                    )
            status, result = service.mutate(
                self.command,
                path,
                body,
                self.headers.get("X-Actor"),
                self.headers.get("Idempotency-Key"),
                self.headers.get("X-Session"),
                self.headers.get("X-Via", "api"),
                upload,
            )
            self.send(status, result)
        except Error as exc:
            self.send(
                exc.status,
                exc.payload,
                headers={"Retry-After": "1"} if exc.status == 503 else None,
            )
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            import traceback

            traceback.print_exc()
            self.send(
                500,
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Unexpected server error. See the local service log.",
                        "fields": {},
                    }
                },
            )

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request
    do_OPTIONS = handle_request


def serve(service, host="127.0.0.1", port=7777):
    with Server((host, port), service) as server:
        print(
            f"Tasktrack listening on {service.base_url} (data: {service.store.directory})",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
