"""Test _api_post's handling of bridge HTTP errors (D2/D3 of
docs/rainforest/design/2026-08-23-erro-de-api-lido-como-lista-vazia.md).

Reproduces the defect: _api_post used to swallow every failure (including a
5xx bridge bug) into None, which every read caller then renders as an empty
list. A local HTTP server stands in for the bridge, so this needs no network
access and no bridge process on the machine, and runs in CI.
"""

import json
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import whatsapp
from whatsapp import Message

# The exact body the bridge returns today for the SQL bug this defect was
# found through (see the plan/design doc referenced above).
REAL_500_BODY = {"error": "SQL logic error: no such column: messages.content (1)"}


class _FakeBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep test output quiet

    def _write_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The client (e.g. the timed-out request in test_timeout_returns_none)
            # may already have closed its side of the socket by the time the
            # slow handler gets here — nothing left to do.
            pass

    def do_POST(self):
        # Drain the request body before responding. requests.post() always
        # sends a Content-Length body (json=payload); leaving unread bytes
        # in the socket's OS receive buffer when the connection is closed
        # right after the response can make the OS send RST instead of a
        # clean FIN, which surfaced as an intermittent ConnectionAbortedError
        # on the client side on Windows.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/500":
            self._write_json(500, REAL_500_BODY)
        elif self.path == "/sender_name":
            self._write_json(500, REAL_500_BODY)
        elif self.path == "/404":
            self._write_json(404, {"error": "not found"})
        elif self.path == "/timeout":
            time.sleep(2)
            self._write_json(200, {})
        elif self.path == "/200":
            self._write_json(200, {"chats": [{"jid": "123@s.whatsapp.net"}]})
        else:
            self._write_json(404, {"error": "unmapped path in fake bridge"})


@pytest.fixture(scope="module")
def fake_bridge():
    httpd = HTTPServer(("127.0.0.1", 0), _FakeBridgeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        thread.join()


def test_5xx_raises_value_error_with_path_status_and_body(fake_bridge):
    """A 5xx is a bridge-side bug and must not read as 'no data' (D2)."""
    with pytest.raises(ValueError) as exc_info:
        whatsapp._api_post("/500", {}, fake_bridge)
    message = str(exc_info.value)
    assert "/500" in message
    assert "500" in message
    assert "no such column: messages.content" in message


def test_404_returns_none(fake_bridge):
    assert whatsapp._api_post("/404", {}, fake_bridge) is None


def test_timeout_returns_none(fake_bridge):
    assert whatsapp._api_post("/timeout", {}, fake_bridge, timeout=1) is None


def test_connection_refused_returns_none():
    # Bind to grab a free port, then close it immediately so nothing is
    # listening there when _api_post connects.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    assert whatsapp._api_post("/whatever", {}, f"http://127.0.0.1:{port}") is None


def test_200_returns_parsed_json_unchanged(fake_bridge):
    result = whatsapp._api_post("/200", {}, fake_bridge)
    assert result == {"chats": [{"jid": "123@s.whatsapp.net"}]}


# --- D6: the ValueError from D2 must not turn a message invisible ---------
#
# _api_post now raises ValueError on a bridge 5xx (D2). get_sender_name is
# the one _api_post caller whose failure sits in the middle of building a
# message line (format_message), so an uncaught ValueError there used to
# propagate into format_message's broad `except Exception`, which swallowed
# it and returned only the "[timestamp] Chat: X " prefix - the sender and
# the message content vanished from the output with no trace.


def _make_message(sender="5562999999999@s.whatsapp.net", content="texto que o usuario precisa ler"):
    return Message(
        timestamp=datetime(2026, 8, 23, 10, 0, 0),
        sender=sender,
        content=content,
        is_from_me=False,
        chat_jid="123@g.us",
        id="msg-1",
        chat_name="Alice",
    )


def test_get_sender_name_falls_back_on_value_error(monkeypatch):
    """A 5xx on /sender_name must degrade to the JID, not raise (D6)."""
    def fake_api_post(path, payload, base_url, timeout=whatsapp.REQUEST_TIMEOUT):
        raise ValueError(f"Bridge API error on {path}: HTTP 500 - boom")

    monkeypatch.setattr(whatsapp, "_api_post", fake_api_post)
    monkeypatch.setattr(whatsapp.accounts, "resolve_account", lambda account: "http://fake-bridge")
    whatsapp._sender_name_cache.clear()

    jid = "5562999999999@s.whatsapp.net"
    assert whatsapp.get_sender_name(jid) == jid
    # Don't cache a transport/bridge failure - a later retry may still work.
    assert (("http://fake-bridge", jid)) not in whatsapp._sender_name_cache


def test_format_message_survives_sender_name_500(fake_bridge, monkeypatch):
    """End to end: format_message keeps the content and falls back to the
    JID as the sender name when /sender_name 500s - it must not come back
    as just the '[timestamp] Chat: X ' prefix (the regression this closes)."""
    monkeypatch.setattr(whatsapp.accounts, "resolve_account", lambda account=None: fake_bridge)
    whatsapp._sender_name_cache.clear()

    message = _make_message()
    result = whatsapp.format_message(message)

    assert "From:" in result
    assert message.sender in result
    assert message.content in result


def test_format_message_surfaces_other_exception_instead_of_silence(monkeypatch):
    """A different exception out of get_sender_name must not vanish in
    silence: format_message signals the failure in the returned text,
    the same pattern get_message_context already uses for a failed fetch."""
    def raise_runtime_error(sender_jid, account=None):
        raise RuntimeError("boom - not a ValueError")

    monkeypatch.setattr(whatsapp, "get_sender_name", raise_runtime_error)

    message = _make_message()
    result = whatsapp.format_message(message)

    assert "Chat: Alice" in result
    assert "boom - not a ValueError" in result
    # The old, silent-drop behavior: only the prefix, nothing to say a
    # message got lost.
    assert result.strip() != "[2026-08-23 10:00:00] Chat: Alice"
