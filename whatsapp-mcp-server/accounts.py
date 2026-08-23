"""Account management for multi-account WhatsApp bridge.

Resolves account aliases to bridge URLs, manages account configuration,
and supports fallback to legacy single-account environment setup.
"""

import os
import json
from pathlib import Path
from typing import Optional, List


def _get_accounts_file() -> Optional[Path]:
    """Returns the accounts.json path if it exists and is readable, None otherwise."""
    # Try explicit env var first
    if env_path := os.environ.get("WHATSAPP_ACCOUNTS_FILE"):
        path = Path(env_path).expanduser()
        if path.exists() and path.is_file():
            return path

    # Try ~/.whatsapp-mcp/accounts.json
    default_path = Path.home() / ".whatsapp-mcp" / "accounts.json"
    if default_path.exists() and default_path.is_file():
        return default_path

    return None


def _load_accounts_map() -> Optional[dict]:
    """Load and parse the accounts.json file, or None if it doesn't exist.

    Raises:
        ValueError: If the file exists but cannot be parsed as valid JSON.
    """
    accounts_file = _get_accounts_file()
    if not accounts_file:
        return None

    try:
        with open(accounts_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(
            f"Invalid JSON in accounts file '{accounts_file}': {e}"
        ) from e
    except (IOError, OSError) as e:
        raise ValueError(
            f"Cannot read accounts file '{accounts_file}': {e}"
        ) from e


def accounts_configured() -> bool:
    """Returns True if a valid accounts.json file is readable.

    Returns False if no accounts file exists.
    Raises ValueError if an accounts file exists but is invalid.
    """
    try:
        return _load_accounts_map() is not None
    except ValueError:
        # Re-raise to indicate misconfiguration (file exists but invalid)
        raise


def resolve_account(alias: Optional[str] = None) -> str:
    """
    Resolve an account alias to its bridge base URL.

    Args:
        alias: Account alias (e.g., "pessoal", "trabalho"), or None for the default account.

    Returns:
        The base URL for the account (e.g., "http://localhost:3005/api").

    Raises:
        ValueError: If the alias is unknown or if no default is set when alias is None.
    """
    accounts_map = _load_accounts_map()

    if accounts_map is None:
        # No accounts.json — fall back to environment configuration
        if alias is not None:
            raise ValueError(f"Account '{alias}' not found. No accounts configured (known: [])")

        # Derive URL from environment, matching the precedence in transcribe.py
        base_url = os.environ.get("WHATSAPP_API_BASE_URL")
        if base_url:
            return base_url

        port = os.environ.get("WHATSAPP_BRIDGE_PORT", "8080")
        return f"http://localhost:{port}/api"

    # Accounts map exists
    if alias is None:
        # Use default account
        default_alias = accounts_map.get("default")
        if not default_alias:
            raise ValueError("No default account set in accounts.json")
        alias = default_alias

    accounts = accounts_map.get("accounts", {})
    if alias not in accounts:
        known_aliases = sorted(accounts.keys())
        raise ValueError(f"Account '{alias}' not found. Known accounts: {known_aliases}")

    account = accounts[alias]
    port = account.get("port")
    if port is None:
        raise ValueError(f"Account '{alias}' has no port configured")

    return f"http://localhost:{port}/api"


def known_aliases() -> List[str]:
    """Return the list of configured account aliases."""
    accounts_map = _load_accounts_map()
    if accounts_map is None:
        return []
    return sorted(accounts_map.get("accounts", {}).keys())


def account_dir(alias: Optional[str] = None) -> str:
    """
    Get the directory for an account.

    Args:
        alias: Account alias, or None for the default account.

    Returns:
        The directory path for the account.

    Raises:
        ValueError: If no accounts are configured or the alias is unknown.
    """
    accounts_map = _load_accounts_map()
    if accounts_map is None:
        raise ValueError("No accounts configured")

    if alias is None:
        alias = accounts_map.get("default")
        if not alias:
            raise ValueError("No default account set")

    accounts = accounts_map.get("accounts", {})
    if alias not in accounts:
        known = sorted(accounts.keys())
        raise ValueError(f"Account '{alias}' not found. Known accounts: {known}")

    dir_path = accounts[alias].get("dir")
    if not dir_path:
        raise ValueError(f"Account '{alias}' has no directory configured")

    return dir_path


def account_task_name(alias: Optional[str] = None) -> str:
    """
    Get the scheduled task name for an account.

    Args:
        alias: Account alias, or None for the default account.

    Returns:
        The task name (e.g., "WhatsAppMCPBridge-pessoal").

    Raises:
        ValueError: If no accounts are configured or the alias is unknown.
    """
    if alias is None:
        accounts_map = _load_accounts_map()
        if accounts_map is None:
            raise ValueError("No accounts configured")
        alias = accounts_map.get("default")
        if not alias:
            raise ValueError("No default account set")

    return f"WhatsAppMCPBridge-{alias}"
