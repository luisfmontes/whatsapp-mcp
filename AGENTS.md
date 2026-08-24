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
- Re-parear/re-sync: `StoreMessage` preserva `content` existente via `COALESCE(NULLIF(...))` (não sobrescreve transcrição com string vazia do sync) — mas faça backup de `messages.db`/`whatsapp.db` antes de qualquer re-pareamento de qualquer forma, é operação real de produção.
- Transcrição é **opt-in**; sem engine, sweep deve ser no-op (não marcar áudios).
- `transcription.env` **nunca** commitado (gitignored).
- Push em `origin` (`luisfmontes/whatsapp-mcp`, este fork), **não** em `upstream` (`rodrigopg/whatsapp-mcp`, que é só fetch para sincronizar).

## Checklist antes de abrir PR

- [ ] `go build -o whatsapp-bridge .` compila; `go test ./...` passa.
- [ ] `python3 -m unittest test_transcribe -v` passa.
- [ ] Mudou main.go? Binário recompilado e bridge reiniciada — local: `launchctl kickstart -k`; VPS: `go build` na própria VPS (CGO não cross-compila) + `systemctl restart whatsapp-bridge-*`.
- [ ] Sem path pessoal/secret vazando (transcription.env, API keys).
- [ ] `python scripts/check-personal-data.py` sai 0. O repo e **publico**: telefone
      e JID novos so passam depois de alguem olhar o numero e registrar em
      `scripts/personal-data-baseline.txt` que ele e sintetico.
- [ ] Mudou comportamento de sync/escrita? Conferir impacto em transcrições existentes.
- [ ] README/install.sh coerentes se mudou onboarding (versão Go, env vars, troubleshooting).
- [ ] PR contra `origin/main`. Nada de PR novo para `upstream` (rodrigopg) — o destino é este fork. Os PRs já abertos lá (#14, #15) ficam como estão; fechá-los sem motivo técnico é ruído no repositório de outra pessoa.

## Comandos essenciais

```bash
# build + test
cd whatsapp-bridge && go build -o whatsapp-bridge . && go test ./...
cd whatsapp-mcp-server && python3 -m unittest test_transcribe -v

# serviço (macOS launchd)
launchctl print gui/$(id -u)/com.whatsapp-mcp.bridge          # status
launchctl kickstart -k gui/$(id -u)/com.whatsapp-mcp.bridge    # reiniciar
tail -f whatsapp-bridge/bridge.log

# transcrição manual (set -a propaga pro subprocesso; source sozinho não)
cd whatsapp-mcp-server && set -a && source ../whatsapp-bridge/transcription.env && set +a
python3 transcribe.py            # backfill
WHATSAPP_BRIDGE_LOG=../whatsapp-bridge/bridge.log python3 recover_audios.py
```
