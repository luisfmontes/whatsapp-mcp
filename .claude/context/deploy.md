# Contexto: Deploy / Operação

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

## QR / auth

- `http://localhost:8081/qr` no browser. macOS salva `/tmp/whatsapp-qr.png` e abre no Preview.

## DNS GitHub (rede do Rodrigo)

- `api.github.com` resolve globalmente pra `4.228.31.149` (Azure) — **inalcançável desta rede** (timeout). IPs legados `140.82.x` (range oficial GitHub) roteiam OK.
- Fix: pin em `/etc/hosts` (bloco demarcado `claude-code: GitHub pin`): api→140.82.112.6, github→140.82.112.3, codeload→140.82.112.9. Remover bloco reverte. Bloqueio é da rede/ISP, não DNS local.

## Git

- Remotes: `rodrigopg` = fork (push aqui), `origin` = lharries upstream. main rastreia `rodrigopg/main`.
