"""Test error handling matrix for bridge offline + missing config scenarios.

Tests that functions handle the intersection of:
  - accounts.json state: missing | 1 account | 2 accounts
  - bridge state: online | offline (dead port)
  - account parameter: None | explicit alias

Reproduces the defect: when no accounts.json and bridge offline, get_bridge_status
levantava ValueError("No accounts configured") em vez de devolver error tuple.
"""

import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

sys.path.insert(0, str(Path(__file__).parent))

import whatsapp
import accounts


class TestErrorMatrix:
    """Matrix: mapa × bridge × conta."""

    @pytest.fixture(autouse=True)
    def cleanup_env(self):
        """Ensure WHATSAPP_ACCOUNTS_FILE is not set before each test."""
        old_val = os.environ.pop('WHATSAPP_ACCOUNTS_FILE', None)
        yield
        if old_val:
            os.environ['WHATSAPP_ACCOUNTS_FILE'] = old_val

    @pytest.mark.parametrize(
        "accounts_state,bridge_online,account_param,expected_type",
        [
            # (state, online?, param, expect: tuple | aggregated | ValueError str)
            # Missing accounts.json (legacy single-account)
            ("missing", True, None, "tuple"),  # bridge online, no account param -> (True, reason, status)
            ("missing", False, None, "tuple"),  # bridge offline, no account param -> (False, error_msg, None)
            ("missing", False, "alias", "ValueError"),  # bridge offline, explicit alias -> error "not found"

            # 1 account configured
            ("single", True, None, "tuple"),  # single account online, no param -> (True, reason, status)
            ("single", False, None, "tuple"),  # single account offline, no param -> (False, task msg, None)
            ("single", True, "pessoal", "tuple"),  # single account online, explicit param -> (True, ...)
            ("single", False, "pessoal", "tuple"),  # single account offline, explicit param -> (False, error, None)

            # 2 accounts configured
            ("multi", True, None, "aggregated"),  # multi account, no param -> aggregated status
            ("multi", False, None, "aggregated"),  # multi account offline, no param -> aggregated status
            ("multi", True, "pessoal", "tuple"),  # multi account, explicit param -> single status
            ("multi", False, "pessoal", "tuple"),  # multi account offline, explicit param -> (False, error, None)
        ],
    )
    def test_get_bridge_status_matrix(
        self, accounts_state, bridge_online, account_param, expected_type, monkeypatch, tmp_path
    ):
        """Test get_bridge_status across error matrix."""

        # Set up accounts state
        if accounts_state == "missing":
            # No accounts.json
            monkeypatch.delenv('WHATSAPP_ACCOUNTS_FILE', raising=False)
            monkeypatch.delenv('HOME', raising=False)
            monkeypatch.delenv('USERPROFILE', raising=False)
            # Point to a path that doesn't exist
            fake_home = tmp_path / "fake_home"
            monkeypatch.setenv('USERPROFILE', str(fake_home))
        elif accounts_state == "single":
            # 1 account in accounts.json
            accounts_file = tmp_path / "accounts.json"
            accounts_file.write_text(json.dumps({
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"port": 3099 if bridge_online else 9999, "dir": str(tmp_path / "pessoal")}
                }
            }))
            monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))
        else:  # multi
            # 2 accounts in accounts.json
            accounts_file = tmp_path / "accounts.json"
            accounts_file.write_text(json.dumps({
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"port": 3099 if bridge_online else 9999, "dir": str(tmp_path / "pessoal")},
                    "trabalho": {"port": 3098 if bridge_online else 9998, "dir": str(tmp_path / "trabalho")}
                }
            }))
            monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

        # Mock _api_request to simulate bridge behavior
        if bridge_online:
            def mock_api_request(method, path, base_url, timeout=60, **kwargs):
                response = MagicMock()
                response.json.return_value = {
                    "healthy": True,
                    "reason": "logged in",
                    "chats": 5
                }
                return response
            monkeypatch.setattr(whatsapp, '_api_request', mock_api_request)
        else:
            import requests
            def mock_api_request(method, path, base_url, timeout=60, **kwargs):
                raise requests.ConnectionError("Connection refused")
            monkeypatch.setattr(whatsapp, '_api_request', mock_api_request)

        # Call get_bridge_status
        if expected_type == "ValueError":
            with pytest.raises(ValueError) as exc_info:
                whatsapp.get_bridge_status(account=account_param)
            # Should mention the account or task
            assert "account" in str(exc_info.value).lower() or "task" in str(exc_info.value).lower()
        elif expected_type == "tuple":
            result = whatsapp.get_bridge_status(account=account_param)
            assert isinstance(result, tuple)
            assert len(result) == 3
            healthy, reason, status = result
            assert isinstance(healthy, bool)
            assert isinstance(reason, str)
            # Check that when bridge is offline, reason mentions the task
            if not bridge_online:
                assert "offline" in reason.lower() or "task" in reason.lower() or "start" in reason.lower()
        elif expected_type == "aggregated":
            healthy, reason, status = whatsapp.get_bridge_status(account=account_param)
            assert isinstance(status, dict)
            assert reason == "aggregated"
            # Status should have keys for each account
            if accounts_state == "multi":
                assert "pessoal" in status
                assert "trabalho" in status

    @pytest.mark.parametrize(
        "accounts_state,bridge_online,account_param",
        [
            # Missing accounts.json
            ("missing", True, None),
            ("missing", False, None),
            ("missing", False, "alias"),

            # 1 account
            ("single", True, None),
            ("single", False, None),
            ("single", True, "pessoal"),
            ("single", False, "pessoal"),

            # 2 accounts
            ("multi", True, None),
            ("multi", False, None),
            ("multi", True, "pessoal"),
            ("multi", False, "pessoal"),
        ],
    )
    def test_list_chats_matrix(
        self, accounts_state, bridge_online, account_param, monkeypatch, tmp_path
    ):
        """Test list_chats across error matrix."""

        # Set up accounts state
        if accounts_state == "missing":
            monkeypatch.delenv('WHATSAPP_ACCOUNTS_FILE', raising=False)
            fake_home = tmp_path / "fake_home"
            monkeypatch.setenv('USERPROFILE', str(fake_home))
        elif accounts_state == "single":
            accounts_file = tmp_path / "accounts.json"
            accounts_file.write_text(json.dumps({
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"port": 3099 if bridge_online else 9999, "dir": str(tmp_path / "pessoal")}
                }
            }))
            monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))
        else:  # multi
            accounts_file = tmp_path / "accounts.json"
            accounts_file.write_text(json.dumps({
                "default": "pessoal",
                "accounts": {
                    "pessoal": {"port": 3099 if bridge_online else 9999, "dir": str(tmp_path / "pessoal")},
                    "trabalho": {"port": 3098 if bridge_online else 9998, "dir": str(tmp_path / "trabalho")}
                }
            }))
            monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

        # Mock _api_post
        if bridge_online:
            def mock_api_post(path, payload, base_url, timeout=60):
                return {"chats": []}
            monkeypatch.setattr(whatsapp, '_api_post', mock_api_post)
        else:
            def mock_api_post(path, payload, base_url, timeout=60):
                return None  # Simulates bridge offline
            monkeypatch.setattr(whatsapp, '_api_post', mock_api_post)

        # Mock _api_request for explicit account check
        import requests
        def mock_api_request(method, path, base_url, timeout=60, **kwargs):
            if bridge_online:
                response = MagicMock()
                response.json.return_value = {"healthy": True}
                return response
            else:
                raise requests.ConnectionError("Connection refused")
        monkeypatch.setattr(whatsapp, '_api_request', mock_api_request)

        # Call list_chats
        if account_param and not bridge_online and accounts_state != "missing":
            # Explicit account that's offline should raise ValueError
            with pytest.raises(ValueError) as exc_info:
                whatsapp.list_chats(account=account_param)
            assert "offline" in str(exc_info.value).lower() or "task" in str(exc_info.value).lower()
        elif account_param and not bridge_online and accounts_state == "missing":
            # Explicit account doesn't exist in missing state
            with pytest.raises(ValueError) as exc_info:
                whatsapp.list_chats(account=account_param)
            assert "not found" in str(exc_info.value).lower()
        else:
            # Should return list (empty or with data)
            result = whatsapp.list_chats(account=account_param)
            assert isinstance(result, list)


class TestAccountTaskName:
    """Test account_task_name robustness."""

    def test_task_name_no_accounts_file_none_alias(self, monkeypatch, tmp_path):
        """When no accounts.json and alias=None, should return default task name."""
        monkeypatch.delenv('WHATSAPP_ACCOUNTS_FILE', raising=False)
        fake_home = tmp_path / "fake_home"
        monkeypatch.setenv('USERPROFILE', str(fake_home))

        # Should NOT raise, should return default
        result = accounts.account_task_name(None)
        assert result == "WhatsAppMCPBridge"

    def test_task_name_no_accounts_file_explicit_alias(self, monkeypatch, tmp_path):
        """When no accounts.json and explicit alias, should return named task."""
        monkeypatch.delenv('WHATSAPP_ACCOUNTS_FILE', raising=False)
        fake_home = tmp_path / "fake_home"
        monkeypatch.setenv('USERPROFILE', str(fake_home))

        # Explicit alias should work even without accounts.json
        result = accounts.account_task_name("pessoal")
        assert result == "WhatsAppMCPBridge-pessoal"

    def test_task_name_with_accounts_file_default(self, monkeypatch, tmp_path):
        """With accounts.json and None alias, should resolve to default."""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "trabalho",
            "accounts": {
                "trabalho": {"port": 3005, "dir": "/some/path"},
                "pessoal": {"port": 3006, "dir": "/other/path"}
            }
        }))
        monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

        result = accounts.account_task_name(None)
        assert result == "WhatsAppMCPBridge-trabalho"

    def test_task_name_with_accounts_file_explicit(self, monkeypatch, tmp_path):
        """With accounts.json and explicit alias, should use that."""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(json.dumps({
            "default": "trabalho",
            "accounts": {
                "trabalho": {"port": 3005, "dir": "/some/path"},
                "pessoal": {"port": 3006, "dir": "/other/path"}
            }
        }))
        monkeypatch.setenv('WHATSAPP_ACCOUNTS_FILE', str(accounts_file))

        result = accounts.account_task_name("pessoal")
        assert result == "WhatsAppMCPBridge-pessoal"
