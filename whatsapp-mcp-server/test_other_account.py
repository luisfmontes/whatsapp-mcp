"""Tests for finding messages in alternate accounts (fix for download_media/get_deleted_message).

When a user calls download_media or get_deleted_message on a message from account X
but passes account Y (or uses the default account), the bridge returns a failure
(HTTP 500 "failed to find message" or HTTP 404 "neither deleted nor edited").
These tools should detect the message exists elsewhere and tell the user which
account to use, rather than suggesting the downloads folder.
"""

import os
import json
import sqlite3
import tempfile
import pytest
from pathlib import Path
from unittest import mock

import accounts
import main


class TestMessageInAccount:
    """Tests for message_in_account(alias, message_id, chat_jid) helper."""

    def test_message_exists(self, tmp_path, monkeypatch):
        """When a message exists in the database, returns True."""
        db_path = tmp_path / "store" / "messages.db"
        db_path.parent.mkdir(parents=True)
        
        # Create database with a message
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('MSG-123', 'test-jid-1@g.us')")
        
        # Create accounts.json
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(tmp_path), "port": 3005}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        assert accounts.message_in_account("pessoal", "MSG-123", "test-jid-1@g.us") is True

    def test_message_not_found(self, tmp_path, monkeypatch):
        """When a message does not exist, returns False."""
        db_path = tmp_path / "store" / "messages.db"
        db_path.parent.mkdir(parents=True)
        
        # Create database without the message
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('OTHER-MSG', 'test-jid-2@g.us')")
        
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(tmp_path), "port": 3005}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        assert accounts.message_in_account("pessoal", "MSG-123", "test-jid-1@g.us") is False

    def test_database_file_missing(self, tmp_path, monkeypatch):
        """When the database file doesn't exist, returns False without error."""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(tmp_path), "port": 3005}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        # No database file created
        assert accounts.message_in_account("pessoal", "MSG-123", "test-jid-1@g.us") is False

    def test_unknown_account(self, tmp_path, monkeypatch):
        """When the account doesn't exist, returns False without error."""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(tmp_path), "port": 3005}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        # Unknown account raises ValueError, which is caught
        assert accounts.message_in_account("unknown", "MSG-123", "test-jid-1@g.us") is False


class TestFindMessageAccounts:
    """Tests for find_message_accounts(message_id, chat_jid, exclude) helper."""

    def test_helper_finds_message_in_other_account(self, tmp_path, monkeypatch):
        """When a message exists in another account, returns that account."""
        # Create two account directories
        pessoal_dir = tmp_path / "pessoal"
        trabalho_dir = tmp_path / "trabalho"
        pessoal_dir.mkdir()
        trabalho_dir.mkdir()
        
        # Create pessoal database (default, will be excluded)
        pessoal_db = pessoal_dir / "store" / "messages.db"
        pessoal_db.parent.mkdir()
        with sqlite3.connect(str(pessoal_db)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('OTHER-MSG', 'test-jid-2@g.us')")
        
        # Create trabalho database (has our message)
        trabalho_db = trabalho_dir / "store" / "messages.db"
        trabalho_db.parent.mkdir()
        with sqlite3.connect(str(trabalho_db)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('MSG-123', 'test-jid-1@g.us')")
        
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(pessoal_dir), "port": 3005},
                "trabalho": {"dir": str(trabalho_dir), "port": 3006}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        result = accounts.find_message_accounts("MSG-123", "test-jid-1@g.us")
        assert result == ["trabalho"]

    def test_helper_ignora_a_conta_chamada(self, tmp_path, monkeypatch):
        """Mutation target: message present in called account and another -> only the other returned."""
        pessoal_dir = tmp_path / "pessoal"
        trabalho_dir = tmp_path / "trabalho"
        pessoal_dir.mkdir()
        trabalho_dir.mkdir()
        
        # Create pessoal database (has our message)
        pessoal_db = pessoal_dir / "store" / "messages.db"
        pessoal_db.parent.mkdir()
        with sqlite3.connect(str(pessoal_db)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('MSG-123', 'test-jid-1@g.us')")
        
        # Create trabalho database (also has our message)
        trabalho_db = trabalho_dir / "store" / "messages.db"
        trabalho_db.parent.mkdir()
        with sqlite3.connect(str(trabalho_db)) as conn:
            conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
            conn.execute("INSERT INTO messages VALUES ('MSG-123', 'test-jid-1@g.us')")
        
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(pessoal_dir), "port": 3005},
                "trabalho": {"dir": str(trabalho_dir), "port": 3006}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        # When excluding pessoal (default), only trabalho comes back
        result = accounts.find_message_accounts("MSG-123", "test-jid-1@g.us", exclude="pessoal")
        assert result == ["trabalho"]

    def test_helper_no_message_anywhere(self, tmp_path, monkeypatch):
        """When message doesn't exist in any account, returns empty list."""
        pessoal_dir = tmp_path / "pessoal"
        trabalho_dir = tmp_path / "trabalho"
        pessoal_dir.mkdir()
        trabalho_dir.mkdir()
        
        # Create both databases without our message
        for d in [pessoal_dir, trabalho_dir]:
            db = d / "store" / "messages.db"
            db.parent.mkdir()
            with sqlite3.connect(str(db)) as conn:
                conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
        
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(pessoal_dir), "port": 3005},
                "trabalho": {"dir": str(trabalho_dir), "port": 3006}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        result = accounts.find_message_accounts("MSG-123", "test-jid-1@g.us")
        assert result == []

    def test_helper_multiple_accounts_have_message(self, tmp_path, monkeypatch):
        """When multiple accounts have the message, all are returned sorted."""
        pessoal_dir = tmp_path / "pessoal"
        trabalho_dir = tmp_path / "trabalho"
        extra_dir = tmp_path / "extra"
        
        for d in [pessoal_dir, trabalho_dir, extra_dir]:
            d.mkdir()
            db = d / "store" / "messages.db"
            db.parent.mkdir()
            with sqlite3.connect(str(db)) as conn:
                conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT)")
                conn.execute("INSERT INTO messages VALUES ('MSG-123', 'test-jid-1@g.us')")
        
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "pessoal",
            "accounts": {
                "pessoal": {"dir": str(pessoal_dir), "port": 3005},
                "trabalho": {"dir": str(trabalho_dir), "port": 3006},
                "extra": {"dir": str(extra_dir), "port": 3007}
            }
        }))
        
        monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
        
        # Excludes pessoal (default), both trabalho and extra found (sorted)
        result = accounts.find_message_accounts("MSG-123", "test-jid-1@g.us")
        assert result == ["extra", "trabalho"]


class TestDownloadMediaIndicatesOtherAccount:
    """Tests for download_media tool indicating alternate accounts."""

    def test_download_indica_a_outra_conta(self):
        """When message not found in default account but exists elsewhere, hint names the account."""
        # Mock whatsapp_download_media to return "failed to find message" error
        bridge_response = 'HTTP 500 - {"success":false,"message":"failed to find message: sql: no rows in result set"}'
        
        # Mock the helper to return an alternate account
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, bridge_response)):
            with mock.patch.object(accounts, "find_message_accounts", return_value=["trabalho"]):
                out = main.download_media("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "trabalho" in out["hint"]
        assert 'account="trabalho"' in out["hint"]
        assert "downloads folder" not in out["hint"]

    def test_download_nenhuma_conta_tem_a_mensagem(self):
        """When message not found in any account, hint uses D3 text without downloads folder."""
        bridge_response = 'HTTP 500 - {"success":false,"message":"failed to find message: sql: no rows in result set"}'
        
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, bridge_response)):
            with mock.patch.object(accounts, "find_message_accounts", return_value=[]):
                out = main.download_media("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "not found" in out["hint"].lower()
        assert "downloads folder" not in out["hint"]
        assert "any account" in out["hint"].lower()

    def test_download_nao_abre_banco_no_caminho_feliz(self):
        """Happy path (success or other error) never calls the helper."""
        with mock.patch.object(main, "whatsapp_download_media", return_value=("/path/to/file", "OK")):
            with mock.patch.object(accounts, "find_message_accounts") as mock_helper:
                out = main.download_media("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is True
        assert mock_helper.call_count == 0

    def test_download_outra_falha_mantem_a_dica_da_pasta(self):
        """Non-"failed to find message" errors still get the downloads folder hint."""
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, "HTTP 500 - timeout")):
            with mock.patch.object(accounts, "find_message_accounts") as mock_helper:
                out = main.download_media("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "downloads folder" in out["hint"]
        assert mock_helper.call_count == 0

    def test_download_sem_deleted_by_sender_dica(self):
        """When deleted by sender, special hint, no account search."""
        revoke_msg = 'HTTP 500 - {"success":false,"message":"Failed to download media: message was deleted by the sender"}'
        
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, revoke_msg)):
            with mock.patch.object(accounts, "find_message_accounts") as mock_helper:
                out = main.download_media("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "deleted this message for everyone" in out["hint"]
        assert "downloads folder" not in out["hint"]
        assert mock_helper.call_count == 0


class TestGetDeletedMessageIndicatesOtherAccount:
    """Tests for get_deleted_message tool indicating alternate accounts."""

    def test_deleted_indica_a_outra_conta(self):
        """When 'neither deleted nor edited' in default, but exists elsewhere, hint names it."""
        bridge_response = 'HTTP 404 - {"error":"message MSG-123 was neither deleted nor edited"}'
        
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(None, bridge_response)):
            with mock.patch.object(accounts, "find_message_accounts", return_value=["trabalho"]):
                with mock.patch.object(accounts, "message_in_account", return_value=False):
                    out = main.get_deleted_message("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "hint" in out
        assert "trabalho" in out["hint"]
        assert 'account="trabalho"' in out["hint"]

    def test_deleted_mensagem_esta_na_conta_chamada_sem_hint(self):
        """When message is in the called account (not deleted/edited), no hint, return as normal."""
        bridge_response = 'HTTP 404 - {"error":"message MSG-123 was neither deleted nor edited"}'
        
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(None, bridge_response)):
            with mock.patch.object(accounts, "message_in_account", return_value=True):
                with mock.patch.object(accounts, "find_message_accounts", return_value=[]):
                    out = main.get_deleted_message("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "hint" not in out

    def test_deleted_nenhuma_conta_tem_a_mensagem(self):
        """When neither deleted nor edited in all accounts, no hint."""
        bridge_response = 'HTTP 404 - {"error":"message MSG-123 was neither deleted nor edited"}'
        
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(None, bridge_response)):
            with mock.patch.object(accounts, "find_message_accounts", return_value=[]):
                with mock.patch.object(accounts, "message_in_account", return_value=False):
                    out = main.get_deleted_message("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert "hint" not in out

    def test_deleted_nao_abre_banco_no_caminho_feliz(self):
        """Happy path (message found as deleted/edited) never calls the helper."""
        result_dict = {"content": "deleted content", "revoked_at": "2026-01-01"}
        
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(result_dict, "OK")):
            with mock.patch.object(accounts, "find_message_accounts") as mock_helper:
                with mock.patch.object(accounts, "message_in_account") as mock_msg_check:
                    out = main.get_deleted_message("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is True
        assert mock_helper.call_count == 0
        assert mock_msg_check.call_count == 0

    def test_deleted_outra_falha_nao_search_accounts(self):
        """Non-'neither deleted' errors don't search for the message elsewhere."""
        auth_error = 'HTTP 401 - {"error":"invalid auth"}'
        
        with mock.patch.object(main, "whatsapp_get_deleted_message", return_value=(None, auth_error)):
            with mock.patch.object(accounts, "find_message_accounts") as mock_helper:
                out = main.get_deleted_message("MSG-123", "test-jid-1@g.us")
        
        assert out["success"] is False
        assert mock_helper.call_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
