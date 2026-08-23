#!/usr/bin/env python3
"""Test account routing for multi-account WhatsApp MCP.

Tests that whatsapp.py and main.py correctly route account parameters
to accounts.resolve_account() and use the resolved base_url for all API calls.
"""

import pytest
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import accounts
import whatsapp
import main


class TestAccountsResolution:
    """Test accounts.resolve_account() resolution logic."""

    def test_resolve_account_with_no_config_returns_env_or_default(self):
        """When no accounts.json exists, falls back to env or default."""
        with patch.dict(os.environ, {}, clear=True):
            url = accounts.resolve_account(None)
            assert url == "http://localhost:8080/api"

    def test_resolve_account_with_env_base_url(self):
        """WHATSAPP_API_BASE_URL env var is respected."""
        with patch.dict(os.environ, {"WHATSAPP_API_BASE_URL": "http://custom:9090/api"}):
            url = accounts.resolve_account(None)
            assert url == "http://custom:9090/api"


class TestWhatsappAccountRouting:
    """Test that whatsapp.py functions route account parameter correctly."""

    @patch("whatsapp.accounts.resolve_account")
    @patch("whatsapp._api_post")
    def test_search_contacts_resolves_account(self, mock_api_post, mock_resolve):
        """search_contacts resolves account and passes base_url to _api_post."""
        mock_resolve.return_value = "http://localhost:3005/api"
        mock_api_post.return_value = {"contacts": []}

        whatsapp.search_contacts("test query", account="pessoal")

        mock_resolve.assert_called_once_with("pessoal")
        mock_api_post.assert_called_once()
        call_args = mock_api_post.call_args
        assert call_args[0][2] == "http://localhost:3005/api"

    @patch("whatsapp.accounts.resolve_account")
    @patch("whatsapp._api_post")
    def test_list_messages_resolves_account(self, mock_api_post, mock_resolve):
        """list_messages resolves account and passes base_url to _api_post."""
        mock_resolve.return_value = "http://localhost:3006/api"
        mock_api_post.return_value = {"messages": []}

        whatsapp.list_messages(account="trabalho")

        mock_resolve.assert_called_once_with("trabalho")
        mock_api_post.assert_called_once()

    @patch("whatsapp.accounts.resolve_account")
    @patch("whatsapp._api_request")
    def test_leave_group_resolves_account(self, mock_api_request, mock_resolve):
        """leave_group resolves account and passes base_url to _api_request."""
        mock_resolve.return_value = "http://localhost:3006/api"
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True, "message": "Left group"}
        mock_api_request.return_value = mock_response

        whatsapp.leave_group("123@g.us", account="trabalho")

        mock_resolve.assert_called_once_with("trabalho")
        mock_api_request.assert_called_once()


class TestMainToolsAccountRouting:
    """Test that main.py @mcp.tool() functions route account parameter correctly."""

    @patch("main.whatsapp_search_contacts")
    def test_search_contacts_tool_passes_account(self, mock_whatsapp_fn):
        """search_contacts tool passes account parameter."""
        mock_whatsapp_fn.return_value = []
        main.search_contacts("test", account="pessoal")
        mock_whatsapp_fn.assert_called_once_with("test", account="pessoal")

    @patch("main.whatsapp_send_message")
    def test_send_message_tool_passes_account(self, mock_whatsapp_fn):
        """send_message tool passes account parameter."""
        mock_whatsapp_fn.return_value = (True, "Sent")
        main.send_message("123456789", "Hello", account="trabalho")
        mock_whatsapp_fn.assert_called_once_with("123456789", "Hello", account="trabalho")

    @patch("main.whatsapp_get_bridge_status")
    def test_get_bridge_status_tool_passes_account(self, mock_whatsapp_fn):
        """get_bridge_status tool passes account parameter."""
        mock_whatsapp_fn.return_value = (True, "", {})
        main.get_bridge_status(account="pessoal")
        mock_whatsapp_fn.assert_called_once_with(account="pessoal")


class TestAccountParameterConsistency:
    """Test that all public functions have account parameter."""

    def test_all_public_functions_have_account_param(self):
        """All public whatsapp.py functions accept account parameter."""
        public_functions = [
            "search_contacts", "list_messages", "list_chats", "get_chat",
            "get_direct_chat_by_contact", "get_contact_chats", "get_last_interaction",
            "get_message_context", "send_message", "send_file", "send_audio_message",
            "download_media", "create_group", "leave_group", "mark_chat_read",
            "mark_chat_unread", "get_group_info", "archive_chat", "resolve_contact",
            "react_to_message", "edit_message", "delete_message",
            "update_group_participants", "send_chat_presence", "check_whatsapp",
            "get_group_invite_link", "get_group_invite_info", "join_group_with_link",
            "update_group_settings", "set_group_photo", "get_user_info",
            "get_profile_picture", "create_poll", "vote_in_poll", "get_poll_results",
            "get_bridge_status",
        ]

        for func_name in public_functions:
            func = getattr(whatsapp, func_name)
            sig = str(func.__code__.co_varnames)
            assert "account" in sig, f"{func_name} missing 'account' parameter"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
