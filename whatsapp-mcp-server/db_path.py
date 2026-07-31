import os
import sys

_resolved_path = None


def resolve_messages_db():
    """Resolve the messages DB path following the fallback chain:
    1. WHATSAPP_MESSAGES_DB env var (if set, use verbatim)
    2. Repo-relative path (if exists)
    3. ~/.whatsapp-mcp/whatsapp-bridge/store/messages.db (if exists)
    4. Fall back to repo-relative path (error surfaces later on connect)

    Logs the chosen path once to stderr on first resolution.
    """
    global _resolved_path
    if _resolved_path is not None:
        return _resolved_path

    env_path = os.environ.get("WHATSAPP_MESSAGES_DB")
    if env_path:
        _resolved_path = env_path
        print(f"whatsapp-mcp: using messages db at {_resolved_path}", file=sys.stderr)
        return _resolved_path

    repo_relative = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whatsapp-bridge", "store", "messages.db")
    if os.path.exists(repo_relative):
        _resolved_path = repo_relative
        print(f"whatsapp-mcp: using messages db at {_resolved_path}", file=sys.stderr)
        return _resolved_path

    home_path = os.path.expanduser("~/.whatsapp-mcp/whatsapp-bridge/store/messages.db")
    if os.path.exists(home_path):
        _resolved_path = home_path
        print(f"whatsapp-mcp: using messages db at {_resolved_path}", file=sys.stderr)
        return _resolved_path

    _resolved_path = repo_relative
    print(f"whatsapp-mcp: using messages db at {_resolved_path}", file=sys.stderr)
    return _resolved_path
