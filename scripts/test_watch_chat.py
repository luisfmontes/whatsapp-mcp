"""Tests for scripts/watch_chat.py: burst grouping, no-exit-after-emit,
skipping outgoing messages, state survival across a re-arm, and the idle
timeout. Drives the real script as a subprocess against a temporary sqlite
file (the script opens messages.db read-only through a file: URI, which
:memory: cannot satisfy), with short --interval/--quiet/--idle so the suite
runs in a couple of seconds.

Run: python -m unittest scripts/test_watch_chat.py -v
"""
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "watch_chat.py"

# Mirrors the real messages table (whatsapp-bridge/main.go CREATE TABLE,
# also used verbatim by test_deleted_message.py in whatsapp-mcp-server/), so
# the query in watch_chat.py runs against the actual column set instead of a
# stand-in that only happens to have the columns its SELECT touches.
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

# No digits before the domain: check-personal-data.py's @s.whatsapp.net /
# @lid patterns require 8-25 leading digits, and a synthetic id like this
# would still have to be baselined even though it is not a real number.
CHAT_JID = "contato-teste@s.whatsapp.net"


class WatchChatTestCase(unittest.TestCase):
    """Common fixture: a temp dir with messages.db + state.json, a writer
    connection to insert fixture rows, and a helper to drive watch_chat.py
    as a subprocess and read its stdout line by line off a background
    thread (Windows pipes are not select()-able, so a thread + Queue is the
    portable way to do a non-blocking readline)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Order matters: the subprocess cleanup must run BEFORE the tempdir
        # cleanup, or a still-open child holding messages.db turns the
        # directory removal into a PermissionError on Windows. addCleanup
        # runs LIFO, so register the tempdir first and the process stop
        # second.
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._stop_proc)

        self.db_path = str(Path(self._tmp.name) / "messages.db")
        self.state_path = str(Path(self._tmp.name) / "state.json")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(MESSAGES_DDL)
        self.conn.commit()
        self.addCleanup(self.conn.close)

        self._next_id = 0
        self.proc = None
        self._lines = None

    def _stop_proc(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()

    def insert(self, content, is_from_me=0, media_type=None, ts=None):
        self._next_id += 1
        msg_id = f"MSG{self._next_id}"
        ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, content, timestamp, is_from_me, media_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, CHAT_JID, content, ts, is_from_me, media_type),
        )
        self.conn.commit()
        return msg_id

    def start_watch(self, interval=0.05, quiet=0.4, idle=1.5, extra_args=None):
        self._stop_proc()
        # On a first run (no state file yet) the process seeds itself --
        # "from-now" reads whatever already exists in messages.db at that
        # moment and marks it seen. Python's own interpreter startup can
        # take longer than the gap between this call returning and the
        # test's next insert(), so without waiting for that one-time seed
        # to land, a message inserted "right after start_watch()" can race
        # ahead of it and get marked seen before it ever counts as new --
        # a test-harness race, not a bug in the watch itself. On a re-arm
        # the state file already exists and this wait is a no-op.
        first_run = not Path(self.state_path).exists()

        cmd = [
            sys.executable, str(SCRIPT),
            "--db", self.db_path,
            "--chat", CHAT_JID,
            "--state", self.state_path,
            "--interval", str(interval),
            "--quiet", str(quiet),
            "--idle", str(idle),
        ]
        if extra_args:
            cmd += extra_args
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        line_queue = queue.Queue()

        def _pump():
            for line in self.proc.stdout:
                line_queue.put(line.rstrip("\n"))

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        self._lines = line_queue

        if first_run:
            self._wait_for_file(self.state_path, timeout=5)
        return self.proc

    @staticmethod
    def _wait_for_file(path, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if Path(path).exists():
                return
            time.sleep(0.01)
        raise AssertionError(f"{path} was never created within {timeout}s (seeding never ran?)")

    def next_line(self, timeout=5):
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def assert_no_line(self, timeout):
        line = self.next_line(timeout=timeout)
        self.assertIsNone(line, f"expected no line, got: {line!r}")


class TestBurstDetection(WatchChatTestCase):
    def test_a_rajada_de_tres_mensagens_gera_uma_linha(self):
        self.start_watch()
        # Spaced further apart than --interval (0.05s) but well inside
        # --quiet (0.4s), so each lands in its own poll tick and a correct
        # implementation still folds all three into a single burst line.
        self.insert("oi")
        time.sleep(0.12)
        self.insert("tudo bem?")
        time.sleep(0.12)
        self.insert("me responde")

        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertIn("NEW MESSAGES (3)", line)
        # And nothing else follows for this single burst.
        self.assert_no_line(timeout=1.0)

    def test_b_segunda_rajada_gera_segunda_linha_sem_sair(self):
        self.start_watch()
        self.insert("primeira rajada")
        first = self.next_line(timeout=5)
        self.assertIsNotNone(first)
        self.assertIn("NEW MESSAGES (1)", first)
        self.assertIsNone(self.proc.poll(), "the watch must not exit after emitting a line")

        self.insert("segunda rajada")
        second = self.next_line(timeout=5)
        self.assertIsNotNone(second)
        self.assertIn("NEW MESSAGES (1)", second)
        self.assertIsNone(self.proc.poll())

    def test_c_mensagem_minha_nao_gera_evento(self):
        self.start_watch()
        self.insert("mensagem que eu mandei", is_from_me=1)
        self.assert_no_line(timeout=1.0)

        # Positive control: the watcher is alive and still reacts to a real
        # incoming message right after.
        self.insert("mensagem recebida")
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertIn("NEW MESSAGES (1)", line)

    def test_d_rearmar_com_mesmo_estado_nao_repete_nem_perde(self):
        self.start_watch(extra_args=["--from-now"])
        self.insert("antes de derrubar")
        first = self.next_line(timeout=5)
        self.assertIn("NEW MESSAGES (1)", first)
        self.assertIn("antes de derrubar", first)

        # Take the watch down (state was already saved right after the
        # emit above) and let a message arrive while nothing is watching.
        self._stop_proc()
        self.insert("chegou com o watcher fora do ar")

        # Re-arm with the SAME --state file.
        self.start_watch(extra_args=["--from-now"])
        second = self.next_line(timeout=5)
        self.assertIsNotNone(second)
        self.assertIn("NEW MESSAGES (1)", second)
        self.assertIn("chegou com o watcher fora do ar", second)
        self.assertNotIn("antes de derrubar", second)

    def test_e_timeout_ocioso_gera_fim_e_sai_com_0(self):
        self.start_watch(idle=0.3)
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("END:"), f"expected an END line, got: {line!r}")
        self.proc.wait(timeout=5)
        self.assertEqual(self.proc.returncode, 0)

    def test_f_include_unanswered_pega_mensagem_pendente_no_primeiro_run(self):
        # This is the prototype bug this task fixes: with an empty state and
        # --from-now (the default), a message already sitting in the chat
        # before the watch starts is marked "seen" and never fires.
        # --include-unanswered exists so it does fire.
        self.insert("mensagem que já estava esperando resposta")
        self.start_watch(extra_args=["--include-unanswered"])
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertIn("NEW MESSAGES (1)", line)
        self.assertIn("mensagem que já estava esperando resposta", line)

    def test_g_from_now_ignora_mensagem_pendente_no_primeiro_run(self):
        # Same setup as (f), default seeding: the pending message must NOT
        # fire, confirming --from-now's documented behavior is preserved.
        self.insert("mensagem que já estava esperando resposta")
        self.start_watch(extra_args=["--from-now"])
        self.assert_no_line(timeout=1.0)


def _status_body(healthy):
    """The bridge's real /api/status shape (captured 2026-09-25 from a live
    bridge), with the account JID swapped for a synthetic, digit-free one."""
    return {
        "success": True, "healthy": healthy, "connected": healthy, "logged_in": True,
        "jid": "conta-teste@s.whatsapp.net",
        "last_successful_connect": "2026-09-24T19:10:33-03:00",
        "auto_reconnect_errors": 0,
        "last_event_at": "2026-09-25T06:46:22-03:00",
        "uptime_seconds": 47557,
        "watchdog": {"interval_seconds": 60, "reconnects": 0, "last_tick_at": "2026-09-25T06:46:18-03:00"},
    }


class FakeBridge:
    """A stand-in for the bridge's /api/status on a free local port. `healthy`
    flips what it answers; stop() closes the socket so the next check is a
    refused connection; `hits` counts requests, to prove a watch without
    --status-url never calls it."""

    def __init__(self):
        import http.server
        import json as _json
        bridge = self
        self.healthy = True
        self.truncate = False
        self.hits = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                bridge.hits += 1
                body = _json.dumps(_status_body(bridge.healthy)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                # truncate: promise the full length, send half, close -- a
                # bridge dying mid-response (http.client.IncompleteRead).
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[: len(body) // 2] if bridge.truncate else body)
                if bridge.truncate:
                    self.close_connection = True

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/status"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self._stopped = False

    def stop(self):
        if not self._stopped:
            self._stopped = True
            self.server.shutdown()
            self.server.server_close()


class TestBridgeStatus(WatchChatTestCase):
    """--status-url: a dead bridge leaves messages.db readable, so without
    this the watch cannot tell "nobody answered" from "the bridge is down"."""

    def setUp(self):
        super().setUp()
        self.bridge = FakeBridge()
        self.addCleanup(self.bridge.stop)

    def start_status_watch(self):
        # idle long enough that no END line interferes with the assertions
        return self.start_watch(idle=30, extra_args=[
            "--status-url", self.bridge.url, "--status-interval", "0.1",
        ])

    def test_h_bridge_caida_gera_uma_linha_so(self):
        self.start_status_watch()
        self.assert_no_line(timeout=0.5)  # healthy at start: nothing to say
        self.bridge.healthy = False
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("BRIDGE: desconectada (healthy:false)"), line)
        self.assertIn("a espera está cega", line)
        # Many more checks happen while it stays down; none of them repeats the line.
        self.assert_no_line(timeout=1.0)

    def test_i_bridge_volta_gera_conectada(self):
        self.start_status_watch()
        self.bridge.healthy = False
        self.assertIn("BRIDGE: desconectada", self.next_line(timeout=5) or "")
        self.bridge.healthy = True
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("BRIDGE: conectada"), line)

    def test_j_porta_recusada_gera_desconectada(self):
        self.start_status_watch()
        self.assert_no_line(timeout=0.5)
        self.bridge.stop()
        line = self.next_line(timeout=10)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("BRIDGE: desconectada (sem resposta"), line)
        # And the watch itself stays up: a dead bridge is news, not a crash.
        self.assertIsNone(self.proc.poll())

    def test_l_resposta_cortada_gera_desconectada_sem_derrubar(self):
        self.start_status_watch()
        self.assert_no_line(timeout=0.5)
        self.bridge.truncate = True
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("BRIDGE: desconectada (resposta cortada)"), line)
        self.assertIsNone(self.proc.poll(), "a cut-off response must not kill the watch")

    def test_k_sem_status_url_nao_chama_a_bridge(self):
        self.start_watch(idle=30)
        self.bridge.healthy = False
        self.assert_no_line(timeout=1.0)
        self.assertEqual(self.bridge.hits, 0)
        # Positive control: the plain watch still reports messages.
        self.insert("mensagem recebida")
        line = self.next_line(timeout=5)
        self.assertIsNotNone(line)
        self.assertIn("NEW MESSAGES (1)", line)


if __name__ == "__main__":
    unittest.main()
