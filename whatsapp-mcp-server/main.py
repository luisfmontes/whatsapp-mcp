from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_messages as whatsapp_list_messages,
    list_chats as whatsapp_list_chats,
    get_chat as whatsapp_get_chat,
    get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact,
    get_contact_chats as whatsapp_get_contact_chats,
    get_last_interaction as whatsapp_get_last_interaction,
    get_message_context as whatsapp_get_message_context,
    send_message as whatsapp_send_message,
    send_file as whatsapp_send_file,
    send_audio_message as whatsapp_audio_voice_message,
    download_media as whatsapp_download_media,
    create_group as whatsapp_create_group,
    leave_group as whatsapp_leave_group,
    mark_chat_read as whatsapp_mark_chat_read,
    mark_chat_unread as whatsapp_mark_chat_unread,
    get_group_info as whatsapp_get_group_info,
    archive_chat as whatsapp_archive_chat,
    resolve_contact as whatsapp_resolve_contact,
    react_to_message as whatsapp_react_to_message,
    edit_message as whatsapp_edit_message,
    delete_message as whatsapp_delete_message,
    update_group_participants as whatsapp_update_group_participants,
    send_chat_presence as whatsapp_send_chat_presence,
    check_whatsapp as whatsapp_check_whatsapp,
    get_group_invite_link as whatsapp_get_group_invite_link,
    get_group_invite_info as whatsapp_get_group_invite_info,
    join_group_with_link as whatsapp_join_group_with_link,
    update_group_settings as whatsapp_update_group_settings,
    set_group_photo as whatsapp_set_group_photo,
    get_user_info as whatsapp_get_user_info,
    get_profile_picture as whatsapp_get_profile_picture,
    create_poll as whatsapp_create_poll,
    vote_in_poll as whatsapp_vote_in_poll,
    get_poll_results as whatsapp_get_poll_results,
    get_bridge_status as whatsapp_get_bridge_status,
    pair_account as whatsapp_pair_account
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")

@mcp.tool()
def search_contacts(query: str, account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts by name or phone number.

    Args:
        query: Search term to match against contact names or phone numbers
        account: Optional account alias to use (defaults to primary account)
    """
    contacts = whatsapp_search_contacts(query, account=account)
    return contacts

@mcp.tool()
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
    """Get WhatsApp messages matching specified criteria with optional context.

    Args:
        after: Optional ISO-8601 formatted string to only return messages after this date
        before: Optional ISO-8601 formatted string to only return messages before this date
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID to filter messages by chat
        query: Optional search term to filter messages by content
        limit: Maximum number of messages to return (default 20)
        page: Page number for pagination (default 0)
        include_context: Whether to include messages before and after matches (default True)
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)
        account: Optional account alias to use (defaults to primary account)
    """
    messages = whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after,
        account=account
    )
    return messages

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active",
    account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria.

    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
        account: Optional account alias to use (defaults to primary account)
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by,
        account=account
    )
    return chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True, account: Optional[str] = None) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.

    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
        account: Optional account alias to use (defaults to primary account)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message, account=account)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.

    Args:
        sender_phone_number: The phone number to search for
        account: Optional account alias to use (defaults to primary account)
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number, account=account)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0, account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.

    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        account: Optional account alias to use (defaults to primary account)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page, account=account)
    return chats

@mcp.tool()
def get_last_interaction(jid: str, account: Optional[str] = None) -> str:
    """Get most recent WhatsApp message involving the contact.

    Args:
        jid: The JID of the contact to search for
        account: Optional account alias to use (defaults to primary account)
    """
    message = whatsapp_get_last_interaction(jid, account=account)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.

    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
        account: Optional account alias to use (defaults to primary account)
    """
    context = whatsapp_get_message_context(message_id, before, after, account=account)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send
        account: Optional account alias to use (defaults to primary account)

    Returns:
        A dictionary containing success status and a status message
    """
    # Validate input
    if not recipient:
        return {
            "success": False,
            "message": "Recipient must be provided"
        }

    # Call the whatsapp_send_message function with the unified recipient parameter
    success, status_message = whatsapp_send_message(recipient, message, account=account)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_file(recipient: str, media_path: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        A dictionary containing success status and a status message
    """

    # Call the whatsapp_send_file function
    success, status_message = whatsapp_send_file(recipient, media_path, account=account)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_audio_message(recipient: str, media_path: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        A dictionary containing success status and a status message
    """
    success, status_message = whatsapp_audio_voice_message(recipient, media_path, account=account)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def download_media(message_id: str, chat_jid: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.

    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
        account: Optional account alias to use (defaults to primary account)

    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path, status_message = whatsapp_download_media(message_id, chat_jid, account=account)

    if file_path:
        return {
            "success": True,
            "message": status_message,
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": f"Failed to download media: {status_message}",
            "hint": "The desktop client usually saves received media to the local "
                    "downloads folder - look for the file there before retrying."
        }

@mcp.tool()
def create_group(
    name: str,
    participants: List[str],
    is_community: bool = False,
    community_parent_jid: str = "",
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new WhatsApp group.

    Args:
        name: Group subject (max 25 characters)
        participants: List of phone numbers (country code, no '+') or JIDs.
                      Your own number is added automatically.
        is_community: If True, create a community parent instead of a normal group
        community_parent_jid: If set, create as a sub-group inside this community
        account: Optional account alias to use (defaults to primary account)

    Returns:
        Dict with: success (bool), message (str), and on success: jid, name, participant_count.
    """
    success, message, details = whatsapp_create_group(
        name=name,
        participants=participants,
        is_community=is_community,
        community_parent_jid=community_parent_jid,
        account=account
    )
    response: Dict[str, Any] = {"success": success, "message": message}
    if success and details:
        response.update(details)
    return response


@mcp.tool()
def leave_group(jid: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Leave a WhatsApp group. Note: WhatsApp has no 'delete group' — leaving is
    the closest action.

    Args:
        jid: The group JID (must end with @g.us)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        Dict with success (bool) and message (str).
    """
    success, message = whatsapp_leave_group(jid, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def mark_chat_as_read(
    chat_jid: str,
    message_ids: List[str],
    sender_jid: Optional[str] = None,
    timestamp: Optional[int] = None,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Mark a WhatsApp chat as read by sending read receipts for the given messages.

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        message_ids: List of message IDs to mark as read
        sender_jid: Optional sender JID (required for group messages)
        timestamp: Optional Unix timestamp in seconds; defaults to now
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_mark_chat_read(
        chat_jid,
        message_ids,
        sender_jid=sender_jid or "",
        timestamp=timestamp or 0,
        account=account
    )
    return {"success": success, "message": message}


@mcp.tool()
def mark_chat_as_unread(chat_jid: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Mark a WhatsApp chat as unread (app-state sync — affects WhatsApp app badge).

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_mark_chat_unread(chat_jid, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def get_group_info(jid: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Get a WhatsApp group's name, topic, participant list and admin-only flags.

    Args:
        jid: The group JID (e.g. 120363012345678901@g.us)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {"success", "message", "name", "topic", "participants",
         "is_locked" (only admins can edit group info),
         "is_announce" (only admins can send messages)}

    Note: This is the way to read back what update_group_settings wrote.
    get_group_invite_info cannot be used for that — the invite-link response
    carries no locked/announce data, so it always reports both as false.
    """
    success, message, info = whatsapp_get_group_info(jid, account=account)
    result: Dict[str, Any] = {"success": success, "message": message}
    if info:
        result.update(info)
    return result


@mcp.tool()
def archive_chat(chat_jid: str, archive: bool = True, account: Optional[str] = None) -> Dict[str, Any]:
    """Archive or unarchive a WhatsApp chat (app-state sync — affects WhatsApp app).

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        archive: True to archive, False to unarchive
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_archive_chat(chat_jid, archive, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def resolve_contact(phone: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a phone number to its WhatsApp JIDs (regular + LID, if any).

    Args:
        phone: Phone number to resolve (e.g. 5511999999999)
        account: Optional account alias to use (defaults to primary account)
    """
    success, message, jids = whatsapp_resolve_contact(phone, account=account)
    return {"success": success, "message": message, "jids": jids}


@mcp.tool()
def react_to_message(
    chat_jid: str,
    message_id: str,
    emoji: str,
    from_me: bool = True,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """React to a WhatsApp message with an emoji.

    Passing an empty emoji string removes the existing reaction.

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        message_id: The ID of the message to react to
        emoji: The emoji to react with, or "" to remove the reaction
        from_me: Whether the target message was sent by you (defaults True, your own
            message). Setting from_me=False works only in direct chats; in group
            chats it returns an error because the original sender's JID isn't available.
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_react_to_message(chat_jid, message_id, emoji, from_me=from_me, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def edit_message(
    chat_jid: str,
    message_id: str,
    new_text: str,
    from_me: bool = True,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Edit the text of a previously sent WhatsApp message.

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        message_id: The ID of the message to edit
        new_text: The new message text
        from_me: Accepted for API symmetry with react_to_message/delete_message but
            ignored — WhatsApp only allows editing your own messages, and the bridge
            never reads this param for /api/edit.
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_edit_message(chat_jid, message_id, new_text, from_me=from_me, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def delete_message(
    chat_jid: str,
    message_id: str,
    from_me: bool = True,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Delete a WhatsApp message for everyone (revoke).

    Args:
        chat_jid: The JID of the chat (e.g. 5511999999999@s.whatsapp.net or group@g.us)
        message_id: The ID of the message to delete
        from_me: Whether the target message was sent by you (defaults True, your own
            message). Setting from_me=False works only in direct chats; in group
            chats it returns an error because the original sender's JID isn't available.
        account: Optional account alias to use (defaults to primary account)
    """
    success, message = whatsapp_delete_message(chat_jid, message_id, from_me=from_me, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def update_group_participants(
    group_jid: str,
    participants: List[str],
    action: str,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Update WhatsApp group participants (add/remove/promote/demote).

    Args:
        group_jid: The JID of the group (must end with @g.us)
        participants: List of phone numbers or JIDs to modify. International format recommended
            (e.g., 5562123456789 or 5562123456789@s.whatsapp.net)
        action: The action to perform: "add" (invite), "remove" (remove from group),
            "promote" (make admin), or "demote" (remove admin). Requires you to be a
            group admin for most actions.
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool (call accepted by WhatsApp, not all participants applied),
            "message": str (summary),
            "participants": [ {"jid": str, "is_admin": bool, "error": int, "add_request"?: bool}, ... ]
        }

    Note: Each participant in the response has error code 0 if applied, non-0 if rejected
    (e.g. 403 privacy, 409 already member). add_request field is present when action="add"
    and the target has privacy settings blocking group invites — the invitation becomes pending.
    """
    success, message, participants = whatsapp_update_group_participants(
        group_jid, participants, action, account=account
    )
    return {
        "success": success,
        "message": message,
        "participants": participants
    }


@mcp.tool()
def send_chat_presence(
    chat_jid: str,
    state: str,
    media: str = "",
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Send typing or recording indicator in a WhatsApp chat (ephemeral, no persistence).

    Args:
        chat_jid: The JID of the chat (direct or group)
        state: "composing" (typing indicator) or "paused" (stopped typing)
        media: "" (text, default) or "audio" (recording indicator)
        account: Optional account alias to use (defaults to primary account)

    Guidance:
        - Send "composing" before replying to show the recipient you're typing.
        - Always send "paused" after you stop (or wait ~10 seconds for WhatsApp to auto-expire).
        - If you send "composing" for "audio", the recipient sees a recording indicator.
        - The indicator is ephemeral and not stored in the chat history.
    """
    success, message = whatsapp_send_chat_presence(chat_jid, state, media, account=account)
    return {"success": success, "message": message}


@mcp.tool()
def check_whatsapp(phones: List[str], account: Optional[str] = None) -> Dict[str, Any]:
    """Check if phone numbers are registered on WhatsApp.

    Args:
        phones: List of phone numbers to check (international format, with or without +).
            Digits only after normalization (8-15 digits); max 50 numbers per call.
            Small batches recommended to avoid rate-limiting.
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str (summary),
            "results": [
                {
                    "query": str (normalized input),
                    "jid": str (WhatsApp JID if found, empty otherwise),
                    "is_in": bool (true if registered on WhatsApp),
                    "verified_name"?: str (business name if business account)
                },
                ...
            ]
        }

    Guidance:
        - Always validate new recipient numbers before calling send_message.
        - Numbers not on WhatsApp return is_in=false (no error).
        - Business accounts include a verified_name field (company or brand name).
        - Use small batches and avoid mass scanning to prevent rate-limiting or account restrictions.
    """
    success, message, results = whatsapp_check_whatsapp(phones, account=account)
    return {
        "success": success,
        "message": message,
        "results": results
    }




@mcp.tool()
def get_group_invite_link(group_jid: str, reset: bool = False, account: Optional[str] = None) -> Dict[str, Any]:
    """Get invite link for a WhatsApp group.

    Args:
        group_jid: The JID of the group (must end with @g.us)
        reset: If True, revoke the old link; anyone with it loses access
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {"success": bool, "message": str, "link": str}

    Note: reset=True revokes the old link; anyone with it loses access.
    """
    success, message, link = whatsapp_get_group_invite_link(group_jid, reset, account=account)
    return {"success": success, "message": message, "link": link}


@mcp.tool()
def get_group_invite_info(link: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Get group information from an invite link.

    Args:
        link: The group invite link (full URL or code only)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str,
            "jid"?: str,
            "name"?: str,
            "topic"?: str,
            "participants"?: [str]
        }

    Note: Read-only — this does NOT join the group. Use it before
    join_group_with_link to see what the link points to.

    Note: No locked/announce data here — WhatsApp does not include those flags
    in an invite-link lookup. Read them with get_group_info instead.
    """
    success, message, info = whatsapp_get_group_invite_info(link, account=account)
    result = {"success": success, "message": message}
    if info:
        result.update(info)
    return result


@mcp.tool()
def join_group_with_link(link: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Join a WhatsApp group using an invite link.

    Args:
        link: The group invite link (full URL or code only)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {"success": bool, "message": str, "jid"?: str}

    Note: This actually joins. If the group requires admin approval, the call
    succeeds as a pending membership request rather than immediate membership.
    """
    success, message, jid = whatsapp_join_group_with_link(link, account=account)
    return {"success": success, "message": message, "jid": jid}


@mcp.tool()
def update_group_settings(
    group_jid: str,
    name: Optional[str] = None,
    topic: Optional[str] = None,
    announce: Optional[bool] = None,
    locked: Optional[bool] = None,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Update WhatsApp group settings.

    Args:
        group_jid: The JID of the group (must end with @g.us)
        name: New group name (or None to skip)
        topic: New group topic (or None to skip; "" to clear)
        announce: True=only admins post, False=everyone posts (or None to skip)
        locked: True=closed group, False=open (or None to skip)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool (true only if ALL requested fields applied),
            "message": str,
            "results"?: [{"field": str, "success": bool, "error"?: str}, ...]
        }

    Note: An omitted field is left unchanged, so topic="" is not "skip" — it
    CLEARS the group topic. The result is per field and can be partial: one
    field may fail (e.g. not an admin) while the others apply, so check
    results[] rather than success alone. Most fields require you to be admin.
    """
    success, message, results = whatsapp_update_group_settings(group_jid, name, topic, announce, locked, account=account)
    return {"success": success, "message": message, "results": results}


@mcp.tool()
def set_group_photo(group_jid: str, media_path: str = "", remove: bool = False, account: Optional[str] = None) -> Dict[str, Any]:
    """Set or remove the group profile photo.

    Args:
        group_jid: The JID of the group (must end with @g.us)
        media_path: Path to JPEG file on bridge host (ignored if remove=True)
        remove: If True, remove the current photo
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {"success": bool, "message": str, "picture_id"?: str}

    Note: media_path is read by the bridge process, so it must exist on the
    bridge's host filesystem, not the caller's. JPEG only. remove=True clears
    the photo and ignores media_path.
    """
    success, message, picture_id = whatsapp_set_group_photo(group_jid, media_path, remove, account=account)
    return {"success": success, "message": message, "picture_id": picture_id}


@mcp.tool()
def get_user_info(jids: List[str], account: Optional[str] = None) -> Dict[str, Any]:
    """Get user information for one or more WhatsApp users.

    Args:
        jids: List of JIDs or phone numbers (max 20 per call)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str,
            "results"?: [
                {
                    "query": str (parsed JID),
                    "jid"?: str,
                    "found": bool,
                    "status"?: str,
                    "picture_id"?: str,
                    "verified_name"?: str,
                    "lid"?: str,
                    "devices"?: [str]
                },
                ...
            ]
        }

    Note: Max 20 per call.

    Note: found=true does NOT mean the number is registered on WhatsApp — it
    only means WhatsApp returned a node for it. An unregistered number comes
    back found=true with an empty status and devices=[]. To check whether a
    number actually has WhatsApp, use check_whatsapp, which answers that
    directly. Observed behavior, not inferred.
    """
    success, message, results = whatsapp_get_user_info(jids, account=account)
    return {"success": success, "message": message, "results": results}


@mcp.tool()
def get_profile_picture(jid: str, preview: bool = False, account: Optional[str] = None) -> Dict[str, Any]:
    """Get profile picture information for a user or group.

    Args:
        jid: JID of user or group
        preview: If True, get a smaller preview image
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str,
            "url"?: str (download URL, not the image itself),
            "id"?: str,
            "type"?: str,
            "direct_path"?: str
        }

    Note: Returns URL for download, not the image; success=false is normal when
    no photo or hidden by privacy; accepts group JID.
    """
    success, message, info = whatsapp_get_profile_picture(jid, preview, account=account)
    result = {"success": success, "message": message}
    if info:
        result.update(info)
    return result


@mcp.tool()
def create_poll(
    chat_jid: str,
    question: str,
    options: List[str],
    selectable_count: int = 1,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Create a poll in a chat.

    Args:
        chat_jid: The JID of the chat where the poll will be created
        question: The poll question/prompt
        options: List of poll options (must be 2–12 unique, non-empty option names)
        selectable_count: Number of options the voter can select (must be between 1
            and the number of options; default 1)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str,
            "message_id"?: str (ID of the created poll message)
        }
    """
    success, message, message_id = whatsapp_create_poll(
        chat_jid, question, options, selectable_count, account=account
    )
    return {
        "success": success,
        "message": message,
        "message_id": message_id,
    }


@mcp.tool()
def vote_in_poll(
    chat_jid: str,
    poll_id: str,
    options: List[str],
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Vote in a poll.

    Args:
        chat_jid: The JID of the chat containing the poll
        poll_id: The id of the poll message (from create_poll's message_id)
        options: List of selected option names. Pass an empty list to remove your vote.
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str
        }
    """
    success, message = whatsapp_vote_in_poll(chat_jid, poll_id, options, account=account)
    return {
        "success": success,
        "message": message,
    }


@mcp.tool()
def get_poll_results(
    chat_jid: str,
    poll_id: str,
    account: Optional[str] = None
) -> Dict[str, Any]:
    """Get poll results.

    Args:
        chat_jid: The JID of the chat containing the poll
        poll_id: The id of the poll message (from create_poll's message_id)
        account: Optional account alias to use (defaults to primary account)

    Returns:
        {
            "success": bool,
            "message": str,
            "question"?: str (the poll question),
            "results"?: [{"option": str, "count": int, "voters"?: [str]}, ...],
            "total_voters"?: int (unique voters who have at least one option
                selected; someone who withdrew their vote is not counted, so
                this never exceeds the sum of the per-option counts),
            "unresolved_votes"?: int (number of votes for which options were unknown)
        }

    Important: The result is what this bridge saw arrive. WhatsApp offers no API to
    query poll votes directly. A vote received while the bridge was offline is not here
    — and there is no way to detect that it was missed. Never present this as an official
    tally. unresolved_votes > 0 means votes from polls whose option names this bridge
    does not know.
    """
    success, message, results = whatsapp_get_poll_results(chat_jid, poll_id, account=account)
    response = {
        "success": success,
        "message": message,
    }
    if results:
        response.update(results)
    return response


@mcp.tool()
def get_bridge_status(account: Optional[str] = None) -> Dict[str, Any]:
    """Get WhatsApp bridge health status.

    Returns the bridge connection state (connected/logged_in) and automatically
    determined health status, plus runtime watchdog state.

    Key insight: `healthy:false` with reason "not logged in" means you need to
    scan the QR code at /qr — reconnecting won't help. If the bridge is
    unreachable (transport error), that's a different problem: the process is down,
    not recoverable via API.

    D13: When called without account and multiple accounts are configured,
    returns aggregated status for all accounts.

    Args:
        account: Optional account alias to use (defaults to primary account)

    Returns:
        Single-account format (when account specified or single account):
        {
            "success": bool,
            "healthy": bool (connected AND logged_in),
            "reason": str (why not healthy, or empty if healthy),
            "connected": bool,
            "logged_in": bool,
            "jid": str (your WhatsApp JID, or empty if not logged in),
            "last_successful_connect": str (RFC3339, or omitted if never connected),
            "auto_reconnect_errors": int,
            "last_event_at": str (RFC3339, or omitted if no event yet),
            "uptime_seconds": int,
            "watchdog": {
                "interval_seconds": int,
                "reconnects": int,
                "last_action": str ("none", "reconnect", "logged-out", or omitted),
                "last_action_at": str (RFC3339, or omitted)
            }
        }
        
        Multi-account aggregated format (when no account specified, multiple configured):
        {
            "aggregated": true,
            "accounts": {
                "<alias>": { status object for that account },
                ...
            }
        }
    """
    healthy, reason, status = whatsapp_get_bridge_status(account=account)
    
    # Check if this is aggregated response (D13)
    if reason == "aggregated" and isinstance(status, dict):
        # Multi-account aggregated response
        return {
            "aggregated": True,
            "accounts": status
        }
    
    # Single account response
    if status is None:
        return {
            "success": False,
            "healthy": False,
            "reason": reason,
            "connected": False,
            "logged_in": False,
            "auto_reconnect_errors": 0,
            "uptime_seconds": 0,
            "watchdog": {
                "interval_seconds": 0,
                "reconnects": 0
            }
        }
    return status


@mcp.tool()
def pair_account(account: str) -> Dict[str, Any]:
    """Get the QR code PNG for pairing a registered account.

    Implements D4: Tool MCP that returns the QR code for an already-registered
    account. The account alias is required (pairing the wrong account is worse
    than an argument error). If the alias doesn't exist in the map, the error
    says to run the installer.

    Task 10 (D8, D3): Include the account alias and port in the response so the
    caller knows which account and port the QR is for.

    Args:
        account: Account alias to pair (required). Must be a registered account
                created by install.ps1 -AddAccount

    Returns:
        A dictionary with:
        - "success": True if QR retrieved, False if already paired
        - "account": The account alias (included in task 10, D8, D3)
        - "port": The port the bridge is running on (included in task 10, D8, D3)
        - "qr_png": The raw PNG bytes (base64 encoded) if success=True
        - "message": Status message

    Raises:
        ValueError: If account not registered, bridge offline, or other errors
    """
    try:
        result = whatsapp_pair_account(account)

        # result is now a dict with account, port, qr_png_bytes
        # Encode PNG bytes as base64 for JSON transmission
        import base64
        qr_base64 = base64.b64encode(result["qr_png_bytes"]).decode('utf-8')

        return {
            "success": True,
            "account": result["account"],
            "port": result["port"],
            "message": f"QR code for account '{account}'",
            "qr_png": qr_base64
        }
    except ValueError as e:
        # Handle specific error cases
        error_msg = str(e)

        # Check if it's an "already paired" message
        if "already paired" in error_msg:
            return {
                "success": False,
                "account": account,
                "message": error_msg
            }

        # Other errors (not found, offline, etc.) should be raised
        raise


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
