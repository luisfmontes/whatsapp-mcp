"""Tests for task 4 of the soft-delete design
(docs/rainforest/planos/2026-09-24-soft-delete.md, D2/D3): the revoked_at
filter that keeps the transcription sweep from touching a message the sender
took back, and the get_deleted_message client/tool pair that exposes the
bridge's /api/deleted_message endpoint — the one explicit path to a message's
withdrawn content.

Run: python3 -m unittest test_deleted_message -v
"""

import json
import sqlite3
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import main
import transcribe
import whatsapp

# The real messages table DDL (mirrors the CREATE TABLE in
# whatsapp-bridge/main.go), so pending_audios runs against the actual schema
# shape instead of a stand-in with only the columns its SELECT happens to
# touch.
MESSAGES_DDL = """
CREATE TABLE messages (
    id TEXT,
    chat_jid TEXT,
    sender TEXT,
    content TEXT,
    timestamp TIMESTAMP,
    is_from_me BOOLEAN,
    media_type TEXT,
    filename TEXT,
    url TEXT,
    media_key BLOB,
    file_sha256 BLOB,
    file_enc_sha256 BLOB,
    file_length INTEGER,
    sender_jid TEXT,
    quoted_message_id TEXT,
    quoted_sender TEXT,
    quoted_content TEXT,
    mentions TEXT,
    revoked_at TIMESTAMP,
    edited_at TIMESTAMP,
    previous_content TEXT,
    PRIMARY KEY (id, chat_jid)
);
"""


class TestSweepSkipsRevoked(unittest.TestCase):
    """D2: pending_audios must not queue a revoked audio for transcription.
    Soft delete (D1) leaves media_type='audio' and empty content on a revoked
    row exactly as they were before the revoke (MarkMessageRevoked no longer
    clears anything) — without the revoked_at filter the sweep can't tell a
    revoked audio apart from one genuinely still pending."""

    def _db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(MESSAGES_DDL)
        return conn

    def test_audio_apagado_fora_da_varredura(self):
        conn = self._db()
        conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', '', '2026-09-24 10:00:00', NULL)",
            ("MSG-AUDIO-PENDENTE", "grupo-teste@g.us"),
        )
        conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', '', '2026-09-24 10:01:00', '2026-09-24 10:02:00')",
            ("MSG-AUDIO-APAGADO", "grupo-teste@g.us"),
        )
        conn.commit()

        rows = transcribe.pending_audios(conn)
        ids = [row[0] for row in rows]

        self.assertEqual(ids, ["MSG-AUDIO-PENDENTE"])
        self.assertNotIn("MSG-AUDIO-APAGADO", ids)


class _FakeDeletedMessageBridge(BaseHTTPRequestHandler):
    """Stands in for the bridge's POST /api/deleted_message, mirroring
    getDeletedMessage's two outcomes: 200 with the original content for a
    revoked/edited message, 404 with {"error": ...} (writeJSONError's shape)
    for one that is neither."""

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep test output quiet

    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        if self.path != "/deleted_message":
            self._write_json(404, {"error": "unmapped path in fake bridge"})
            return
        message_id = body.get("message_id")
        if message_id == "MSG-APAGADA":
            self._write_json(200, {
                "content": "legenda original",
                "previous_content": None,
                "media_type": "image",
                "revoked_at": "2026-09-24T10:00:00Z",
                "edited_at": None,
            })
        elif message_id == "MSG-COMUM":
            self._write_json(404, {"error": "message MSG-COMUM was neither deleted nor edited"})
        else:
            self._write_json(500, {"error": "unexpected message id in fake bridge"})


class WhatsappGetDeletedMessageTest(unittest.TestCase):
    """whatsapp.get_deleted_message must mirror download_media's contract
    (raw status-code branching), not _api_post's — _api_post collapses every
    4xx into None with no message, which would swallow the 404 refusal this
    tool exists to surface."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), _FakeDeletedMessageBridge)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.httpd.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join()

    def test_apagada_devolve_conteudo(self):
        with mock.patch.object(whatsapp.accounts, "resolve_account", return_value=self.base_url):
            result, status = whatsapp.get_deleted_message("MSG-APAGADA", "grupo-teste@g.us")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "legenda original")
        self.assertEqual(result["media_type"], "image")
        self.assertEqual(status, "OK")

    def test_comum_devolve_a_recusa(self):
        with mock.patch.object(whatsapp.accounts, "resolve_account", return_value=self.base_url):
            result, status = whatsapp.get_deleted_message("MSG-COMUM", "grupo-teste@g.us")
        self.assertIsNone(result)
        self.assertIn("neither deleted nor edited", status)


class MainGetDeletedMessageToolTest(unittest.TestCase):
    """The MCP tool must pass through the client's content on success and its
    refusal message on failure, same convention TestDownloadRevoked uses for
    download_media in test_download_revoked.py."""

    def test_repassa_conteudo_no_sucesso(self):
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(
            {
                "content": "legenda original",
                "previous_content": None,
                "media_type": "image",
                "revoked_at": "2026-09-24T10:00:00Z",
                "edited_at": None,
            },
            "OK",
        )):
            out = main.get_deleted_message("MSG-APAGADA", "grupo-teste@g.us")
        self.assertTrue(out["success"])
        self.assertEqual(out["content"], "legenda original")
        self.assertEqual(out["revoked_at"], "2026-09-24T10:00:00Z")

    def test_repassa_a_recusa(self):
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(
            None, "message MSG-COMUM was neither deleted nor edited",
        )):
            out = main.get_deleted_message("MSG-COMUM", "grupo-teste@g.us")
        self.assertFalse(out["success"])
        self.assertIn("neither deleted nor edited", out["message"])

    def test_download_inclui_file_path(self):
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(
            {
                "content": "legenda",
                "previous_content": None,
                "media_type": "image",
                "revoked_at": "2026-09-24T10:00:00Z",
                "edited_at": None,
                "downloaded": True,
                "filename": "foto.jpg",
                "path": "/abs/path/foto.jpg",
            },
            "OK",
        )):
            out = main.get_deleted_message("MSG-APAGADA", "grupo-teste@g.us", download=True)
        self.assertTrue(out["success"])
        self.assertTrue(out["downloaded"])
        self.assertEqual(out["file_path"], "/abs/path/foto.jpg")


if __name__ == "__main__":
    unittest.main()
