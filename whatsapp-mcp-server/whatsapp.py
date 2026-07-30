import unicodedata
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any
import os
import os.path
import requests
import json
import audio

WHATSAPP_API_BASE_URL = os.environ.get("WHATSAPP_API_BASE_URL", "http://localhost:8080/api")
WHATSAPP_API_AUTH_TOKEN = os.environ.get("WHATSAPP_API_AUTH_TOKEN", "")

REQUEST_TIMEOUT = 30

# Cache of sender_jid -> resolved name, avoids one HTTP round trip per message
# when formatting a batch of messages (format_message is called per-message).
_sender_name_cache: Dict[str, str] = {}


def _auth_headers() -> Dict[str, str]:
    if WHATSAPP_API_AUTH_TOKEN:
        return {"Authorization": f"Bearer {WHATSAPP_API_AUTH_TOKEN}"}
    return {}


def _api_post(path: str, payload: dict, timeout: int = REQUEST_TIMEOUT) -> Optional[dict]:
    """POST to the bridge REST API. Returns the parsed JSON dict on HTTP 200,
    or None on any failure (non-200 status, timeout, connection error, bad JSON).
    Never raises — mirrors the old `except sqlite3.Error` behavior."""
    try:
        url = f"{WHATSAPP_API_BASE_URL}{path}"
        response = requests.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
        if response.status_code != 200:
            print(f"API error: HTTP {response.status_code} - {response.text}")
            return None
        return response.json()
    except requests.RequestException as e:
        print(f"Request error: {str(e)}")
        return None
    except json.JSONDecodeError:
        print("Error parsing response as JSON")
        return None
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
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


def get_sender_name(sender_jid: str) -> str:
    if sender_jid in _sender_name_cache:
        return _sender_name_cache[sender_jid]

    result = _api_post("/sender_name", {"sender_jid": sender_jid})
    if result is None:
        # Transport failure: don't cache, so a later retry can still succeed.
        return sender_jid

    name = result.get("name", sender_jid)
    _sender_name_cache[sender_jid] = name
    return name

def format_message(message: Message, show_chat_info: bool = True) -> None:
    """Print a single message with consistent formatting."""
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
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        print(f"Error formatting message: {e}")
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> None:
    output = ""
    if not messages:
        output += "No messages to display."
        return output

    for message in messages:
        output += format_message(message, show_chat_info)
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
    context_after: int = 1
) -> List[Message]:
    """Get messages matching the specified criteria with optional context."""
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

    result = _api_post("/messages", payload)
    if result is None:
        return []

    messages = [_message_from_dict(m) for m in result.get("messages", [])]

    if include_context and messages:
        messages_with_context = []
        for msg in messages:
            try:
                context = get_message_context(msg.id, context_before, context_after)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            except Exception as e:
                # A single failed context lookup shouldn't sink the whole batch -
                # fall back to the bare message (mirrors "never propagate" contract).
                print(f"Error fetching context for message {msg.id}: {e}")
                messages_with_context.append(msg)

        return format_messages_list(messages_with_context, show_chat_info=True)

    return format_messages_list(messages, show_chat_info=True)


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    result = _api_post("/message_context", {
        "message_id": message_id,
        "before": before,
        "after": after,
    })

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
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria."""
    result = _api_post("/chats", {
        "query": query,
        "limit": limit,
        "page": page,
        "include_last_message": include_last_message,
        "sort_by": sort_by,
    })

    if result is None:
        return []

    return [_chat_from_dict(c) for c in result.get("chats", [])]


def search_contacts(query: str) -> List[Contact]:
    """Search contacts by name or phone number."""
    result = _api_post("/contacts/search", {"query": query})

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


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact.

    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    result = _api_post("/contacts/chats", {
        "jid": jid,
        "limit": limit,
        "page": page,
    })

    if result is None:
        return []

    return [_chat_from_dict(c) for c in result.get("chats", [])]


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    result = _api_post("/contacts/last_interaction", {"jid": jid})

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
    return format_message(message)


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID."""
    result = _api_post("/chat", {
        "chat_jid": chat_jid,
        "include_last_message": include_last_message,
    })

    if result is None:
        return None

    chat_data = result.get("chat")
    if not chat_data:
        return None

    return _chat_from_dict(chat_data)


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number (handles LID contacts)."""
    result = _api_post("/chat/by_contact", {"sender_phone_number": _normalize_phone(sender_phone_number)})

    if result is None:
        return None

    chat_data = result.get("chat")
    if not chat_data:
        return None

    return _chat_from_dict(chat_data)

def send_message(recipient: str, message: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"

        url = f"{WHATSAPP_API_BASE_URL}/send"
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

def send_file(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"

        if not media_path:
            return False, "Media path must be provided"

        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        url = f"{WHATSAPP_API_BASE_URL}/send"
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

def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
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

        url = f"{WHATSAPP_API_BASE_URL}/send"
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

def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Download media from a message and return the local file path.

    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message

    Returns:
        The local file path if download was successful, None otherwise
    """
    try:
        url = f"{WHATSAPP_API_BASE_URL}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }

        response = requests.post(url, json=payload, headers=_auth_headers())

        if response.status_code == 200:
            result = response.json()
            if result.get("success", False):
                path = result.get("path")
                print(f"Media downloaded successfully: {path}")
                return path
            else:
                print(f"Download failed: {result.get('message', 'Unknown error')}")
                return None
        else:
            print(f"Error: HTTP {response.status_code} - {response.text}")
            return None

    except requests.RequestException as e:
        print(f"Request error: {str(e)}")
        return None
    except json.JSONDecodeError:
        print(f"Error parsing response: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return None


def create_group(
    name: str,
    participants: List[str],
    is_community: bool = False,
    community_parent_jid: str = "",
) -> Tuple[bool, str, Optional[dict]]:
    try:
        if not name or not name.strip():
            return False, "Group name is required", None
        if not participants:
            return False, "At least one participant is required", None
        url = f"{WHATSAPP_API_BASE_URL}/create_group"
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


def leave_group(jid: str) -> Tuple[bool, str]:
    try:
        if not jid or not jid.strip():
            return False, "Group JID is required"
        url = f"{WHATSAPP_API_BASE_URL}/leave_group"
        response = requests.post(url, json={"jid": jid}, headers=_auth_headers())
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def mark_chat_read(chat_jid: str, message_ids: List[str], sender_jid: str = "", timestamp: int = 0) -> Tuple[bool, str]:
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        url = f"{WHATSAPP_API_BASE_URL}/mark_chat_read"
        payload: Dict[str, Any] = {"chat_jid": chat_jid, "message_ids": message_ids}
        if sender_jid:
            payload["sender_jid"] = sender_jid
        if timestamp:
            payload["timestamp"] = timestamp
        response = requests.post(url, json=payload, headers=_auth_headers())
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def mark_chat_unread(chat_jid: str) -> Tuple[bool, str]:
    try:
        if not chat_jid or not chat_jid.strip():
            return False, "chat_jid is required"
        url = f"{WHATSAPP_API_BASE_URL}/mark_chat_unread"
        response = requests.post(url, json={"chat_jid": chat_jid}, headers=_auth_headers())
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error parsing response: {response.text}"
        return bool(result.get("success", False)), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"
