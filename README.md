# WhatsApp MCP Server (Community Fork)

> **Why this fork?** The chain is [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) — frozen since early 2025, with 30+ open PRs carrying critical fixes (broken whatsmeow API, LID contact migration, security hardening) left unmerged — then [rodrigopg/whatsapp-mcp](https://github.com/rodrigopg/whatsapp-mcp), which cherry-picked the best of those PRs, and then this fork, which adds **native Windows support** (no WSL), a session health check with a **reconnection watchdog**, and media download fixes. Improvements from `rodrigopg` are merged in as they land.

This is a Model Context Protocol (MCP) server for WhatsApp.

With this you can search and read your personal WhatsApp messages (including images, videos, documents, and audio messages), search your contacts and send messages to either individuals or groups. You can also create and leave groups.

It connects to your **personal WhatsApp account** directly via the WhatsApp web multidevice API (using the [whatsmeow](https://github.com/tulir/whatsmeow) library). All your messages are stored locally in a SQLite database and only sent to an LLM (such as Claude) when the agent accesses them through tools (which you control).

> *Caution:* as with many MCP servers, the WhatsApp MCP is subject to [the lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/). Prompt injection could lead to private data exfiltration.

---

## What's new in this fork

### Bug fixes & compatibility
- **whatsmeow API updated** — the upstream bridge broke when whatsmeow added `context.Context` to all API calls. Fixed across all call sites.
- **PNG QR fallback** — QR code is saved to `/tmp/whatsapp-qr.png` and opened automatically on macOS if the terminal rendering is hard to scan.

### Security
- **REST API bound to `127.0.0.1` by default** — the upstream bound to `0.0.0.0`, meaning anyone on the same LAN could send messages as you. Set `BIND_ADDR=<ip>` to opt into wider exposure (`0.0.0.0`, or a specific interface like a Tailscale address). If `BIND_ADDR` is not loopback, `API_AUTH_TOKEN` becomes mandatory — the bridge refuses to start without it, and every `/api/*` request must send `Authorization: Bearer <token>`.

### Contact name resolution (LID migration)
WhatsApp has been migrating contacts from phone-based JIDs (`+55...@s.whatsapp.net`) to internal LID JIDs (`xxx@lid`) for privacy. This broke contact search, `get_direct_chat_by_contact`, and `list_messages` in the upstream. Fixed with:
- `resolvePhoneToJIDs` (Go bridge) — looks up all JID variants (PN + LID) for a phone number via `whatsapp.db`, exposed to the MCP server through `POST /api/chat/by_contact`
- `POST /api/contacts/search` searches `whatsmeow_contacts` in `whatsapp.db` first (real names + LID contacts), falling back to `messages.db`
- `get_direct_chat_by_contact` resolves LID JIDs to phone numbers correctly (bridge-side, via the endpoint above)
- `resolveToPN` in the Go bridge normalizes LID→PN at write time so the same contact never splits across two `chat_jid` values
- `migrateLIDChats` runs at startup and merges any existing `@lid` chat rows into their `@s.whatsapp.net` equivalents (transactional, idempotent)

### Contact name display
- New `senders` table in `messages.db` stores `full_name`, `push_name`, `business_name` per sender
- `SyncAllContacts` bulk-upserts the whatsmeow contact store into `senders` on connect and after each history sync
- Incoming messages enrich `senders` with `PushName` + contact store data
- Terminal log now shows human-readable names instead of raw phone numbers
- `GetChatName` checks the local `senders` table before hitting the live contact store

### Outbound message persistence
- Sent text messages are stored locally immediately so your own sends appear in the conversation history without waiting for a multi-device echo (which doesn't fire on single-device accounts)

### Media improvements
- Document uploads now use stdlib MIME detection instead of hardcoding `application/octet-stream`
- `FileName` field set on `DocumentMessage` so files display correctly in WhatsApp

### Group management (new MCP tools)
- **`create_group`** — create a new WhatsApp group with a name and participants; optionally create as a community parent or sub-group
- **`leave_group`** — leave a group by JID

### Audio transcription (opt-in)
Voice messages are stored with empty searchable text by default. Enable transcription to turn them into searchable `content`, written back into the messages DB so the normal (accent-insensitive) search finds them. A 5-minute sweep in the bridge transcribes new audios automatically; `transcribe.py` backfills existing ones, and `recover_audios.py` recovers media that expired from WhatsApp's CDN (via media retry — needs your phone online).

**Transcription is off until you configure an engine.** With nothing set, the sweep is a clean no-op — it never marks audios, so enabling later still picks them all up. Pick one engine:

- **Local (whisper.cpp)** — private, no per-call cost, needs a compiled [whisper.cpp](https://github.com/ggml-org/whisper.cpp) build + a model + `ffmpeg`:
  ```sh
  export TRANSCRIPTION_ENGINE=local
  export WHISPER_CLI=/path/to/whisper.cpp/build/bin/whisper-cli
  export WHISPER_MODEL=/path/to/models/ggml-medium.bin
  ```
- **API (OpenAI Whisper, or any OpenAI-compatible endpoint like Groq)** — zero local setup, audio leaves your machine:
  ```sh
  export TRANSCRIPTION_ENGINE=api
  export TRANSCRIPTION_API_KEY=sk-...           # OpenAI
  # Groq instead:
  #   TRANSCRIPTION_API_KEY=gsk_...
  #   TRANSCRIPTION_API_BASE=https://api.groq.com/openai/v1
  #   TRANSCRIPTION_API_MODEL=whisper-large-v3
  ```

Optional: `TRANSCRIPTION_PROMPT` biases both engines toward correct punctuation and domain terms.

**Where these vars must live** depends on how the bridge starts — the 5-minute sweep inherits the bridge process's environment, *not* your interactive shell's:
- **`go run main.go` / running `./whatsapp-bridge` by hand** — `export` them in the same shell first, or prefix the command.
- **`start-bridge.sh` (from `install.sh`) or macOS launchd auto-start** — `export` in your shell will **not** reach launchd. Instead create `~/.whatsapp-mcp/transcription.env` with the vars (one `export VAR=value` per line); `start-bridge.sh` sources it, so both the manual launcher and launchd pick them up.

Once an engine is configured: new voice messages become searchable within ~5 minutes, and **`list_messages` matches their transcribed text** like any other message.

When installed as a Claude Code plugin, run the backfill/recovery scripts from `~/.whatsapp-mcp` (the runtime install), not from the plugin directory.

The backfill/recovery scripts are separate processes that read the engine vars from *their own* shell — so `source ~/.whatsapp-mcp/transcription.env` (or re-`export` the vars) in the shell you run them from; the `transcription.env` you set up for the bridge does not reach them automatically. Then, from `whatsapp-mcp-server/`:
- **`python3 transcribe.py`** — backfill existing audios that are still downloadable.
- **`python3 recover_audios.py`** — for audios that expired from WhatsApp's CDN (shown as `[áudio indisponível…]`); requires your **phone online**. This one scrapes the bridge's log to confirm each re-upload, so the bridge must be logging to a *file* and `WHATSAPP_BRIDGE_LOG` must point at it (a foreground `go run main.go` logs to the terminal, not a file). With the `install.sh` launchd setup the log is at `~/.whatsapp-mcp/bridge.log`, so run `WHATSAPP_BRIDGE_LOG=~/.whatsapp-mcp/bridge.log python3 recover_audios.py`.

### Configuration
- `WHATSAPP_BRIDGE_PORT` env var — change the REST API port (default `8080`)
- `WHATSAPP_API_BASE_URL` env var — point the Python MCP server at a non-default bridge URL
- `WHATSAPP_API_AUTH_TOKEN` env var — bearer token the MCP server sends as `Authorization: Bearer <token>`; required if the bridge's `API_AUTH_TOKEN` is set
- `BIND_ADDR` env var — change the bind address of the REST API (see [Security](#security) above for the auth requirement this triggers)
- `WHATSAPP_WATCHDOG_INTERVAL` env var — seconds between watchdog checks (default `60`, minimum `10`; anything invalid falls back to the default and logs which value is in force)
- `API_AUTH_TOKEN` env var (bridge) — bearer token required on all `/api/*` requests once `BIND_ADDR` is non-loopback
- Transcription env vars — see [Audio transcription](#audio-transcription-opt-in) above

**Running the MCP server against a remote bridge** (e.g. the bridge lives on a VPS/home
server, Claude Code runs on your laptop): the MCP server talks to the bridge over HTTP only —
it doesn't read any local SQLite file — so pointing `WHATSAPP_API_BASE_URL` at a remote host is
enough. Don't bind the bridge's REST API to a public IP without `API_AUTH_TOKEN` set; a private
network (Tailscale, WireGuard, SSH tunnel) plus the token is the recommended setup.

---

## Installation

### Install as a Claude Code plugin

This fork is **not published to a marketplace yet**. The `rodrigopg/claude-plugins` marketplace distributes the *upstream* plugin, which has no Windows support and none of the fixes listed above — installing from there does not give you this fork. Until a marketplace exists, use the one-line installer below: it registers the MCP server directly, which is what the plugin does anyway.

If you do load this repo as a plugin (`.claude-plugin/plugin.json` is here), run `/whatsapp:setup` for guided onboarding: it checks dependencies (Go 1.25+, uv, git), builds and installs the bridge as a service, and walks you through QR pairing. The plugin installs from this repo's `main` branch. The first tool call after install/update may be slow while `uv` resolves dependencies.

---

### One-line install (macOS / Linux / WSL)

```bash
curl -fsSL https://raw.githubusercontent.com/luisfmontes/whatsapp-mcp/main/install.sh | bash
```

The script:
- checks Go 1.25+, Python 3.9+, uv (installs uv if missing)
- clones the repo to `~/.whatsapp-mcp`
- compiles the Go bridge
- writes `claude_desktop_config.json` / `~/.cursor/mcp.json` automatically
- creates a `start-bridge.sh` launcher
- on macOS: writes a launchd plist for optional auto-start

After install, run `~/.whatsapp-mcp/start-bridge.sh`, open **http://localhost:8080/qr** in your browser, scan the QR, then restart Claude Desktop or Cursor.

Optional flags (`bash -s -- --service --codex`):
- `--service` — installs the bridge as a systemd **user** service on Linux (`whatsapp-bridge`, auto-restart, survives logout via linger) or loads the launchd plist with KeepAlive on macOS. If a bridge is already running, service setup is skipped.
- `--codex` — registers the MCP server with the Codex CLI (see below).

---

### Codex CLI

```bash
curl -fsSL https://raw.githubusercontent.com/luisfmontes/whatsapp-mcp/main/install.sh | bash -s -- --codex
```

This writes `[mcp_servers.whatsapp]` into `~/.codex/config.toml`. The write is append-safe: if the key already exists, the file is left untouched and the snippet is printed for manual merge. To configure manually:

```toml
[mcp_servers.whatsapp]
command = "uv"
args = ["--directory", "/path/to/whatsapp-mcp/whatsapp-mcp-server", "run", "main.py"]
env = { WHATSAPP_BRIDGE_PORT = "8080" }
```

---

### Manual install

#### Prerequisites

- Go 1.25+
- Python 3.9+
- Claude Desktop (or Cursor)
- UV: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- FFmpeg — *optional* for sending/converting audio; *required* only if you enable **local** audio transcription (the `local` engine shells out to `ffmpeg`). Not needed for the API engine or core MCP use.

#### Steps

1. **Clone this repository**

   ```bash
   git clone https://github.com/luisfmontes/whatsapp-mcp.git
   cd whatsapp-mcp
   ```

2. **Run the WhatsApp bridge**

   ```bash
   cd whatsapp-bridge
   go run main.go
   ```

   On first run, open **http://localhost:8080/qr** in your browser and scan the QR code with WhatsApp (Settings → Linked Devices → Link a Device). The page auto-refreshes when a new code is generated. The QR is also written to the system temp dir as `whatsapp-qr.png` and opened in the default image viewer (Preview on macOS, the registered handler on Windows, `xdg-open` on Linux). See the Windows section below: 8080 is often unusable there, so that install defaults to 8081.

3. **Connect to the MCP server**

   ```json
   {
     "mcpServers": {
       "whatsapp": {
         "command": "{{PATH_TO_UV}}",
         "args": [
           "--directory",
           "{{PATH_TO_SRC}}/whatsapp-mcp/whatsapp-mcp-server",
           "run",
           "main.py"
         ]
       }
     }
   }
   ```

   - **Claude Desktop**: save as `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Cursor**: save as `~/.cursor/mcp.json`

4. **Restart Claude Desktop / Cursor**

### Windows

No C toolchain needed — MSYS2 is no longer required. On Windows the bridge is
built against [`modernc.org/sqlite`](https://pkg.go.dev/modernc.org/sqlite), a
pure-Go build of SQLite, selected by build tag; macOS and Linux keep using
`mattn/go-sqlite3` (CGO) exactly as before. `CGO_ENABLED=0` produces a
standalone `.exe`.

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Service
```

Flags mirror `install.sh`: `-Service` registers auto-start, `-Codex` registers
the MCP with Codex CLI, plus `-InstallDir`, `-BridgePort`, `-RepoUrl`, `-Branch`.
To build by hand instead:

```powershell
cd whatsapp-bridge
go build -o whatsapp-bridge.exe .
$env:WHATSAPP_BRIDGE_PORT = '8081'
.\whatsapp-bridge.exe
```

Three things differ from macOS/Linux and will bite if ignored:

- **Port.** Windows reserves TCP ranges for Hyper-V/WSL, and the project default
  8080 frequently lands inside one. The bind then fails with *"an attempt was
  made to access a socket in a way forbidden by its access permissions"* — and
  the bridge only logs it and keeps running, so you get a live process with no
  port open. Check with `netsh interface ipv4 show excludedportrange protocol=tcp`
  and pick a port outside those ranges (`install.ps1` does this automatically and
  defaults to 8081).
- **Auto-start** is a Task Scheduler task named `WhatsAppMCPBridge`, triggered at
  logon, running unelevated in your session (no launchd, no systemd). Inspect it
  with `Get-ScheduledTask -TaskName WhatsAppMCPBridge`. Task Scheduler keeps no
  output of its own, so diagnostics live in `bridge.log` in the install dir.
- **ffmpeg** is optional but needed for sending audio and for the local whisper
  engine: `winget install --id Gyan.FFmpeg`. If it is not on `PATH`, point
  `FFMPEG_BIN` at the executable. For transcription on Windows prefer
  `TRANSCRIPTION_ENGINE=api` — the local engine needs a whisper.cpp build present
  on this machine.

The QR is written to `%TEMP%\whatsapp-qr.png` and opened with the default image
viewer, in addition to being served at `/qr`.

### Multi-account (Windows)

Run multiple WhatsApp accounts (personal, work, etc.) on the same Windows machine, each isolated in its own bridge process.

#### Model

- **One bridge process per account**: each account has its own `whatsapp-bridge` binary running on a separate port, storing messages in a separate `store/` directory.
- **One scheduled task per account**: named `WhatsAppMCPBridge-<alias>` (e.g., `WhatsAppMCPBridge-trabalho`), auto-starting with login, managed by Windows Task Scheduler.
- **Single Python MCP server**: all 36 tools gain an optional `account` parameter. Calls with no `account` use the default account; calls with `account="trabalho"` route to that account's bridge.

#### Setup

1. **Create a new account** with `install.ps1 -AddAccount`:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1 -AddAccount trabalho -Service
   ```

   This creates:
   - Directory: `~/.whatsapp-mcp/accounts/trabalho/`
   - Allocated port: automatically chosen (e.g., 3006), avoiding Windows' reserved ranges
   - Entry in `~/.whatsapp-mcp/accounts.json`
   - Launcher and scheduled task (registered if `-Service` is passed)

2. **Pair the account** via the MCP tool `pair_account("trabalho")`:

   ```python
   # In Claude, call the pair_account tool:
   pair_account(account="trabalho")
   # Returns the QR code PNG (base64-encoded)
   # Scan with your phone to link the account
   ```

#### Configuration: `accounts.json`

Lives at `~/.whatsapp-mcp/accounts.json`. Created by the installer, maps account aliases to directories and ports:

```json
{
  "default": "pessoal",
  "accounts": {
    "pessoal": {
      "dir": "C:\\Users\\<seu-usuario>\\.whatsapp-mcp\\whatsapp-bridge",
      "port": 8081,
      "jid": null
    },
    "trabalho": {
      "dir": "C:\\Users\\<seu-usuario>\\.whatsapp-mcp\\accounts\\trabalho",
      "port": 3006,
      "jid": null
    }
  }
}
```

- `default`: which account is used when tools are called without `account` parameter
- `accounts`: map of account aliases to config (dir, port, jid filled after pairing)
- `jid`: set automatically when the account is paired (JID of the linked phone)
- `dir`: working directory for that account's bridge (where `store/` lives)

#### Usage rules

- **Read operations** (e.g., `list_chats`, `list_messages`) default to the account in `"default"`. Pass `account="trabalho"` to read from another account.
- **Write operations** (e.g., `send_message`, `send_file`) **require the `account` parameter if more than one account is configured**. Calling `send_message` without `account` when two accounts exist returns an error naming both: this prevents accidentally sending a work message from your personal account or vice versa.
- If only one account exists (no `accounts.json`), all tools work as before — `account` is optional everywhere.

#### Scheduled tasks and auto-start

Each account gets its own Task Scheduler entry. Inspect them with:

```powershell
Get-ScheduledTask -TaskName "WhatsAppMCPBridge-*"
```

Stop/start a specific account:

```powershell
Stop-ScheduledTask -TaskName "WhatsAppMCPBridge-trabalho"
Start-ScheduledTask -TaskName "WhatsAppMCPBridge-trabalho"
```

#### Transcription configuration

The transcription settings file (`transcription.env`) is **shared by all accounts** — stored at `~/.whatsapp-mcp/transcription.env` and loaded by each account's launcher. This means all accounts use the same transcription engine and API key. See [Audio transcription](#audio-transcription-opt-in) above for configuration.

#### Single-account mode (no multiconta)

If you have only one account, **you don't need `accounts.json`**. The bridge works exactly as before:

- No account registration needed
- All tools work without `account` parameter
- Reads and writes default to `WHATSAPP_BRIDGE_PORT` (8081)
- Multiconta is opt-in; a fresh install is single-account by design

#### Platform support

Multi-account mode is **Windows only** in this version (via `install.ps1 -AddAccount`). The `install.sh` for macOS/Linux does not support `-AddAccount` yet — Linux/macOS users can still run multiple bridges by hand (one per directory, one per port, with separate configs), but the UI for it is not ready.

#### Contributing on Windows

`go build` on Windows never compiles the `!windows` half of the SQLite layer, so
a broken macOS/Linux build passes every local check. Before pushing, type-check
the other platforms — no C toolchain required, since this catches compile errors,
not linkage:

```powershell
$env:GOOS='darwin'; go build -o "$env:TEMP\x" ./...
$env:GOOS='linux';  go build -o "$env:TEMP\x" ./...
Remove-Item env:GOOS
```

---

## Architecture

```
Claude / Cursor
      ↕ MCP (stdio)
Python MCP Server  (whatsapp-mcp-server/)
      ↕ HTTP REST
Go WhatsApp Bridge (whatsapp-bridge/)
      ↕ WhatsApp Web protocol
   WhatsApp servers
```

**Storage** (`whatsapp-bridge/store/`):
- `messages.db` — chats, messages, senders (local SQLite, written by the bridge)
- `whatsapp.db` — whatsmeow session + contact store (written by whatsmeow)

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_contacts` | Search contacts by name or phone number (LID-aware) |
| `list_messages` | Retrieve messages with filters, pagination, context |
| `list_chats` | List chats with metadata |
| `get_chat` | Get info about a specific chat |
| `get_direct_chat_by_contact` | Find a direct chat by phone number (LID-aware) |
| `get_contact_chats` | All chats involving a contact |
| `get_last_interaction` | Most recent message with a contact |
| `get_message_context` | Messages around a specific message |
| `send_message` | Send a text message |
| `send_file` | Send image, video, document, or audio file |
| `send_audio_message` | Send audio as a WhatsApp voice message |
| `download_media` | Download media from a message, get local path |
| `mark_chat_as_read` | Mark a chat as read |
| `mark_chat_as_unread` | Mark a chat as unread |
| `archive_chat` | Archive or unarchive a chat |
| `resolve_contact` | Resolve a phone number or QR link to a JID |
| `react_to_message` | React to a message with an emoji |
| `edit_message` | Edit the text of a message you sent |
| `delete_message` | Delete a message for everyone (revoke) |
| `create_group` | Create a new WhatsApp group |
| `leave_group` | Leave a group |
| `get_group_info` | Group name and participant list |
| `update_group_participants` | Add/remove/promote/demote group members |
| `update_group_settings` | Set group name, topic, announce-only, or locked mode |
| `set_group_photo` | Set or remove the group photo |
| `get_group_invite_link` | Get the group invite link (optionally revoking the old one) |
| `get_group_invite_info` | Inspect an invite link without joining |
| `join_group_with_link` | Join a group from an invite link |
| `get_user_info` | Status, profile picture ID, devices, and verified name |
| `get_profile_picture` | URL of a user's or group's profile picture |
| `send_chat_presence` | Send typing or recording indicators |
| `check_whatsapp` | Check if phone numbers are registered on WhatsApp |
| `get_bridge_status` | Whether the bridge is connected and logged in, without trying to use it |
| `create_poll` | Create a poll in a chat |
| `vote_in_poll` | Vote in a poll (empty selection withdraws the vote) |
| `get_poll_results` | Tally of the votes this bridge has seen |

---

## Troubleshooting

- **QR code not displaying**: terminal QR not working? Check `/tmp/whatsapp-qr.png` (macOS opens it automatically).
- **Contacts showing as numbers**: the bridge syncs names on connect. Give it a few seconds after the "Connected" message.
- **LID contacts not found**: happens when WhatsApp hasn't yet synced the LID→PN mapping locally. Reconnect to trigger a fresh sync.
- **Out of sync / re-pairing**: deleting `whatsapp-bridge/store/whatsapp.db` (or re-scanning the QR for any reason) forces WhatsApp to re-deliver up to a year of history. **This destroys your audio transcriptions** — the re-sync re-inserts every audio row with empty `content`, overwriting transcribed text (the writes use `INSERT OR REPLACE`). Before re-pairing, **back up `whatsapp-bridge/store/messages.db`**. Deleting only `messages.db` does *not* protect transcriptions either: the next sync still arrives empty. After re-pairing you must re-run `transcribe.py` / `recover_audios.py` to rebuild them.
- **Device limit**: WhatsApp limits linked devices. Remove one via Settings → Linked Devices on your phone.
- **Dev clone or custom port**: point the MCP server at your setup via the `WHATSAPP_MESSAGES_DB`, `WHATSAPP_BRIDGE_PORT`, or `WHATSAPP_API_BASE_URL` env vars.

---

## Credits

- Original project: [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)
- Fork this one is based on: [rodrigopg/whatsapp-mcp](https://github.com/rodrigopg/whatsapp-mcp)
- WhatsApp web protocol library: [whatsmeow](https://github.com/tulir/whatsmeow)
- PRs cherry-picked from: #209 (coucaj), #221 (fpto), #239 (jayeshkaithwas), #244 (realitix)
