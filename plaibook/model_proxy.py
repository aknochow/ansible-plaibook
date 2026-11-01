# -*- coding: utf-8 -*-
"""Hold a provider key on the host, and forward one run's model calls with it.

The container gets a token that only works here and only for this run. A stolen
token can spend until the request limit, and it stops working when the run ends.
"""

from __future__ import annotations

import hmac
import http.client
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENAI_HOST = "api.openai.com"
ALLOWED_PATHS = frozenset({"/v1/chat/completions"})
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_REQUESTS = 500
UPSTREAM_TIMEOUT_SECONDS = 600


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Request lines stay out of the review's output."""

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status: int, message: str) -> None:
        self.close_connection = True
        self._reply(status, b'{"error":{"message":"' + message.encode("ascii") + b'"}}')

    def do_POST(self) -> None:  # noqa: N802
        proxy = self.server.proxy
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {proxy.token}".encode("ascii")):
            self._refuse(401, "invalid run token")
            return
        if self.path not in ALLOWED_PATHS:
            self._refuse(403, "path not allowed")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._refuse(411, "length required")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._refuse(413, "body too large")
            return
        if not proxy.take_request():
            self._refuse(429, "run request limit reached")
            return
        body = self.rfile.read(length)
        status, content_type, reply = proxy.forward(self.path, body)
        self._reply(status, reply, content_type)

    def do_GET(self) -> None:  # noqa: N802
        self._refuse(405, "method not allowed")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    proxy: "ModelProxy"


class ModelProxy:
    """Forward allowed calls for one run to one fixed upstream."""

    def __init__(self, api_key: str, *, upstream_host: str = OPENAI_HOST, max_requests: int = MAX_REQUESTS):
        self.token = "plaibook-run-" + secrets.token_urlsafe(32)
        self._api_key = api_key
        self._upstream_host = upstream_host
        self._remaining = max_requests
        self._lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.proxy = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def take_request(self) -> bool:
        with self._lock:
            if self._remaining <= 0:
                return False
            self._remaining -= 1
            return True

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPSConnection(self._upstream_host, timeout=UPSTREAM_TIMEOUT_SECONDS)

    def forward(self, path: str, body: bytes) -> tuple[int, str, bytes]:
        """One upstream call. Redirects are returned, not followed."""
        connection = self._connection()
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            reply = response.read().replace(self._api_key.encode("utf-8"), b"***")
            return response.status, response.getheader("Content-Type", "application/json"), reply
        except OSError:
            return 502, "application/json", b'{"error":{"message":"upstream request failed"}}'
        finally:
            connection.close()
