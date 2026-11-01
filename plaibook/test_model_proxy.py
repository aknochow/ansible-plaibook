# -*- coding: utf-8 -*-
from __future__ import annotations

import http.client
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from plaibook.model_proxy import ModelProxy

KEY = "real-provider-key"


@pytest.fixture
def upstream():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers), body))
            reply = json.dumps({"ok": True, "echo": self.headers["Authorization"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    yield server, seen
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy(upstream, monkeypatch):
    server, _ = upstream
    instance = ModelProxy(KEY, max_requests=2)
    monkeypatch.setattr(
        instance,
        "_connection",
        lambda: http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5),
    )
    instance.start()
    yield instance
    instance.stop()


def _post(proxy, path="/v1/chat/completions", token=None, body=b"{}", method="POST"):
    connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    headers = {"Content-Type": "application/json", "X-Extra": "dropped"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, body=body if method == "POST" else None, headers=headers)
    response = connection.getresponse()
    reply = response.read()
    connection.close()
    return response.status, reply


def test_valid_call_swaps_the_token_for_the_key_and_hides_it_in_the_reply(proxy, upstream):
    status, reply = _post(proxy, token=proxy.token, body=b'{"m":1}')
    assert status == 200
    assert KEY.encode() not in reply
    path, headers, body = upstream[1][0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert proxy.token not in json.dumps(headers)
    assert "X-Extra" not in headers
    assert body == b'{"m":1}'


@pytest.mark.parametrize("token", [None, "wrong", KEY])
def test_a_wrong_token_never_reaches_upstream(proxy, upstream, token):
    assert _post(proxy, token=token)[0] == 401
    assert upstream[1] == []


@pytest.mark.parametrize("path", ["/v1/files", "/v1/chat/completions/../files", "/v1/organization/users"])
def test_only_allowed_paths_are_forwarded(proxy, upstream, path):
    assert _post(proxy, path=path, token=proxy.token)[0] == 403
    assert upstream[1] == []


def test_get_is_refused(proxy, upstream):
    assert _post(proxy, token=proxy.token, method="GET")[0] == 405
    assert upstream[1] == []


def test_request_limit_stops_spending(proxy, upstream):
    assert _post(proxy, token=proxy.token)[0] == 200
    assert _post(proxy, token=proxy.token)[0] == 200
    assert _post(proxy, token=proxy.token)[0] == 429
    assert len(upstream[1]) == 2


def test_upstream_failure_is_a_502(upstream, monkeypatch):
    instance = ModelProxy(KEY)

    def broken():
        raise ConnectionRefusedError

    class Broken:
        def request(self, *a, **k):
            broken()

        def close(self):
            pass

    monkeypatch.setattr(instance, "_connection", Broken)
    assert instance.forward("/v1/chat/completions", b"{}")[0] == 502
