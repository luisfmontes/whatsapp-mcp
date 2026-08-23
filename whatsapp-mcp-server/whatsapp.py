import logging
import unicodedata
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any
from urllib.parse import urlparse, urlunparse
import os
import os.path
import requests
import json
import audio
import accounts

# This server runs over MCP's stdio transport, where stdout carries JSON-RPC
# framing — a stray print() either corrupts that stream or is silently
# discarded, so it's not a usable diagnostic channel. logging defaults to
# stderr, which stdio-transport MCP servers can safely use for diagnostics.
logger = logging.getLogger(__name__)

WHATSAPP_API_AUTH_TOKEN = os.environ.get("WHATSAPP_API_AUTH_TOKEN", "")

REQUEST_TIMEOUT = 30

# Cache of (base_url, sender_jid) -> resolved name, avoids one HTTP round trip per message
# when formatting a batch of messages (format_message is called per-message).
# Key is segmented by account (base_url) to prevent data leakage between accounts.
_sender_name_cache: Dict[Tuple[str, str], str] = {}


def _auth_headers() -> Dict[str, str]:
    if WHATSAPP_API_AUTH_TOKEN:
        return {"Authorization": f"Bearer {WHATSAPP_API_AUTH_TOKEN}"}
    return {}


def _require_account(account: Optional[str]) -> None:
    """Guard for write operations: fail if account is None and multiple accounts are configured.

    Implements D2 from design: write operations without account must fail before any HTTP
    request when multiple accounts are configured, with a message listing known aliases.

    Args:
        account: The account alias passed to the write operation.

    Raises:
        ValueError: If account is None and multiple accounts are configured.
    """
    if account is None:
        known = accounts.known_aliases()
        if len(known) > 1:
            raise ValueError(
                f"Write operation requires an account. "
                f"Multiple accounts configured: {', '.join(known)}. "
                f"Specify one explicitly: account='<account_name>'"
            )



def _check_account_online(account: Optional[str]) -> str:
    """Verify that an account's bridge is online by probing /status.

    Implements D12 from design: when a bridge is unreachable, return a clear error
    message naming the account alias and its scheduled task name.

    Args:
        account: The account alias to check (None = default account).

    Returns:
        The base_url if the account's bridge responds to /status.

    Raises:
        ValueError: If the account's bridge is unreachable, with a message naming
                   the account alias and the task name (e.g., "WhatsAppMCPBridge-trabalho").
    """
    base_url = accounts.resolve_account(account)

    # Resolve alias for error message
    accounts_map = accounts._load_accounts_map()
    resolved_account = account
    if accounts_map:
        if account is None:
            resolved_account = accounts_map.get("default")

    try:
        # Quick probe with short timeout: if /status doesn't respond, bridge is down
        response = _api_request("GET", "/status", base_url, timeout=5)
        response.raise_for_status()
        return base_url
    except requests.RequestException:
        # Bridge is offline; generate error message with task name
        task_name = accounts.account_task_name(resolved_account)
        raise ValueError(
            f"Account '{resolved_account}' bridge is offline. "
            f"Start the scheduled task: {task_name}"
        )


def _resolve_and_check_account_explicit(account: str) -> str:
    """Resolve account and verify bridge is online when account is explicitly provided.

    D12: Read operations called with an explicit account parameter should fail
    with a clear error (not silently return empty data) if that account's bridge
    is unreachable.

    Args:
        account: The account alias (NOT None). Must be explicitly provided.

    Returns:
        The base_url if the account's bridge responds to /status.

    Raises:
        ValueError: If the account does not exist or its bridge is unreachable.
    """
    return _check_account_online(account)


def _api_request(method: str, path: str, base_url: str, timeout: int = REQUEST_TIMEOUT, **kwargs) -> requests.Response:
    """GET/POST to the bridge with auth header + timeout, raising requests.RequestException
    on 401 with an actionable message. Used by the Tuple[bool, str, ...]-returning action
    functions below, which need the raw Response (they parse .json() and build their own
    return tuple) rather than _api_post's Optional[dict] shape."""
    url = f"{base_url}{path}"
    response = requests.request(method, url, headers=_auth_headers(), timeout=timeout, **kwargs)
    if response.status_code == 401:
        raise requests.RequestException(
            f"HTTP 401 - check WHATSAPP_API_AUTH_TOKEN matches the bridge's API_AUTH_TOKEN ({response.text})"
        )
    return response


def _api_post(path: str, payload: dict, base_url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[dict]:
    """POST to the bridge REST API. Returns the parsed JSON dict on HTTP 200,
    or None on any failure (non-200 status, timeout, connection error, bad JSON).
    Never raises — mirrors the old `except sqlite3.Error` behavior."""
    try:
        url = f"{base_url}{path}"
        response = requests.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
        if response.status_code == 401:
            logger.warning(
                "API auth error on %s: HTTP 401 - check WHATSAPP_API_AUTH_TOKEN "
                "matches the bridge's API_AUTH_TOKEN (%s)",
                path, response.text,
            )
            return None
        if response.status_code != 200:
            logger.warning("API error on %s: HTTP %s - %s", path, response.status_code, response.text)
            return None
        return response.json()
    except requests.RequestException as e:
        logger.warning("Request error on %s: %s", path, e)
        return None
    except json.JSONDecodeError:
        logger.warning("Error parsing response as JSON from %s", path)
        return None
    except Exception as e:
        logger.warning("Unexpected error on %s: %s", path, e)
        return None


def _strip_accents(text: Optional[str]) -> Optional[str]:
    """Lowercase and remove diacritics so searches match regardless of accents."""
    if text is None:
        return None
    decomposed = unicodedata.normalize('NFD', text)
    return ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn').lower()


def _normalize_phone(phone: str) -> str:
    return phone.replace('+', '').replace(' ', '').replace('-', '')


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an RFC3339/ISO-8601 timestamp coming from the bridge JSON.
    Returns None for falsy input or unparseable strings (never raises)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]


def _message_from_dict(d: dict) -> Message:
    return Message(
        timestamp=_parse_ts(d.get("timestamp")),
        sender=d.get("sender"),
        chat_name=d.get("chat_name"),
        content=d.get("content"),
        is_from_me=d.get("is_from_me", False),
        chat_jid=d.get("chat_jid"),
        id=d.get("id"),
        media_type=d.get("media_type"),
    )


def _chat_from_dict(d: dict) -> Chat:
    return Chat(
        jid=d.get("jid"),
        name=d.get("name"),
        last_message_time=_parse_ts(d.get("last_message_time")),
        last_message=d.get("last_message"),
        last_sender=d.get("last_sender"),
        last_is_from_me=d.get("last_is_from_me"),
    )


def get_sender_name(sender_jid: str, account: Optional[str] = None) -> str:
    base_url = accounts.resolve_account(account)
    # Cache key includes base_url (account) to prevent data leakage between accounts
    cache_key = (base_url, sender_jid)
    if cache_key in _sender_name_cache:
        return _sender_name_cache[cache_key]

    result = _api_post("/sender_name", {"sender_jid": sender_jid}, base_url)
    if result is None:
        # Transport failure: don't cache, so a later retry can still succeed.
        return sender_jid

    name = result.get("name", sender_jid)
    _sender_name_cache[cache_key] = name
    return name

def format_message(message: Message, show_chat_info: bool = True, account: Optional[str] = None) -> str:
    """Format a single message with consistent formatting."""
    output = ""
    ts_str = f"{message.timestamp:%Y-%m-%d %H:%M:%S}" if message.timestamp else "unknown time"

    if show_chat_info and message.chat_name:
        output += f"[{ts_str}] Chat: {message.chat_name} "
    else:
        output += f"[{ts_str}] "

    content_prefix = ""
    if hasattr(message, 'media_type') and message.media_type:
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "

    try:
        sender_name = get_sender_name(message.sender, account) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        logger.warning("Error formatting message %s: %s", message.id, e)
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True, account: Optional[str] = None) -> str:
    output = ""
    if not messages:
        output += "No messages to display."
        return output

    for message in messages:
        output += format_message(message, show_chat_info, account)
    return output

def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1,
    account: Optional[str] = None
) -> str:
    """Get messages matching the specified criteria with optional context."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    # Validate date filters up front (same contract as before: invalid dates raise
    # a ValueError that propagates to the caller, it is NOT swallowed like transport errors).
    if after:
        try:
            datetime.fromisoformat(after)
        except ValueError:
            raise ValueError(f"Invalid date format for 'after': {after}. Please use ISO-8601 format.")

    if before:
        try:
            datetime.fromisoformat(before)
        except ValueError:
            raise ValueError(f"Invalid date format for 'before': {before}. Please use ISO-8601 format.")

    payload = {
        "after": after,
        "before": before,
        "sender_phone_number": _normalize_phone(sender_phone_number) if sender_phone_number else None,
        "chat_jid": chat_jid,
        "query": query,
        "limit": limit,
        "page": page,
    }

    result = _api_post("/messages", payload, base_url)
    if result is None:
        return ""

    messages = [_message_from_dict(m) for m in result.get("messages", [])]

    if include_context and messages:
        messages_with_context = []
        context_failures = []
        for msg in messages:
            try:
                context = get_message_context(msg.id, context_before, context_after, account)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            except Exception as e:
                # A single failed context lookup shouldn't sink the whole batch -
                # fall back to the bare message. But unlike a genuine "not found"
                # (which get_message_context itself distinguishes), any failure here
                # silently degrades results the caller can't otherwise detect, so
                # surface it in the response text instead of only a stdout print
                # (this server runs over stdio, where stdout is the JSON-RPC channel,
                # not a readable log).
                context_failures.append(f"{msg.id} ({e})")
                messages_with_context.append(msg)

        output = format_messages_list(messages_with_context, show_chat_info=True, account=account)
        if context_failures:
            output += (
                f"\n[Warning: context lookup failed for {len(context_failures)} message(s), "
                f"showing them without surrounding context: {'; '.join(context_failures)}]"
            )
        return output

    return format_messages_list(messages, show_chat_info=True, account=account)


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5,
    account: Optional[str] = None
) -> MessageContext:
    """Get context around a specific message.

    D12: If account is explicitly provided and its bridge is offline, raises ValueError
    with account alias and scheduled task name instead of silently returning empty result.

    D1: When account is None, falls back to default account without checking online status.
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/message_context", {
        "message_id": message_id,
        "before": before,
        "after": after,
    }, base_url)

    if result is None:
        # Transport/server failure - mirror old `except ... raise` behavior for
        # the DB-error path by not silently fabricating a context.
        raise ValueError(f"Could not fetch context for message ID {message_id}")

    msg_data = result.get("message")
    if not msg_data:
        raise ValueError(f"Message with ID {message_id} not found")

    target_message = _message_from_dict(msg_data)
    before_messages = [_message_from_dict(m) for m in result.get("before", [])]
    after_messages = [_message_from_dict(m) for m in result.get("after", [])]

    return MessageContext(
        message=target_message,
        before=before_messages,
        after=after_messages
    )


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active",
    account: Optional[str] = None
) -> List[Chat]:
    """Get chats matching the specified criteria.

    D12: If account is explicitly provided and its bridge is offline, raises ValueError
    with account alias and scheduled task name instead of silently returning empty list.

    D1: When account is None, falls back to default account without checking online status.
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)

    result = _api_post("/chats", {
        "query": query,
        "limit": limit,
        "page": page,
        "include_last_message": include_last_message,
        "sort_by": sort_by,
    }, base_url)

    if result is None:
        return []

    return [_chat_from_dict(c) for c in result.get("chats", [])]


def search_contacts(query: str, account: Optional[str] = None) -> List[Contact]:
    """Search contacts by name or phone number."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/contacts/search", {"query": query}, base_url)

    if result is None:
        return []

    return [
        Contact(
            phone_number=c.get("phone_number"),
            name=c.get("name"),
            jid=c.get("jid"),
        )
        for c in result.get("contacts", [])
    ]


def get_contact_chats(jid: str, limit: int = 20, page: int = 0, account: Optional[str] = None) -> List[Chat]:
    """Get all chats involving the contact.

    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/contacts/chats", {
        "jid": jid,
        "limit": limit,
        "page": page,
    }, base_url)

    if result is None:
        return []

    return [_chat_from_dict(c) for c in result.get("chats", [])]


def get_last_interaction(jid: str, account: Optional[str] = None) -> str:
    """Get most recent message involving the contact."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/contacts/last_interaction", {"jid": jid}, base_url)

    if result is None:
        return None

    # interfaces.md documents this endpoint two ways (`formatted` string vs raw
    # `message` object); accept either so a mismatch with the bridge implementation
    # doesn't silently break this call.
    if "formatted" in result and result.get("formatted") is not None:
        return result["formatted"]

    msg_data = result.get("message")
    if not msg_data:
        return None

    message = _message_from_dict(msg_data)
    return format_message(message, account=account)


def get_chat(chat_jid: str, include_last_message: bool = True, account: Optional[str] = None) -> Optional[Chat]:
    """Get chat metadata by JID."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/chat", {
        "chat_jid": chat_jid,
        "include_last_message": include_last_message,
    }, base_url)

    if result is None:
        return None

    chat_data = result.get("chat")
    if not chat_data:
        return None

    return _chat_from_dict(chat_data)


def get_direct_chat_by_contact(sender_phone_number: str, account: Optional[str] = None) -> Optional[Chat]:
    """Get chat metadata by sender phone number (handles LID contacts)."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    result = _api_post("/chat/by_contact", {"sender_phone_number": _normalize_phone(sender_phone_number)}, base_url)

    if result is None:
        return None

    chat_data = result.get("chat")
    if not chat_data:
        return None

    return _chat_from_dict(chat_data)

def send_message(recipient: str, message: str, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"

        url = f"{base_url}/send"
        payload = {
            "recipient": recipient,
            "message": message,
        }

        response = requests.post(url, json=payload, headers=_auth_headers())

        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"

    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_file(recipient: str, media_path: str, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"

        if not media_path:
            return False, "Media path must be provided"

        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        url = f"{base_url}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }

        response = requests.post(url, json=payload, headers=_auth_headers())

        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"

    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_audio_message(recipient: str, media_path: str, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"

        if not media_path:
            return False, "Media path must be provided"

        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        if not media_path.endswith(".ogg"):
            try:
                media_path = audio.convert_to_opus_ogg_temp(media_path)
            except Exception as e:
                return False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}"

        url = f"{base_url}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }

        response = requests.post(url, json=payload, headers=_auth_headers())

        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"

    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def download_media(message_id: str, chat_jid: str, account: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Download media from a message and return the local file path.

    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message

    Returns:
        A tuple of (file path or None, status message). The message carries the
        reason on failure: the bridge already reports one, and collapsing every
        failure mode into a bare None left the caller unable to tell an expired
        media from a bad auth token from a bridge that is simply down.
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        url = f"{base_url}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }

        response = requests.post(url, json=payload, headers=_auth_headers())

        if response.status_code == 200:
            result = response.json()
            if result.get("success", False):
                path = result.get("path")
                logger.info("Media downloaded successfully: %s", path)
                return path, "Media downloaded successfully"
            else:
                reason = result.get('message', 'Unknown error')
                logger.warning("Download failed: %s", reason)
                return None, reason
        elif response.status_code == 401:
            reason = (
                "HTTP 401 - check WHATSAPP_API_AUTH_TOKEN matches the bridge's "
                f"API_AUTH_TOKEN ({response.text})"
            )
            logger.warning("Download auth error: %s", reason)
            return None, reason
        else:
            reason = f"HTTP {response.status_code} - {response.text}"
            logger.warning("Download error: %s", reason)
            return None, reason

    except requests.RequestException as e:
        logger.warning("Download request error: %s", e)
        return None, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        logger.warning("Error parsing download response: %s", response.text)
        return None, f"Error parsing response: {response.text}"
    except Exception as e:
        logger.warning("Unexpected download error: %s", e)
        return None, f"Unexpected error: {str(e)}"


def create_group(
    name: str,
    participants: List[str],
    is_community: bool = False,
    community_parent_jid: str = "",
    account: Optional[str] = None
) -> Tuple[bool, str, Optional[dict]]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not name or not name.strip():
            return False, "Group name is required", None
        if not participants:
            return False, "At least one participant is required", None
        url = f"{base_url}/create_group"
        payload = {
            "name": name,
            "participants": participants,
            "is_community": is_community,
            "community_parent_jid": community_parent_jid,
        }
        response = requests.post(url, json=payload, headers=_auth_headers())
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None
        success = bool(result.get("success", False))
        message = result.get("message", "Unknown response")
        details = {
            "jid": result.get("jid"),
            "name": result.get("name"),
            "participant_count": result.get("participant_count"),
        } if success else None
        return success, message, details
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def leave_group(jid: str, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not jid or not jid.strip():
            return False, "Group JID is required"
        response = _api_request("POST", "/leave_group", base_url, json={"jid": jid})
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def mark_chat_read(chat_jid: str, message_ids: List[str], sender_jid: str = "", timestamp: int = 0, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        payload: Dict[str, Any] = {"chat_jid": chat_jid, "message_ids": message_ids}
        if sender_jid:
            payload["sender_jid"] = sender_jid
        if timestamp:
            payload["timestamp"] = timestamp
        response = _api_request("POST", "/mark_chat_read", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def mark_chat_unread(chat_jid: str, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        response = _api_request("POST", "/mark_chat_unread", base_url, json={"chat_jid": chat_jid})
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def get_group_info(jid: str, account: Optional[str] = None) -> Tuple[bool, str, Optional[dict]]:
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not jid or not jid.strip():
            return False, "Group JID is required", None
        response = _api_request("GET", "/group_info", base_url, params={"jid": jid})
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None
        success = bool(result.get("success", False))
        if not success:
            return False, result.get("message", "Unknown response"), None
        info = {
            "name": result.get("name", ""),
            "participants": result.get("participants", []),
            "topic": result.get("topic", ""),
            "is_locked": bool(result.get("is_locked", False)),
            "is_announce": bool(result.get("is_announce", False)),
        }
        return True, "Group info retrieved", info
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def archive_chat(chat_jid: str, archive: bool, account: Optional[str] = None) -> Tuple[bool, str]:
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        response = _api_request("POST", "/archive_chat", base_url, json={"chat_jid": chat_jid, "archive": archive})
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def resolve_contact(phone: str, account: Optional[str] = None) -> Tuple[bool, str, List[str]]:
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not phone or not phone.strip():
            return False, "phone is required", []
        response = _api_request("GET", "/resolve_contact", base_url, params={"phone": phone})
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", []
        success = bool(result.get("success", False))
        jids = result.get("jids", []) or []
        return success, result.get("message", "Contact resolved" if success else "Unknown response"), jids
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", []
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", []


def react_to_message(chat_jid: str, message_id: str, emoji: str, from_me: bool = True, account: Optional[str] = None) -> Tuple[bool, str]:
    """React to a message with an emoji ("" removes the reaction).

    from_me defaults True (your own message). from_me=False works only in direct
    chats; in group chats the bridge returns an error because the original
    sender's JID isn't available.
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        if not message_id or not message_id.strip():
            return False, "message_id is required"
        payload = {
            "chat_jid": chat_jid,
            "message_id": message_id,
            "emoji": emoji,
            "from_me": from_me,
        }
        response = _api_request("POST", "/react", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def edit_message(chat_jid: str, message_id: str, new_text: str, from_me: bool = True, account: Optional[str] = None) -> Tuple[bool, str]:
    """Edit the text of a previously sent message.

    from_me is accepted for API symmetry but ignored — WhatsApp (whatsmeow's
    BuildEdit) only allows editing your own messages; the bridge never reads it.
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        if not message_id or not message_id.strip():
            return False, "message_id is required"
        payload = {
            "chat_jid": chat_jid,
            "message_id": message_id,
            "new_text": new_text,
            "from_me": from_me,
        }
        response = _api_request("POST", "/edit", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def delete_message(chat_jid: str, message_id: str, from_me: bool = True, account: Optional[str] = None) -> Tuple[bool, str]:
    """Delete a message for everyone (revoke).

    from_me defaults True (your own message). from_me=False works only in direct
    chats; in group chats the bridge returns an error because the original
    sender's JID isn't available.
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        if not message_id or not message_id.strip():
            return False, "message_id is required"
        payload = {
            "chat_jid": chat_jid,
            "message_id": message_id,
            "from_me": from_me,
        }
        response = _api_request("POST", "/revoke", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def update_group_participants(group_jid: str, participants: List[str], action: str, account: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
    """Update group participants (add/remove/promote/demote).

    Returns (success, message, participants_list). Each participant in the list
    has jid, is_admin, error (0 = applied, non-0 = WhatsApp error code),
    and optionally add_request (true if invitation pending).
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not group_jid or not group_jid.strip():
            return False, "group_jid is required", []
        if not participants:
            return False, "participants list is required", []
        payload = {
            "group_jid": group_jid,
            "participants": participants,
            "action": action,
        }
        response = _api_request("POST", "/group_participants", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", []
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("participants", []),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", []
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", []


def send_chat_presence(chat_jid: str, state: str, media: str = "", account: Optional[str] = None) -> Tuple[bool, str]:
    """Send chat presence (typing/paused).

    state: 'composing' (typing) or 'paused' (stopped).
    media: '' (text, default) or 'audio' (recording).
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        payload = {
            "chat_jid": chat_jid,
            "state": state,
            "media": media,
        }
        response = _api_request("POST", "/chat_presence", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def check_whatsapp(phones: List[str], account: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
    """Check if phone numbers are on WhatsApp.

    phones: list of phone numbers (with or without +). Each number is checked
    and result includes query (normalized), jid, is_in, and optionally verified_name.
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not phones:
            return False, "phones list is required", []
        payload = {"phones": phones}
        response = _api_request("POST", "/is_on_whatsapp", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", []
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("results", []),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", []
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", []


def get_group_invite_link(group_jid: str, reset: bool = False, account: Optional[str] = None) -> Tuple[bool, str, str]:
    """Get group invite link (or reset if reset=True).

    D2: When reset=True, this is a write operation and requires account to be specified
    (fails if None and multiple accounts configured). When reset=False, this is a read
    operation and account is optional.

    D12: If account is explicitly provided, verify its bridge is online.
    """
    # D2: reset=True is a write operation and requires account
    if reset:
        _require_account(account)

    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not group_jid or not group_jid.strip():
            return False, "group_jid is required", ""
        payload = {
            "group_jid": group_jid,
            "reset": reset,
        }
        response = _api_request("POST", "/group_invite_link", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", ""
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("link", ""),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", ""
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", ""


def get_group_invite_info(link: str, account: Optional[str] = None) -> Tuple[bool, str, Optional[dict]]:
    """Get group info from invite link (doesn't join the group)."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not link or not link.strip():
            return False, "link is required", None
        payload = {"link": link}
        response = _api_request("POST", "/group_invite_info", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None
        if not result.get("success", False):
            return False, result.get("message", "Unknown response"), None
        info = {
            "jid": result.get("jid", ""),
            "name": result.get("name", ""),
            "topic": result.get("topic", ""),
            "participants": result.get("participants", []),
        }
        return True, result.get("message", "Unknown response"), info
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def join_group_with_link(link: str, account: Optional[str] = None) -> Tuple[bool, str, str]:
    """Join a group using invite link."""
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not link or not link.strip():
            return False, "link is required", ""
        payload = {"link": link}
        response = _api_request("POST", "/join_group_with_link", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", ""
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("jid", ""),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", ""
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", ""


def update_group_settings(group_jid: str, name: Optional[str] = None, topic: Optional[str] = None,
                         announce: Optional[bool] = None, locked: Optional[bool] = None, account: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
    """Update group settings (name, topic, announce, locked)."""
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not group_jid or not group_jid.strip():
            return False, "group_jid is required", []
        if name is None and topic is None and announce is None and locked is None:
            return False, "at least one of name, topic, announce, locked is required", []

        payload = {"group_jid": group_jid}
        if name is not None:
            payload["name"] = name
        if topic is not None:
            payload["topic"] = topic
        if announce is not None:
            payload["announce"] = announce
        if locked is not None:
            payload["locked"] = locked

        response = _api_request("POST", "/group_settings", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", []
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("results", []),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", []
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", []


def set_group_photo(group_jid: str, media_path: str = "", remove: bool = False, account: Optional[str] = None) -> Tuple[bool, str, str]:
    """Set group photo from file or remove it."""
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not group_jid or not group_jid.strip():
            return False, "group_jid is required", ""
        payload = {
            "group_jid": group_jid,
            "media_path": media_path,
            "remove": remove,
        }
        response = _api_request("POST", "/group_photo", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", ""
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("picture_id", ""),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", ""
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", ""


def get_user_info(jids: List[str], account: Optional[str] = None) -> Tuple[bool, str, List[dict]]:
    """Get user info for a list of JIDs."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not jids:
            return False, "jids list is required", []
        payload = {"jids": jids}
        response = _api_request("POST", "/user_info", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", []
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("results", []),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", []
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", []


def get_profile_picture(jid: str, preview: bool = False, account: Optional[str] = None) -> Tuple[bool, str, Optional[dict]]:
    """Get profile picture info for a user or group."""
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not jid or not jid.strip():
            return False, "jid is required", None
        payload = {
            "jid": jid,
            "preview": preview,
        }
        response = _api_request("POST", "/profile_picture", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None
        if not result.get("success", False):
            return False, result.get("message", "Unknown response"), None
        info = {
            "url": result.get("url", ""),
            "id": result.get("id", ""),
            "type": result.get("type", ""),
            "direct_path": result.get("direct_path", ""),
        }
        return True, result.get("message", "Unknown response"), info
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def create_poll(chat_jid: str, question: str, options: List[str], selectable_count: int = 1, account: Optional[str] = None) -> Tuple[bool, str, str]:
    """Create a poll in a chat.

    Returns (success, message, message_id).
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required", ""
        if not question or not question.strip():
            return False, "question is required", ""
        if not options or len(options) < 2 or len(options) > 12:
            return False, "options must have between 2 and 12 items", ""
        # Check for duplicates and empty option names
        trimmed = [opt.strip() for opt in options]
        if len(trimmed) != len(set(trimmed)):
            return False, "Invalid options: names must be unique and non-empty", ""
        if any(not opt for opt in trimmed):
            return False, "Invalid options: names must be unique and non-empty", ""
        if selectable_count < 1 or selectable_count > len(options):
            return False, f"selectable_count must be between 1 and {len(options)}", ""
        payload = {
            "chat_jid": chat_jid,
            "question": question,
            "options": options,
            "selectable_count": selectable_count,
        }
        response = _api_request("POST", "/create_poll", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", ""
        return (
            bool(result.get("success", False)),
            result.get("message", "Unknown response"),
            result.get("message_id", ""),
        )
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", ""
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", ""


def vote_in_poll(chat_jid: str, poll_id: str, options: List[str], account: Optional[str] = None) -> Tuple[bool, str]:
    """Vote in a poll. Empty options list means removing the vote.

    Returns (success, message).
    """
    _require_account(account)
    base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        if not poll_id or not poll_id.strip():
            return False, "poll_id is required"
        payload = {
            "chat_jid": chat_jid,
            "poll_id": poll_id,
            "options": options,
        }
        response = _api_request("POST", "/vote_poll", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def get_poll_results(chat_jid: str, poll_id: str, account: Optional[str] = None) -> Tuple[bool, str, Optional[dict]]:
    """Get poll results by poll ID.

    Returns (success, message, results_dict).
    """
    # D12: If account is explicitly provided, verify its bridge is online
    if account is not None:
        base_url = _resolve_and_check_account_explicit(account)
    else:
        base_url = accounts.resolve_account(account)
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required", None
        if not poll_id or not poll_id.strip():
            return False, "poll_id is required", None
        payload = {
            "chat_jid": chat_jid,
            "poll_id": poll_id,
        }
        response = _api_request("POST", "/poll_results", base_url, json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None
        if not result.get("success", False):
            return False, result.get("message", "Unknown response"), None
        results = {
            "question": result.get("question", ""),
            "results": result.get("results", []),
            "total_voters": result.get("total_voters", 0),
            "unresolved_votes": result.get("unresolved_votes", 0),
        }
        return True, result.get("message", "Unknown response"), results
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def get_bridge_status(account: Optional[str] = None) -> Tuple[bool, str, Optional[dict]]:
    """Get bridge health status (T005, RF-06).

    Returns (healthy, reason, status_dict).

    Key insight from contracts: healthy:false with reason "not logged in" means
    you need to scan the QR code at /qr — reconnecting won't help. If the bridge
    is unreachable (transport error), that's a different problem: the process is down.
    
    D13: When called without account and multiple accounts are configured,
    returns aggregated status of all accounts. Otherwise returns status of
    the specified (or default) account.
    """
    # D13: If no account specified and multiple configured, return aggregated status
    if account is None and accounts.accounts_configured():
        known = accounts.known_aliases()
        if len(known) > 1:
            # Aggregate status from all accounts
            result = {}
            for alias in known:
                try:
                    base_url = accounts.resolve_account(alias)
                    try:
                        # Use 5-second timeout for health-check
                        response = _api_request("GET", "/status", base_url, timeout=5)
                        try:
                            status_data = response.json()
                            result[alias] = {
                                "healthy": bool(status_data.get("healthy", False)),
                                "reason": status_data.get("reason", ""),
                                "status": status_data
                            }
                        except json.JSONDecodeError:
                            result[alias] = {
                                "healthy": False,
                                "reason": f"Error parsing response: {response.text}",
                                "status": None
                            }
                    except requests.RequestException as e:
                        # Account is offline - cite the task name (D12)
                        task_name = accounts.account_task_name(alias)
                        result[alias] = {
                            "healthy": False,
                            "reason": f"Bridge offline. Start task: {task_name}",
                            "status": None
                        }
                except Exception as e:
                    result[alias] = {
                        "healthy": False,
                        "reason": f"Error: {str(e)}",
                        "status": None
                    }
            # Return aggregated result as special format
            # We return (False, "aggregated", result_dict) to signal multi-account response
            return False, "aggregated", result

    # Single account case: original behavior
    base_url = accounts.resolve_account(account)
    try:
        # Use 5-second timeout: a health-check that waits 30s defeats the purpose
        response = _api_request("GET", "/status", base_url, timeout=5)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}", None

        healthy = bool(result.get("healthy", False))
        reason = result.get("reason", "")

        return healthy, reason, result
    except requests.RequestException as e:
        # D12: Bridge is offline; generate error message with account alias and task name
        # Resolve alias for error message (same logic as _check_account_online)
        accounts_map = accounts._load_accounts_map()
        resolved_account = account
        if accounts_map:
            if account is None:
                resolved_account = accounts_map.get("default")

        task_name = accounts.account_task_name(resolved_account)
        return False, f"Bridge offline. Start task: {task_name}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def pair_account(account: str) -> bytes:
    """
    Get the QR code PNG for pairing a registered account.

    Implements D4 from design: parear conta nova acontece por endpoint REST + tool MCP
    que devolve o QR. O alias é obrigatório aqui: parear a conta errada é pior que um
    erro de argumento. Se o alias não existir no mapa, o erro deve dizer para rodar o
    instalador.

    Args:
        account: Account alias to pair (required, not optional).

    Returns:
        The raw PNG bytes from /qr.png, starting with PNG signature \x89PNG.

    Raises:
        ValueError: If account is not registered, if the bridge is offline,
                   or if the account is already paired (logged in).
    """
    # Validate that account is provided (required for pairing)
    if not account:
        raise ValueError("Account alias is required for pairing")

    # Check if accounts are configured at all
    accounts_map = accounts._load_accounts_map()
    if accounts_map is None:
        raise ValueError(
            "No accounts configured. Run 'install.ps1 -AddAccount <alias>' to create an account."
        )

    # Check if the specified account exists
    known_accounts = accounts_map.get("accounts", {})
    if account not in known_accounts:
        known_aliases = sorted(known_accounts.keys())
        raise ValueError(
            f"Account '{account}' not found. Run 'install.ps1 -AddAccount {account}' to create it. "
            f"Known accounts: {known_aliases}"
        )

    # Verify bridge is online
    try:
        base_url = _check_account_online(account)
    except ValueError:
        # Re-raise the offline error from _check_account_online
        raise

    # Check if already paired: if bridge is logged_in, no QR available
    try:
        response = _api_request("GET", "/status", base_url, timeout=5)
        response.raise_for_status()
        status = response.json()

        # If already logged in, account is paired
        if status.get("logged_in", False):
            raise ValueError(
                f"Account '{account}' is already paired. No QR code available."
            )
    except (json.JSONDecodeError, requests.RequestException):
        # If we can't check status, continue to try getting QR
        # (bridge might be in an intermediate state)
        pass

    # Fetch QR PNG from the bridge
    # /qr.png is NOT under the /api prefix, so derive the origin from base_url
    # base_url is "http://localhost:PORT/api", extract "http://localhost:PORT"
    try:
        parsed = urlparse(base_url)
        # Reconstruct URL without the path (removes /api)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        qr_url = f"{origin}/qr.png"

        response = requests.get(qr_url, headers=_auth_headers(), timeout=REQUEST_TIMEOUT)

        if response.status_code == 404:
            # QR not available - either bridge just started or account is already paired
            # We checked logged_in above, so this is likely just bridge startup delay
            raise ValueError(
                f"QR code not yet available for account '{account}'. "
                f"Start the bridge and wait for the QR code to appear."
            )

        response.raise_for_status()

        # Return the PNG bytes
        return response.content

    except requests.RequestException as e:
        # Re-raise with clear message
        raise ValueError(f"Failed to get QR for account '{account}': {str(e)}")
