# WhatsApp MCP Server (community fork)

MCP server pra WhatsApp pessoal. Bridge Go/whatsmeow (`whatsapp-bridge/`) + servidor MCP Python (`whatsapp-mcp-server/`), SQLite local, transcrição de áudio opt-in.

## Carregar contexto antes de trabalhar

| Área | Arquivo |
|------|---------|
| Bridge Go (whatsmeow, REST, history sync, mídia, segurança) | `.claude/context/bridge-go.md` |
| Servidor MCP Python (tools, busca, LID) | `.claude/context/mcp-python.md` |
| Transcrição (whisper local/API, backfill, recovery, sweep) | `.claude/context/transcription.md` |
| Deploy/operação (VPS systemd, launchd local, build, install.sh, DNS, git) | `.claude/context/deploy.md` |

Tarefa multi-área: leia os arquivos relevantes em paralelo.

## Regras inegociáveis

- Go **1.25+** (casa com go.mod); recompilar binário após mudar main.go — `go run` é só dev. CGO impede cross-compile do macOS pra Linux/VPS — compilar direto na VPS (tem Go+gcc lá).
- REST API liga **127.0.0.1** por padrão; `BIND_ADDR=<ip>` pra expor além de loopback exige `API_AUTH_TOKEN` setado (a bridge recusa subir sem token nesse caso) — nunca bind não-loopback sem token, mesmo em rede privada (Tailscale/VPN não é substituto de auth).
- `safeMediaPath` **rejeita** componentes com separadores/`..` — não sanitizar silenciosamente.
- Re-parear/re-sync **apaga transcrições** (INSERT OR REPLACE em messages) — backup `messages.db` antes.
- Transcrição é **opt-in**; sem engine, sweep deve ser no-op (não marcar áudios).
- `transcription.env` **nunca** commitado (gitignored).
- Push em `rodrigopg` (fork), não `origin` (upstream).

## Checklist antes de abrir PR

- [ ] `go build -o whatsapp-bridge .` compila; `go test ./...` passa.
- [ ] `python3 -m unittest test_transcribe -v` passa.
- [ ] Mudou main.go? Binário recompilado e bridge reiniciada — local: `launchctl kickstart -k`; VPS: `go build` na própria VPS (CGO não cross-compila) + `systemctl restart whatsapp-bridge-*`.
- [ ] Sem path pessoal/secret vazando (transcription.env, API keys).
- [ ] Mudou comportamento de sync/escrita? Conferir impacto em transcrições existentes.
- [ ] README/install.sh coerentes se mudou onboarding (versão Go, env vars, troubleshooting).
- [ ] PR contra `rodrigopg/main`.

## Comandos essenciais

```bash
# build + test
cd whatsapp-bridge && go build -o whatsapp-bridge . && go test ./...
cd whatsapp-mcp-server && python3 -m unittest test_transcribe -v

# serviço (macOS launchd)
launchctl print gui/$(id -u)/com.whatsapp-mcp.bridge          # status
launchctl kickstart -k gui/$(id -u)/com.whatsapp-mcp.bridge    # reiniciar
tail -f whatsapp-bridge/bridge.log

# transcrição manual (source env primeiro)
cd whatsapp-mcp-server && source ../whatsapp-bridge/transcription.env
python3 transcribe.py            # backfill
WHATSAPP_BRIDGE_LOG=../whatsapp-bridge/bridge.log python3 recover_audios.py
```
