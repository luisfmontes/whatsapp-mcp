"""Tests for targeted audio transcription (--message-id / --chat-jid).

Run: python3 -m unittest test_transcribe_target -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from transcribe import pending_audios, main, SENTINEL_EMPTY, SENTINEL_UNAVAILABLE


def _make_test_db():
    """Create a temporary SQLite database with messages schema.

    Returns (db_path, conn).
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE messages (
            id TEXT,
            chat_jid TEXT,
            media_type TEXT,
            content TEXT,
            file_sha256 BLOB,
            timestamp TEXT,
            revoked_at TEXT,
            PRIMARY KEY (id, chat_jid)
        )
    """)
    conn.commit()
    return path, conn


class PendingAudiosTest(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn = _make_test_db()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _insert_audio(self, msg_id, chat_jid, content="", sha="abcd1234", timestamp="2026-10-01 12:00:00"):
        """Insert an audio message."""
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, file_sha256, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', ?, ?, ?, NULL)",
            (msg_id, chat_jid, content, bytes.fromhex(sha), timestamp)
        )
        self.conn.commit()

    def test_no_filter_returns_all_pending_audios(self):
        """Without --message-id, pending_audios returns all, ordered DESC."""
        self._insert_audio("msg1", "123@s.whatsapp.net", timestamp="2026-10-01 10:00:00")
        self._insert_audio("msg2", "456@s.whatsapp.net", timestamp="2026-10-01 11:00:00")
        self._insert_audio("msg3", "123@s.whatsapp.net", timestamp="2026-10-01 12:00:00")

        rows = pending_audios(self.conn)
        self.assertEqual(len(rows), 3)
        # DESC order: newest first
        self.assertEqual(rows[0][0], "msg3")
        self.assertEqual(rows[1][0], "msg2")
        self.assertEqual(rows[2][0], "msg1")

    def test_no_filter_with_limit(self):
        """Without --message-id, --limit still works."""
        for i in range(5):
            self._insert_audio(f"msg{i}", "123@s.whatsapp.net",
                             timestamp=f"2026-10-01 {10+i:02d}:00:00")

        rows = pending_audios(self.conn, limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "msg4")  # newest
        self.assertEqual(rows[1][0], "msg3")

    def test_message_id_and_chat_jid_filter(self):
        """With --message-id and --chat-jid, returns only that message."""
        self._insert_audio("msg1", "123@s.whatsapp.net")
        self._insert_audio("msg2", "456@s.whatsapp.net")
        self._insert_audio("msg3", "789@s.whatsapp.net")

        rows = pending_audios(self.conn, message_id="msg2", chat_jid="456@s.whatsapp.net")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "msg2")
        self.assertEqual(rows[0][1], "456@s.whatsapp.net")

    def test_same_id_different_chat_does_not_match(self):
        """Same message ID in different chat should not match."""
        self._insert_audio("msg1", "123@s.whatsapp.net")
        self._insert_audio("msg1", "456@s.whatsapp.net")

        rows = pending_audios(self.conn, message_id="msg1", chat_jid="123@s.whatsapp.net")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "123@s.whatsapp.net")

    def test_filter_ignores_transcribed_messages(self):
        """Filter still respects empty/NULL content check."""
        # Insert one with content (already transcribed)
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, file_sha256, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', ?, ?, ?, NULL)",
            ("msg1", "123@s.whatsapp.net", "Already transcribed", bytes.fromhex("abcd1234"), "2026-10-01 12:00:00")
        )
        self.conn.commit()

        rows = pending_audios(self.conn, message_id="msg1", chat_jid="123@s.whatsapp.net")
        self.assertEqual(len(rows), 0)

    def test_filter_ignores_revoked_messages(self):
        """Filter still respects revoked_at check."""
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, file_sha256, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', ?, ?, ?, ?)",
            ("msg1", "123@s.whatsapp.net", "", bytes.fromhex("abcd1234"), "2026-10-01 12:00:00", "2026-10-01 13:00:00")
        )
        self.conn.commit()

        rows = pending_audios(self.conn, message_id="msg1", chat_jid="123@s.whatsapp.net")
        self.assertEqual(len(rows), 0)


class MainArgumentsTest(unittest.TestCase):
    def test_message_id_without_chat_jid_fails(self):
        """--message-id without --chat-jid must exit 2."""
        with self.assertRaises(SystemExit) as cm:
            with mock.patch('sys.argv', ['transcribe.py', '--message-id', 'msg1']):
                main()
        self.assertEqual(cm.exception.code, 2)

    def test_chat_jid_without_message_id_fails(self):
        """--chat-jid without --message-id must exit 2."""
        with self.assertRaises(SystemExit) as cm:
            with mock.patch('sys.argv', ['transcribe.py', '--chat-jid', '123@s.whatsapp.net']):
                main()
        self.assertEqual(cm.exception.code, 2)

    def test_both_provided_succeeds_parse(self):
        """--message-id and --chat-jid together should parse (and then fail on DB)."""
        db_path, conn = _make_test_db()
        try:
            with mock.patch('sys.argv', ['transcribe.py', '--message-id', 'msg1', '--chat-jid', '123@s.whatsapp.net']):
                with mock.patch('transcribe.DB_PATH', db_path):
                    with mock.patch('transcribe.engine_ready', return_value=(False, "test")):
                        # Should not raise SystemExit(2), just exit early due to engine not ready
                        main()
        finally:
            conn.close()
            os.unlink(db_path)


class MainTargetedTranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.db_path, self.conn = _make_test_db()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _insert_audio(self, msg_id, chat_jid, content="", sha="abcd1234", timestamp="2026-10-01 12:00:00"):
        """Insert an audio message."""
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, media_type, content, file_sha256, timestamp, revoked_at) "
            "VALUES (?, ?, 'audio', ?, ?, ?, NULL)",
            (msg_id, chat_jid, content, bytes.fromhex(sha), timestamp)
        )
        self.conn.commit()

    def test_target_nonexistent_message_logs_and_exits(self):
        """Targeting a non-existent message logs it and exits 0."""
        self._insert_audio("msg1", "123@s.whatsapp.net")

        with mock.patch('sys.argv', ['transcribe.py', '--message-id', 'nonexistent', '--chat-jid', '123@s.whatsapp.net']):
            with mock.patch('transcribe.DB_PATH', self.db_path):
                with mock.patch('transcribe.engine_ready', return_value=(True, "local")):
                    with mock.patch('transcribe.log') as mock_log:
                        main()
                        # Should log that the message is not pending
                        calls = [call[0][0] for call in mock_log.call_args_list]
                        self.assertTrue(any("not pending" in msg for msg in calls), calls)

    def test_target_only_one_message_selected(self):
        """When targeting a specific message, pending_audios returns only that one."""
        self._insert_audio("msg1", "123@s.whatsapp.net")
        self._insert_audio("msg2", "456@s.whatsapp.net")
        self._insert_audio("msg3", "789@s.whatsapp.net")

        # Verify that when targeted, only msg2 is selected by pending_audios
        conn = sqlite3.connect(self.db_path)
        rows = pending_audios(conn, message_id="msg2", chat_jid="456@s.whatsapp.net")
        conn.close()

        # Should only get msg2
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "msg2")
        self.assertEqual(rows[0][1], "456@s.whatsapp.net")

        # Verify that without filter, all three are selected
        conn = sqlite3.connect(self.db_path)
        rows_all = pending_audios(conn)
        conn.close()

        self.assertEqual(len(rows_all), 3)


if __name__ == "__main__":
    unittest.main()
