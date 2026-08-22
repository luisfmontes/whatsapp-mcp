---
description: Diagnose WhatsApp MCP setup — bridge health, auth, pairing, and which account each server points to
---

Diagnose the WhatsApp MCP setup. Read-only — never restart services, rewrite config, or delete files. Report findings as a checklist, not prose.

## 1. Enumerate configured accounts

Read `~/.claude.json`, list every `mcpServers` key starting with `whatsapp`. For each, extract `command`, `args` (the `--directory` value), and `env` (`WHATSAPP_API_BASE_URL`, `WHATSAPP_API_AUTH_TOKEN` if present).

No `whatsapp*` keys found → report "No WhatsApp MCP server configured — run `/whatsapp-mcp-win:setup`" and stop here.

## 2. Per-account checks

For each configured server, in order:

**a. Install dir exists and has a compiled bridge**
On macOS/Linux: Check `<--directory>/../whatsapp-bridge/whatsapp-bridge` (or the equivalent path from `WHATSAPP_API_BASE_URL`'s install layout) exists and is executable.
On Windows: Check `<--directory>/../whatsapp-bridge/whatsapp-bridge.exe` exists (Windows does not have an executable bit; file existence is sufficient).
Missing → "bridge never built, run install.sh".

**b. Bridge process reachable**
On macOS/Linux: `curl -s -o /dev/null -w "%{http_code}" <WHATSAPP_API_BASE_URL>/qr` (or `/chats` with a POST body `{}` if auth is required — see below). Connection refused/timeout → bridge not running or wrong port. Distinguish these two: `pgrep -fa whatsapp-bridge` tells you if a process exists at all locally; if it does but the port doesn't answer, that's the silent-bind-failure pattern (bridge started before its network interface was ready) — check `ss -tln | grep <port>` and the bridge's own log (`bridge.log` in its install dir) for `bind: cannot assign requested address`.

On Windows: `try { (Invoke-WebRequest -Uri "<WHATSAPP_API_BASE_URL>/qr" -UseBasicParsing -TimeoutSec 5).StatusCode } catch { $_.Exception.Message }` (or POST to `/chats` with `{"limit":1}` body if auth is required). Connection refused/timeout → bridge not running or wrong port. Distinguish these two: `Get-Process whatsapp-bridge -ErrorAction SilentlyContinue` tells you if a process exists locally; if it does but the port doesn't answer, that's the silent-bind-failure pattern. Check `Get-NetTCPConnection -LocalPort <port> -State Listen -ErrorAction SilentlyContinue` — if no result, the bind failed. Check the bridge's own log (`bridge.log` in its install dir) for bind errors. On Windows, also check `netsh interface ipv4 show excludedportrange protocol=tcp` — if the port falls in an excluded range (Hyper-V/WSL reservation), the bridge's bind will fail silently; move `WHATSAPP_BRIDGE_PORT` to a different port.

**b2. Auto-start registered? (only if the bridge is NOT running)**
The mechanism differs per platform: launchd on macOS, a systemd user unit on Linux, and a Task Scheduler task on Windows. On Windows, check `Get-ScheduledTask -TaskName WhatsAppMCPBridge -ErrorAction SilentlyContinue`; no result means auto-start was never registered (re-run `install.ps1 -Service`), while a result with `State` of `Disabled` means it exists but will not fire at logon. A registered task whose bridge is not running usually means the launcher failed early — read `bridge.log` in the install dir, since Task Scheduler itself keeps no output.

**c. Auth, if configured**
If `WHATSAPP_API_AUTH_TOKEN` is set in this server's env, POST to `<base>/chats` with `{"limit":1}` and that bearer token. 401 → token doesn't match the bridge's `API_AUTH_TOKEN`. 200 → auth OK, continue.

**d. Session paired**
If (b) succeeded, POST `<base>/chats` `{"limit":1}`. Empty result with no error could mean "genuinely no chats yet" or "never paired" — check the bridge log for `Connected to WhatsApp` vs a QR-waiting state to disambiguate. Never paired → tell the user to open `<base-without-/api>/qr`.

**e. Identity sanity**
If (d) returned a chat, note the chat name/JID in the report. This is the cross-check for the most dangerous failure mode in this project's history: two account configs silently pointing at the same bridge/port, so both MCP servers return identical data. If you're checking 2+ accounts, compare their (d) results — same JID/chat name across two different server names is a real bug, flag it as CRITICAL, not a warning.

## 3. Report

One line per account, per check, pass/fail/skip. End with a one-line verdict per account ("healthy" / "needs pairing" / "wrong token" / "not running") and, only if something failed, the single next command to run (not a menu of options).
