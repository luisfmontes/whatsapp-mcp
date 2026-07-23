# Contexto: Deploy / Operação

## Host Linux (VM de dev) vs macOS

- Doc da seção launchd descreve o setup **macOS** do Rodrigo. Esta VM roda **Linux** — NÃO tem launchd.
- Bridge roda como **systemd system-service**: `/etc/systemd/system/whatsapp-bridge-dev1.service` (enabled, `Restart=always`, `RestartSec=5`, `WHATSAPP_BRIDGE_PORT=8081`, ExecStart = binário direto).
- **Reiniciar sem sudo:** `systemctl restart` precisa root (não temos). Workaround: `kill <pid>` do `/whatsapp-bridge` → systemd religa em ~5s sozinho. Confirmar: `pgrep -f '/whatsapp-bridge$'`. `systemctl --user` falha (é system-scope, não user).
- Engine `local` (whisper.cpp) NÃO funciona aqui (paths `/Users/rodrigo/...` macOS, sem binário). Usar **engine `api` Groq** — ver transcription.env + `transcription.md`. Env vem de `transcription.systemd.env` (EnvironmentFile do systemd, gitignored, contém Groq key).
- Porta é **8081** (service seta `WHATSAPP_BRIDGE_PORT=8081`). `transcribe.py` default 8080 → backfill manual precisa `WHATSAPP_API_BASE_URL=http://localhost:8081/api`.
- Listen: `ss -ltnp | grep 8081`. Verificar processos: `pgrep -fa whatsapp-bridge`.

## launchd (serviço persistente — macOS)

- Agent: `~/Library/LaunchAgents/com.whatsapp-mcp.bridge.plist`. **`RunAtLoad=true` + `KeepAlive=true`** (arranca no login, reinicia se cair, ThrottleInterval 10s).
- Roda do clone real `/Users/rodrigo/git/whatsapp-mcp/whatsapp-bridge` (NÃO de `~/.whatsapp-mcp`). Executa `start-bridge.sh`.
- **launchd NÃO herda `export` do shell** → env de transcrição vem de `transcription.env` (sourced por start-bridge.sh). Plist seta `WHATSAPP_BRIDGE_LOG` → `bridge.log`.
- Controle:
  - Parar: `launchctl bootout gui/$(id -u)/com.whatsapp-mcp.bridge`
  - Iniciar: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.whatsapp-mcp.bridge.plist`
  - Status: `launchctl print gui/$(id -u)/com.whatsapp-mcp.bridge`

## start-bridge.sh

- `cd` próprio dir, source `transcription.env` opcional, `WHATSAPP_BRIDGE_PORT=8081 exec ./whatsapp-bridge`.
- `transcription.env` é **gitignored** (path pessoal + prompt).

## Build

- `cd whatsapp-bridge && go build -o whatsapp-bridge .` — recompilar após mudar main.go (binário stale não tem fixes).
- `go run main.go` = dev (binário temp em /var/folders, morre ao fechar terminal). Loga no terminal, NÃO em arquivo → recover_audios não funciona com go run.
- Matar órfãos de porta: `lsof -ti TCP:8081 | xargs kill`. `go run` spawna child `main` que sobrevive ao kill do pai.

## install.sh (para outros)

- Gate Go 1.25+ (casa com go.mod). Clona em `~/.whatsapp-mcp`, compila, escreve config Claude/Cursor, cria start-bridge.sh + plist (RunAtLoad/KeepAlive=false por padrão lá).
- One-line: `curl -fsSL https://raw.githubusercontent.com/rodrigopg/whatsapp-mcp/main/install.sh | bash`.
- Flags: `--service` cria unit systemd **user** `whatsapp-bridge` (Linux) / launchd KeepAlive (macOS); `--codex` registra o MCP no `~/.codex/config.toml`.

## Plugin Claude Code

- Repo também é distribuído como plugin: `.claude-plugin/plugin.json` + `commands/setup.md`, via marketplace `rodrigopg/claude-plugins` (`/plugin install whatsapp-mcp@rodrigopg`).
- Coexistência nesta VM: `install.sh --service` cria unit systemd **user**, mas AQUI a bridge já roda como serviço **system** na porta 8081 — o guard do install.sh detecta bridge ativa e pula o setup de serviço (não duplica).

## QR / auth

- `http://localhost:8081/qr` no browser (PNG raw em `/qr.png`). Salva `/tmp/whatsapp-qr.png`. QR renova a cada ~20s.
- **Re-parear:** QR só é emitido no startup quando `client.Store.ID == nil` (sem sessão em `whatsapp.db`). Logout remoto apaga a sessão mas NÃO re-entra no loop de QR em runtime — `/qr` fica preso em "connected". Precisa reiniciar o processo (Linux: `kill <pid>`, systemd religa). Logout pelo celular já zera `whatsmeow_device`; aí o bridge sobe limpo e gera QR.
- History sync ao parear é limitado (poucas msgs/conversas recentes), não histórico completo — normal do multi-device. Backup `messages.db` antes (INSERT OR REPLACE pode sobrescrever).

## DNS GitHub (rede do Rodrigo)

- `api.github.com` resolve globalmente pra `4.228.31.149` (Azure) — **inalcançável desta rede** (timeout). IPs legados `140.82.x` (range oficial GitHub) roteiam OK.
- Fix: pin em `/etc/hosts` (bloco demarcado `claude-code: GitHub pin`): api→140.82.112.6, github→140.82.112.3, codeload→140.82.112.9. Remover bloco reverte. Bloqueio é da rede/ISP, não DNS local.

## Git

- Remotes: `rodrigopg` = fork (push aqui), `origin` = lharries upstream. main rastreia `rodrigopg/main`.
