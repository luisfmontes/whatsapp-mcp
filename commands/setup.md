---
description: Set up the WhatsApp MCP bridge — check dependencies, install as a service, pair via QR and smoke-test
---

Set up the WhatsApp bridge for this plugin. Follow the steps in order; do not skip ahead.

## 0. Detect existing accounts

Read `~/.claude.json` and list any `mcpServers` keys starting with `whatsapp` (e.g. `whatsapp`, `whatsapp-personal`). Also check for existing install dirs: `~/.whatsapp-mcp*`.

- **None found** → this is a first install. Skip ahead to step 1, use defaults (`~/.whatsapp-mcp`, port 8080, server name `whatsapp`).
- **One or more found** → ask the user with AskUserQuestion:
  - Question: "Found existing WhatsApp account(s): `<list the keys/dirs found>`. What do you want to do?"
  - Options: "Add another account" / "Reconfigure/reinstall an existing one" / "Cancel"
  - If **add another account**: ask a follow-up AskUserQuestion for a short label (e.g. "personal", "work") — used to derive the install dir (`~/.whatsapp-mcp-<label>`), port (next free port after 8080, check what's already claimed in `~/.claude.json` envs), and MCP server name (`whatsapp-<label>`). Confirm the derived values with the user in the question itself (put them in the option descriptions) before proceeding.
  - If **reconfigure**: ask which existing account (list them), then run steps 1-6 against that account's existing dir/port instead of picking new ones.
  - If **cancel**: stop here.

Carry the resolved `INSTALL_DIR` / `BRIDGE_PORT` / `SERVER_NAME` from this step through steps 1-6 below — every reference to "the installer" or "the MCP server" means for this specific account.

## 1. Check dependencies

Verify all three before installing anything. If any is missing, STOP — report which one and its install link. Do not partially install.

- `go version` → must be 1.25 or newer. Missing/old: https://go.dev/dl/
- `uv --version` → any version. Missing: https://docs.astral.sh/uv/getting-started/installation/
- `git --version` → any version. Missing: https://git-scm.com/downloads

## 2. Run the installer with service mode

Prefer the plugin's own copy — it always supports the flags. Set `WHATSAPP_MCP_DIR` and `WHATSAPP_BRIDGE_PORT` to the values resolved in step 0. If `${CLAUDE_PLUGIN_ROOT}/install.sh` exists, run:

```bash
WHATSAPP_MCP_DIR="<resolved INSTALL_DIR>" WHATSAPP_BRIDGE_PORT="<resolved BRIDGE_PORT>" \
  bash "${CLAUDE_PLUGIN_ROOT}/install.sh" --service
```

Otherwise download first and verify the script supports `--service` before executing (an older published copy would silently ignore the flag):

```bash
curl -fsSL https://raw.githubusercontent.com/rodrigopg/whatsapp-mcp/main/install.sh -o /tmp/whatsapp-mcp-install.sh
grep -q -- '--service' /tmp/whatsapp-mcp-install.sh
```

- If the grep matches: `WHATSAPP_MCP_DIR="<resolved INSTALL_DIR>" WHATSAPP_BRIDGE_PORT="<resolved BRIDGE_PORT>" bash /tmp/whatsapp-mcp-install.sh --service`
- If it does NOT match: run without `--service` (same env vars), then start the bridge manually with `<INSTALL_DIR>/start-bridge.sh`, and warn the user that automatic service setup is not available until the published installer updates.

If the installer reports this account's bridge port/process is already in use, that account's installation is already running — do not reinstall; continue to step 4.

After the installer succeeds, pre-warm the MCP server's environment so the first tool call doesn't time out on dependency resolution:

```bash
uv --directory "<resolved INSTALL_DIR>/whatsapp-mcp-server" sync
```

## 3. Pair with WhatsApp

Tell the user to open `http://localhost:<resolved BRIDGE_PORT>/qr` in a browser and scan the QR code with WhatsApp (Settings → Linked Devices → Link a Device). Wait for the user to confirm pairing before continuing.

- The bridge listens on loopback only by design. On a headless/remote machine, tell the user to port-forward: `ssh -L <port>:localhost:<port> <host>` and open the URL locally.

## 4. Register the MCP server in Claude Code

`install.sh` writes Claude Desktop/Cursor config automatically, but **not** `~/.claude.json` (Claude Code's own config) — that entry must be added here.

Read `~/.claude.json`, add (or update, if reconfiguring) an entry under `mcpServers` using `<resolved SERVER_NAME>` as the key:

```json
"<SERVER_NAME>": {
  "command": "uv",
  "args": ["--directory", "<resolved INSTALL_DIR>/whatsapp-mcp-server", "run", "main.py"],
  "env": { "WHATSAPP_API_BASE_URL": "http://localhost:<resolved BRIDGE_PORT>/api" }
}
```

Preserve every other existing entry in `mcpServers` — only add/replace this one key. Then tell the user to restart Claude Code (or reconnect MCP servers) so the server picks up the fresh install.

## 5. Smoke test

Call `mcp__<SERVER_NAME>__search_contacts` with a short query. If it returns data (even an empty list without error), report setup as successful.

If it fails:
- Bridge not running? Check `pgrep -fa whatsapp-bridge` and report the error.
- Bridge running but tools still fail? Likely a port mismatch — confirm `WHATSAPP_API_BASE_URL` in this server's `~/.claude.json` env matches the bridge's actual port.
- Multiple accounts and this smoke test returns the wrong data (e.g. same chats as another account)? Do NOT proceed — that means two accounts are pointing at the same bridge port or install dir. Re-check step 0's resolved values before continuing.

## 6. Transcription (optional)

Mention that audio transcription is opt-in and disabled by default; the user can enable it later following the "Audio transcription" section of the repository README.
