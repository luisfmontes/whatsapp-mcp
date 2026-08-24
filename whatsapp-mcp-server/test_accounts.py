"""Tests for account management and multi-account resolution."""

import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest import mock

import accounts


class TestAccountsConfigured:
    """Tests for accounts_configured() function."""

    def test_no_accounts_file(self, monkeypatch):
        """When no accounts.json exists, accounts_configured() returns False."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        # Mock home to ensure ~/.whatsapp-mcp/accounts.json doesn't exist
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)  # Windows
            assert accounts.accounts_configured() is False

    def test_with_accounts_file(self, monkeypatch):
        """When accounts.json exists, accounts_configured() returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.accounts_configured() is True


class TestResolveAccountWithoutConfigFile:
    """Tests for resolve_account() when no accounts.json exists."""

    def test_resolve_default_from_api_base_url(self, monkeypatch):
        """Without accounts.json, resolve_account(None) uses WHATSAPP_API_BASE_URL."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        monkeypatch.delenv("WHATSAPP_BRIDGE_PORT", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            monkeypatch.setenv("WHATSAPP_API_BASE_URL", "http://example.com:9000/api")
            assert accounts.resolve_account(None) == "http://example.com:9000/api"

    def test_resolve_default_from_bridge_port(self, monkeypatch):
        """Without accounts.json, resolve_account(None) builds URL from WHATSAPP_BRIDGE_PORT."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        monkeypatch.delenv("WHATSAPP_API_BASE_URL", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            monkeypatch.setenv("WHATSAPP_BRIDGE_PORT", "3005")
            assert accounts.resolve_account(None) == "http://localhost:3005/api"

    def test_resolve_default_fallback(self, monkeypatch):
        """Without accounts.json or env vars, resolve_account(None) uses default port 8080."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        monkeypatch.delenv("WHATSAPP_API_BASE_URL", raising=False)
        monkeypatch.delenv("WHATSAPP_BRIDGE_PORT", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            assert accounts.resolve_account(None) == "http://localhost:8080/api"

    def test_resolve_named_account_fails(self, monkeypatch):
        """Without accounts.json, resolve_account(alias) raises error with empty list."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            with pytest.raises(ValueError) as exc:
                accounts.resolve_account("trabalho")
            assert "trabalho" in str(exc.value)
            assert "known: []" in str(exc.value)

    def test_resolve_default_api_base_url_wins_over_bridge_port(self, monkeypatch):
        """Without accounts.json, when BOTH WHATSAPP_API_BASE_URL and
        WHATSAPP_BRIDGE_PORT are set (the real state on a machine that also
        sets a bridge port for other tooling), WHATSAPP_API_BASE_URL must win.

        The three existing precedence tests each unset the other variable, so
        none of them exercises the actual precedence decision — only the two
        fallback branches in isolation. This is the missing combined case,
        and matches the precedence documented in the plan (D9 /
        'mesma precedencia de hoje' in the task 7 brief)."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            monkeypatch.setenv("WHATSAPP_API_BASE_URL", "http://example.com:9000/api")
            monkeypatch.setenv("WHATSAPP_BRIDGE_PORT", "3005")
            assert accounts.resolve_account(None) == "http://example.com:9000/api"


class TestResolveAccountWithConfigFile:
    """Tests for resolve_account() when accounts.json exists."""

    def test_resolve_default_account(self, monkeypatch):
        """With accounts.json, resolve_account(None) returns the default account URL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None},
                        "trabalho": {"dir": f"{tmpdir}/trabalho", "port": 3006, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.resolve_account(None) == "http://localhost:3005/api"

    def test_resolve_named_account(self, monkeypatch):
        """With accounts.json, resolve_account(alias) returns the named account URL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None},
                        "trabalho": {"dir": f"{tmpdir}/trabalho", "port": 3006, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.resolve_account("trabalho") == "http://localhost:3006/api"

    def test_resolve_unknown_account_raises_error(self, monkeypatch):
        """With accounts.json, resolve_account(unknown) raises error listing known accounts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None},
                        "trabalho": {"dir": f"{tmpdir}/trabalho", "port": 3006, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            with pytest.raises(ValueError) as exc:
                accounts.resolve_account("inexistente")
            assert "inexistente" in str(exc.value)
            # Check that known accounts are listed (sorted)
            assert "pessoal" in str(exc.value)
            assert "trabalho" in str(exc.value)


class TestKnownAliases:
    """Tests for known_aliases() function."""

    def test_no_accounts_file(self, monkeypatch):
        """Without accounts.json, known_aliases() returns empty list."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            assert accounts.known_aliases() == []

    def test_with_accounts_file(self, monkeypatch):
        """With accounts.json, known_aliases() returns sorted list of aliases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "trabalho": {"dir": tmpdir + "/trabalho", "port": 3006, "jid": None},
                        "pessoal": {"dir": tmpdir + "/pessoal", "port": 3005, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.known_aliases() == ["pessoal", "trabalho"]


class TestAccountDir:
    """Tests for account_dir() function."""

    def test_get_default_account_dir(self, monkeypatch):
        """account_dir(None) returns directory of the default account."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            default_dir = f"{tmpdir}/pessoal"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": default_dir, "port": 3005, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.account_dir(None) == default_dir

    def test_get_named_account_dir(self, monkeypatch):
        """account_dir(alias) returns directory of the named account."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            trabalho_dir = f"{tmpdir}/trabalho"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None},
                        "trabalho": {"dir": trabalho_dir, "port": 3006, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.account_dir("trabalho") == trabalho_dir


class TestAccountTaskName:
    """Tests for account_task_name() function."""

    def test_get_default_task_name(self, monkeypatch):
        """account_task_name(None) returns task name of the default account."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": tmpdir, "port": 3005, "jid": None}
                    }
                })
            )
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))
            assert accounts.account_task_name(None) == "WhatsAppMCPBridge-pessoal"

    def test_get_named_task_name(self, monkeypatch):
        """account_task_name(alias) returns task name of the named account."""
        assert accounts.account_task_name("trabalho") == "WhatsAppMCPBridge-trabalho"

    def test_task_name_without_config(self, monkeypatch):
        """account_task_name(alias) works without accounts.json when alias is provided."""
        monkeypatch.delenv("WHATSAPP_ACCOUNTS_FILE", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HOME", tmpdir)
            monkeypatch.setenv("USERPROFILE", tmpdir)
            assert accounts.account_task_name("trabalho") == "WhatsAppMCPBridge-trabalho"


class TestIntegrationScenario:
    """Integration tests simulating the full multi-account scenario from the spec."""

    def test_two_accounts_scenario(self, monkeypatch):
        """Test the complete two-account scenario from the spec."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pessoal_dir = tmpdir
            trabalho_dir = f"{tmpdir}/trabalho"

            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_text(
                json.dumps({
                    "default": "pessoal",
                    "accounts": {
                        "pessoal": {"dir": pessoal_dir, "port": 3005, "jid": None},
                        "trabalho": {"dir": trabalho_dir, "port": 3006, "jid": None}
                    }
                }),
                encoding='utf-8'
            )

            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            # Test resolve_account(None) returns default
            assert accounts.resolve_account(None) == "http://localhost:3005/api"

            # Test resolve_account("trabalho") returns the other account
            assert accounts.resolve_account("trabalho") == "http://localhost:3006/api"

            # Test resolve_account("inexistente") raises error with known accounts
            with pytest.raises(ValueError) as exc:
                accounts.resolve_account("inexistente")
            assert "inexistente" in str(exc.value)
            assert "pessoal" in str(exc.value)
            assert "trabalho" in str(exc.value)

            # Test known_aliases
            assert set(accounts.known_aliases()) == {"pessoal", "trabalho"}

            # Test account_dir
            assert accounts.account_dir(None) == pessoal_dir
            assert accounts.account_dir("trabalho") == trabalho_dir

            # Test account_task_name
            assert accounts.account_task_name(None) == "WhatsAppMCPBridge-pessoal"
            assert accounts.account_task_name("trabalho") == "WhatsAppMCPBridge-trabalho"


class TestCorruptedAccountsFile:
    """Tests for handling corrupted or invalid accounts.json files."""

    def test_corrupted_json_raises_error_with_path(self, monkeypatch):
        """When accounts.json exists but is invalid JSON, a clear error is raised with the path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            # Write invalid JSON (not valid UTF-8 JSON structure)
            accounts_file.write_bytes(b'{invalid json content \xff')

            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            # accounts_configured() should raise, not return True or False
            with pytest.raises(ValueError) as exc:
                accounts.accounts_configured()
            error_msg = str(exc.value)
            assert str(accounts_file) in error_msg
            assert "Invalid JSON" in error_msg or "JSON" in error_msg

    def test_corrupted_file_resolve_raises_error_with_path(self, monkeypatch):
        """When trying to resolve an account from corrupted JSON, error cites the file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            # Write invalid JSON
            accounts_file.write_bytes(b'{"default": "pessoal", invalid}')

            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            with pytest.raises(ValueError) as exc:
                accounts.resolve_account(None)
            error_msg = str(exc.value)
            assert str(accounts_file) in error_msg
            assert "Invalid JSON" in error_msg or "JSON" in error_msg

    def test_empty_json_raises_error(self, monkeypatch):
        """When accounts.json is empty or malformed, error is raised."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            # Write empty file
            accounts_file.write_text("")

            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            with pytest.raises(ValueError) as exc:
                accounts.accounts_configured()
            error_msg = str(exc.value)
            assert str(accounts_file) in error_msg

    def test_known_aliases_corrupted_file_raises_error(self, monkeypatch):
        """When trying to get aliases from corrupted JSON, error is raised with path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.json"
            accounts_file.write_bytes(b'not json at all')

            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            with pytest.raises(ValueError) as exc:
                accounts.known_aliases()
            error_msg = str(exc.value)
            assert str(accounts_file) in error_msg


class TestCollidingAccounts:
    """A map that names two accounts on one port, or one directory, is a
    misconfiguration that produces wrong answers instead of errors: the second
    bridge cannot bind, and every tool aimed at either alias talks to whichever
    won the socket. Two accounts sharing a directory is worse -- they share one
    whatsapp.db, so re-pairing one logs the other out. Both fail loudly."""

    def _write(self, tmpdir, accounts_map):
        accounts_file = Path(tmpdir) / "accounts.json"
        accounts_file.write_text(json.dumps(accounts_map), encoding="utf-8")
        return accounts_file

    def test_duplicate_port_raises_naming_both_aliases(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = self._write(tmpdir, {
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"dir": "C:/a", "port": 3005},
                    "trabalho": {"dir": "C:/b", "port": 3005},
                },
            })
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            with pytest.raises(ValueError) as exc:
                accounts.accounts_configured()
            msg = str(exc.value)
            assert "pessoal" in msg and "trabalho" in msg, msg
            assert "3005" in msg, msg
            assert str(accounts_file) in msg, msg

    def test_duplicate_dir_raises_naming_both_aliases(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = self._write(tmpdir, {
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"dir": "C:/Bridge", "port": 3005},
                    "trabalho": {"dir": "C:/bridge/", "port": 3006},
                },
            })
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            with pytest.raises(ValueError) as exc:
                accounts.accounts_configured()
            msg = str(exc.value)
            assert "pessoal" in msg and "trabalho" in msg, msg

    def test_resolve_account_also_refuses_a_colliding_map(self, monkeypatch):
        """The check sits in the loader, so every entry point inherits it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = self._write(tmpdir, {
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"dir": "C:/a", "port": 3005},
                    "trabalho": {"dir": "C:/b", "port": 3005},
                },
            })
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            for call in (lambda: accounts.resolve_account("trabalho"),
                         lambda: accounts.known_aliases(),
                         lambda: accounts.account_dir("pessoal")):
                with pytest.raises(ValueError):
                    call()

    def test_distinct_ports_and_dirs_still_load(self, monkeypatch):
        """The guard must not reject a healthy map."""
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = self._write(tmpdir, {
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"dir": "C:/a", "port": 3005},
                    "trabalho": {"dir": "C:/b", "port": 3006},
                },
            })
            monkeypatch.setenv("WHATSAPP_ACCOUNTS_FILE", str(accounts_file))

            assert accounts.accounts_configured() is True
            assert accounts.resolve_account("trabalho") == "http://localhost:3006/api"
            assert accounts.known_aliases() == ["pessoal", "trabalho"]
