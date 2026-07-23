---
description: Set up the WhatsApp MCP bridge — check dependencies, install as a service, pair via QR and smoke-test
---

Set up the WhatsApp bridge for this plugin. Follow the steps in order; do not skip ahead.

## 1. Check dependencies

Verify all three before installing anything. If any is missing, STOP — report which one and its install link. Do not partially install.

- `go version` → must be 1.25 or newer. Missing/old: https://go.dev/dl/
- `uv --version` → any version. Missing: https://docs.astral.sh/uv/getting-started/installation/
- `git --version` → any version. Missing: https://git-scm.com/downloads

## 2. Run the installer with service mode

Prefer the plugin's own copy — it always supports the flags. If `${CLAUDE_PLUGIN_ROOT}/install.sh` exists, run:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/install.sh" --service
```

Otherwise download first and verify the script supports `--service` before executing (an older published copy would silently ignore the flag):

```bash
curl -fsSL https://raw.githubusercontent.com/rodrigopg/whatsapp-mcp/main/install.sh -o /tmp/whatsapp-mcp-install.sh
grep -q -- '--service' /tmp/whatsapp-mcp-install.sh
```

- If the grep matches: `bash /tmp/whatsapp-mcp-install.sh --service`
- If it does NOT match: run `bash /tmp/whatsapp-mcp-install.sh` (no flags), then start the bridge manually with `~/.whatsapp-mcp/start-bridge.sh`, and warn the user that automatic service setup is not available until the published installer updates.

If the installer reports the bridge port/process is already in use, an existing installation is running — do not reinstall; continue to step 4.

After the installer succeeds, pre-warm the MCP server's environment so the first tool call doesn't time out on dependency resolution:

```bash
uv --directory "${CLAUDE_PLUGIN_ROOT}/whatsapp-mcp-server" sync
```

## 3. Pair with WhatsApp

Tell the user to open http://localhost:8080/qr in a browser and scan the QR code with WhatsApp (Settings → Linked Devices → Link a Device). Wait for the user to confirm pairing before continuing.

- If `WHATSAPP_BRIDGE_PORT` is set, use that port in the URL instead of 8080.
- The bridge listens on loopback only by design. On a headless/remote machine, tell the user to port-forward: `ssh -L 8080:localhost:8080 <host>` and open the URL locally.

## 4. Restart the MCP connection

Tell the user to restart Claude Code (or reconnect MCP servers) so the whatsapp MCP server picks up the fresh install — it may have started before the install existed.

## 5. Smoke test

Call one WhatsApp MCP tool — e.g. `search_contacts` with a short query. If it returns data (even an empty list without error), report setup as successful.

If it fails:
- Bridge not running? Check `pgrep -fa whatsapp-bridge` and report the error.
- Bridge running but tools still fail? Likely a port mismatch — set `WHATSAPP_API_BASE_URL` (or `WHATSAPP_BRIDGE_PORT`) in the MCP server's environment to match the bridge's actual port.

## 6. Transcription (optional)

Mention that audio transcription is opt-in and disabled by default; the user can enable it later following the "Audio transcription" section of the repository README.
