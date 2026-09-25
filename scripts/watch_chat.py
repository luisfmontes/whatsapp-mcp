"""Watch a chat in the bridge's messages.db and emit one line per burst of
incoming messages.

Zero token while it waits: this only writes to stdout once the other person
sends something and then stops typing for --quiet seconds. It does not exit
after printing a line -- the same person can send several bursts in one
sitting -- and instead exits on its own after --idle seconds with nothing
new at all. State (which message ids were already reported) lives in
--state, so re-arming the watch with the same file neither repeats a
message nor loses one that arrived while nothing was watching.

First run (state file empty or missing) needs a starting point, since there
is no "already reported" history yet:
  --from-now (default): mark everything that already exists as seen, so only
    messages that arrive from this point on fire an event. A message already
    waiting in the chat when the watch starts is silently treated as seen.
  --include-unanswered: only mark as seen what happened up to the chat's own
    last outgoing message (is_from_me=1). Anything the other person sent
    after that -- i.e. still unanswered -- stays unseen, so it fires an
    event on the very first poll instead of being swallowed.

A database read error prints one ERROR line and keeps polling; it never
brings the watch down.

A dead bridge does not look like a database error: messages.db stays
readable, the SELECT just keeps coming back empty, and "nobody answered"
becomes indistinguishable from "the bridge is down". --status-url (the
account's bridge /api/status) closes that gap: every --status-interval
seconds the watch asks the bridge whether it is healthy and prints one
"BRIDGE: desconectada" line when it goes down and one "BRIDGE: conectada"
line when it comes back -- once per transition, never once per check.
Without --status-url nothing touches the network and the output is the
same as before the flag existed.

Usage:
  python watch_chat.py --db <messages.db> --chat <jid> [--chat <jid-lid>] --state <state.json>
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DEFAULT_INTERVAL = 3
DEFAULT_QUIET = 25
DEFAULT_IDLE = 7200
DEFAULT_STATUS_INTERVAL = 60
STATUS_TIMEOUT = 3

# No outgoing message ever recorded for this chat: treat the whole history as
# unanswered rather than refusing to start.
EPOCH_FLOOR = "0000-01-01"


def _db_uri(db_path):
    """Read-only sqlite URI for db_path. Built via pathlib so a Windows
    backslash path round-trips into a valid file: URI (a hand-built
    f"file:{db_path}" string does not)."""
    return pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"


def _select_incoming(db_path, chats, since):
    """Messages received (is_from_me=0) in any of `chats` at or after `since`."""
    conn = sqlite3.connect(_db_uri(db_path), uri=True, timeout=5)
    try:
        placeholders = ",".join("?" * len(chats))
        sql = (
            f"SELECT id, timestamp, media_type, content FROM messages "
            f"WHERE chat_jid IN ({placeholders}) AND is_from_me = 0 AND timestamp >= ? "
            f"ORDER BY timestamp"
        )
        return conn.execute(sql, (*chats, since)).fetchall()
    finally:
        conn.close()


def _last_outgoing_timestamp(db_path, chats):
    """Timestamp of the chat's most recent is_from_me=1 message, or None if
    it never sent one."""
    conn = sqlite3.connect(_db_uri(db_path), uri=True, timeout=5)
    try:
        placeholders = ",".join("?" * len(chats))
        row = conn.execute(
            f"SELECT MAX(timestamp) FROM messages "
            f"WHERE chat_jid IN ({placeholders}) AND is_from_me = 1",
            chats,
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def bridge_health(status_url):
    """(healthy, reason) from the bridge's /api/status. Healthy only on HTTP
    200 with a JSON body whose "healthy" is true; anything else -- refused
    connection, timeout, bad JSON, healthy:false -- is down, with a short
    reason for the BRIDGE line."""
    try:
        with urllib.request.urlopen(status_url, timeout=STATUS_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        # The OS message is long and localized (and mis-encoded on a Windows
        # console); the line only needs which of the two it was.
        cause = getattr(e, "reason", e)
        if isinstance(cause, ConnectionRefusedError):
            return False, "sem resposta: conexão recusada"
        return False, f"sem resposta em {STATUS_TIMEOUT}s"
    except ValueError:
        return False, "resposta sem JSON"
    if isinstance(body, dict) and body.get("healthy") is True:
        return True, ""
    if isinstance(body, dict) and body.get("logged_in") is False:
        return False, "deslogada"
    return False, "healthy:false"


def load_state(state_path):
    try:
        with open(state_path, encoding="utf-8") as f:
            s = json.load(f)
        return s["since"], set(s["seen"]), s.get("last_activity", time.time())
    except (OSError, ValueError, KeyError):
        return None, set(), time.time()


def save_state(state_path, since, seen, last_activity):
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"since": since, "seen": sorted(seen), "last_activity": last_activity}, f)
    os.replace(tmp, state_path)


def seed_state(args):
    """First run only: pick the floor timestamp and the set of message ids
    that count as already seen. See the module docstring for what each mode
    means; this is the fix for the prototype's bug where a message already
    waiting in the chat never fired an event."""
    if args.include_unanswered:
        last_sent = _last_outgoing_timestamp(args.db, args.chat)
        since = last_sent or EPOCH_FLOOR
        seen = set()
    else:
        since = time.strftime("%Y-%m-%d")
        seen = {row[0] for row in _select_incoming(args.db, args.chat, since)}
    return since, seen, time.time()


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Watch a WhatsApp chat and print one line per burst of incoming messages.",
    )
    p.add_argument("--db", required=True, help="path to the bridge's messages.db")
    p.add_argument(
        "--chat", action="append", required=True,
        help="chat_jid to watch; repeat to also cover the @lid form of the same chat",
    )
    p.add_argument("--state", required=True, help="JSON file that survives across re-arms")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between polls")
    p.add_argument(
        "--quiet", type=float, default=DEFAULT_QUIET,
        help="seconds of silence after the last new message that closes a burst into one line",
    )
    p.add_argument(
        "--idle", type=float, default=DEFAULT_IDLE,
        help="seconds with nothing new at all before the watch exits on its own",
    )
    p.add_argument(
        "--status-url",
        help="the account's bridge /api/status; when set, a BRIDGE line reports the bridge going down and coming back",
    )
    p.add_argument(
        "--status-interval", type=float, default=DEFAULT_STATUS_INTERVAL,
        help="seconds between bridge status checks (only with --status-url)",
    )
    seeding = p.add_mutually_exclusive_group()
    seeding.add_argument(
        "--from-now", action="store_true",
        help="first run only: mark everything that already exists as seen (default)",
    )
    seeding.add_argument(
        "--include-unanswered", action="store_true",
        help="first run only: also fire on messages received after the chat's last outgoing message",
    )
    return p.parse_args(argv)


def run(args, out=None):
    out = out or sys.stdout
    since, seen, last_activity = load_state(args.state)
    if since is None:
        since, seen, last_activity = seed_state(args)
        save_state(args.state, since, seen, last_activity)

    pending, last_new, failures = [], 0.0, 0
    # Assume healthy at start: the caller only arms the watch after checking
    # the bridge, so the first line worth printing is the first drop.
    last_healthy, last_status_check = True, None
    while True:
        if args.status_url and (
            last_status_check is None or time.time() - last_status_check >= args.status_interval
        ):
            last_status_check = time.time()
            healthy, reason = bridge_health(args.status_url)
            if healthy != last_healthy:
                if healthy:
                    print("BRIDGE: conectada — a espera voltou a valer", file=out, flush=True)
                else:
                    print(
                        f"BRIDGE: desconectada ({reason}) — a espera está cega: "
                        "mensagem nova não chega enquanto a bridge estiver fora",
                        file=out, flush=True,
                    )
                last_healthy = healthy
        try:
            fresh = [row for row in _select_incoming(args.db, args.chat, since) if row[0] not in seen]
            failures = 0
        except sqlite3.Error as e:
            failures += 1
            if failures in (1, 20):
                print(f"ERROR reading the database ({failures}x): {e}", file=out, flush=True)
            fresh = []
        if fresh:
            pending += fresh
            seen.update(row[0] for row in fresh)
            last_new = last_activity = time.time()
        if pending and time.time() - last_new >= args.quiet:
            summary = " | ".join(
                (f"[{row[2]}]" if row[2] else "") + (row[3] or "")[:80].replace("\n", " ")
                for row in pending
            )
            # Persist BEFORE printing: whoever reads the line may act on it
            # (and re-arm/kill this process) right away, so the ids in this
            # burst must already be durable as "seen" by the time the line
            # reaches stdout -- otherwise a re-arm right after the print can
            # still find them "new" and report the same burst twice.
            save_state(args.state, since, seen, last_activity)
            print(f"NEW MESSAGES ({len(pending)}), last {pending[-1][1]}: {summary}", file=out, flush=True)
            pending = []
        if not pending and time.time() - last_activity >= args.idle:
            save_state(args.state, since, seen, last_activity)
            print(f"END: {int(args.idle // 60)} min with nothing new, watch stopped", file=out, flush=True)
            return 0
        time.sleep(args.interval)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except AttributeError:
        pass  # stdout may already be a plain stream (e.g. under some test harnesses)
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
