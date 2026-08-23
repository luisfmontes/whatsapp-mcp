# Contexto: Deploy / Operação

## Bridge remota (bridge e MCP em máquinas diferentes)

Desde 2026-07-30 a bridge suporta rodar numa máquina diferente do MCP server (ex: bridge numa
VPS/home server, MCP local no laptop) — o server ficou 100% stateless (ver `mcp-python.md`),
então isso passou a ser suportado sem inventar nada além do que já existia:

- **Bind**: por padrão `127.0.0.1` (só a própria máquina). Pra expor pra outra rede, `BIND_ADDR=<ip>`
  (endereço de uma VPN/rede privada — Tailscale, WireGuard, etc — nunca `0.0.0.0` direto pra
  internet pública).
- **Auth obrigatória** quando não-loopback: `API_AUTH_TOKEN` — a bridge **recusa subir** se
  `BIND_ADDR` não é loopback e o token está vazio (fail-closed). Toda `/api/*` exige
  `Authorization: Bearer <token>`; `/qr`/`/qr.png` continuam sem auth (é o próprio fluxo de
  pareamento). MCP server lê o token equivalente via `WHATSAPP_API_AUTH_TOKEN`.
- **Múltiplas contas na mesma VPS**: rodar duas (ou mais) instâncias da bridge é só questão de
  diretório + porta (`WHATSAPP_BRIDGE_PORT`) + token diferentes por instância — cada uma seu
  próprio `store/`, sem interferência entre sessões.
- **Rede privada é sua escolha** (Tailscale, WireGuard, SSH tunnel, VPN da nuvem que você usa) —
  a bridge só precisa que `BIND_ADDR` aponte pra uma interface alcançável pelo MCP client.
  Cuidado com ordem de boot: se a bridge sobe **antes** da VPN/interface estar pronta, o bind
  falha silenciosamente (`bind: cannot assign requested address`, só logado, não fatal) e o
  processo fica "active" no systemd/launchd **sem porta nenhuma aberta** — checar `ss -tln`
  além do status do serviço. Se usar systemd, adicionar `After=`/`Wants=` pro serviço da sua VPN.

## Host Linux (VM de dev) vs macOS

- Doc da seção launchd descreve o setup **macOS**. VMs Linux não têm launchd — a bridge roda
  como **systemd system-service** lá (ex: `/etc/systemd/system/whatsapp-bridge-dev1.service`,
  `Restart=always`, `RestartSec=5`, `WHATSAPP_BRIDGE_PORT` explícito, ExecStart = binário direto).
- **Reiniciar sem sudo** (se não tiver root no host): `systemctl restart` exige root; workaround é
  `kill <pid>` do processo `whatsapp-bridge` — systemd com `Restart=always` religa sozinho em
  ~5s. Confirmar com `pgrep -f '/whatsapp-bridge$'`. `systemctl --user` falha nesse caso (unit é
  system-scope, não user).
- Engine de transcrição `local` (whisper.cpp) só funciona se o binário/modelo existirem *nesse*
  host — paths absolutos de outra máquina (ex: macOS) não existem lá. Nesse caso usar engine
  `api` (Groq/OpenAI) via `transcription.env`/`transcription.systemd.env` (EnvironmentFile do
  systemd, gitignored, contém a API key).
- Porta não é sempre 8080 — `transcribe.py`/scripts de backfill usam esse default, então rodar
  manual num host com porta diferente precisa `WHATSAPP_API_BASE_URL=http://localhost:<porta>/api`
  explícito.

## Windows (Task Scheduler)

- Não há launchd nem systemd. O auto-start é uma tarefa `WhatsAppMCPBridge` (single account) ou
  `WhatsAppMCPBridge-<alias>` (multi-account), trigger `-AtLogOn`, rodando **sem elevação** na 
  sessão do usuário (sem senha guardada, sem admin). Criada por `install.ps1 -Service`. 
  Inspecionar single: `Get-ScheduledTask -TaskName WhatsAppMCPBridge`. Inspecionar todas:
  `Get-ScheduledTask -TaskName "WhatsAppMCPBridge*"`.
- `-ExecutionTimeLimit ([TimeSpan]::Zero)` é obrigatório: o default do agendador mata a tarefa em
  3 dias, o que num bridge de longa duração é morte silenciosa no meio da semana.
- O Task Scheduler **não guarda saída** — diagnóstico só existe se o launcher redirecionar. O
  `start-bridge.ps1` gerado faz `& '.\whatsapp-bridge.exe' *>> bridge.log` e seta
  `WHATSAPP_BRIDGE_LOG` (o `recover_audios.py` lê esse arquivo).
- Como no launchd, **o agendador não herda env da shell** → transcrição vem de `transcription.env`,
  carregado pelo launcher. As linhas do arquivo começam com `export` (é escrito para `source` no
  POSIX); o launcher remove esse prefixo antes de setar a variável. **Em modo multi-account,
  `transcription.env` é único** (em `~/.whatsapp-mcp/transcription.env`), **compartilhado por todas
  as contas** — cada launcher carrega esse arquivo, então todas usam a mesma chave de transcrição
  e engine.
- Arquivo de QR em `%TEMP%`: em modo single-account é `whatsapp-qr.png`; em multi-account com
  `WHATSAPP_ACCOUNT` setado (pela tarefa do bridge), passa a ser `whatsapp-qr-<alias>.png`
  (ex.: `whatsapp-qr-trabalho.png`). Mesmo vale para o lockfile de transcrição: `wa_transcribe.lock`
  em single-account, `wa_transcribe-<alias>.lock` em multi-account. Sem `WHATSAPP_ACCOUNT`, o
  bridge usa exatamente os nomes legados (a conta default não percebe a mudança).
- **Porta:** o Windows reserva faixas TCP (Hyper-V/WSL) e a 8080 costuma cair numa delas. O bind
  falha com `An attempt was made to access a socket in a way forbidden by its access permissions`
  e o bridge **só loga e continua** — mesma armadilha de "serviço ativo sem porta aberta" da seção
  de bridge remota, mas aqui é o caso comum, não a exceção. Checar
  `netsh interface ipv4 show excludedportrange protocol=tcp`; o `install.ps1` já troca de porta
  sozinho e usa 8081 como default. Em multi-account, cada new-account ganha sua porta (começando
  de 3006, pulando as já-usadas).
- Reiniciar: `Stop-Process -Name whatsapp-bridge -Force` (não há sinal de parada limpa no Windows;
  a tarefa religa no próximo logon, ou `Start-ScheduledTask -TaskName WhatsAppMCPBridge*`).
  Em multi-account, reiniciar uma conta específica: `Stop-Process -Name whatsapp-bridge -Force`
  mata todas (não há filtro por port), então use `Start-ScheduledTask -TaskName "WhatsAppMCPBridge-trabalho"`
  pra religar só a que você quer.
- Checar porta: `Get-NetTCPConnection -LocalPort <porta> -State Listen`. Checar processo:
  `Get-Process whatsapp-bridge` (em multi-account todos aparecem como `whatsapp-bridge.exe`; 
  filtrar por porta ou olhar `Get-ScheduledTask -TaskName "WhatsAppMCPBridge*" -v` pra ver qual
  tarefa roda cada um).
- Arquivos `.ps1` deste repo são **ASCII puro** de propósito: o PowerShell 5.1 lê arquivo sem BOM
  como ANSI, e um glifo Unicode virando bytes soltos (0x93 = aspa em CP-1252) faz o script inteiro
  falhar no parse com "falta } de fechamento". Já o JSON de config é gravado **sem BOM**, porque BOM
  na frente de JSON quebra o parser de vários clientes MCP.

## launchd (serviço persistente — macOS)

- Agent: `~/Library/LaunchAgents/com.whatsapp-mcp.bridge.plist`. **`RunAtLoad=true` + `KeepAlive=true`** (arranca no login, reinicia se cair, ThrottleInterval 10s).
- Roda do **clone de trabalho**, não de `~/.whatsapp-mcp`. Executa `start-bridge.sh`. (Herdado do upstream, onde o caminho documentado era `/Users/rodrigo/git/whatsapp-mcp/whatsapp-bridge` — a máquina macOS do Rodrigo, não a sua.)
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
- One-line: `curl -fsSL https://raw.githubusercontent.com/luisfmontes/whatsapp-mcp/main/install.sh | bash`.
- Flags: `--service` cria unit systemd **user** `whatsapp-bridge` (Linux) / launchd KeepAlive (macOS); `--codex` registra o MCP no `~/.codex/config.toml`.

## Plugin Claude Code

- Repo também é distribuído como plugin: `.claude-plugin/plugin.json` + `commands/setup.md`. **Ainda sem marketplace próprio** — instalação por `install.sh`/`install.ps1` deste repo. O marketplace `rodrigopg/claude-plugins` distribui o plugin *dele*, não este.
- Coexistência com bridge já rodando como serviço system-scope (ex: setup de VM Linux acima): `install.sh --service` cria unit systemd **user**; se a bridge já roda como serviço **system** numa porta própria, o guard do install.sh detecta a bridge ativa e pula o setup de serviço (não duplica).

## QR / auth

- Local (install.sh default): `http://localhost:8081/qr` no browser (PNG raw em `/qr.png`). macOS salva `/tmp/whatsapp-qr.png` e abre no Preview. QR renova a cada ~20s.
- Bridge remota: `/qr`/`/qr.png` ficam **sempre sem auth** mesmo com `API_AUTH_TOKEN` setado (é o próprio fluxo de pareamento) — acessar via túnel SSH (`ssh -L 8081:<bind-addr>:8081 <host>`) ou direto pela rede privada se sua máquina já estiver nela.
- **Re-parear:** QR só é emitido no startup quando `client.Store.ID == nil` (sem sessão em `whatsapp.db`). Logout remoto apaga a sessão mas NÃO re-entra no loop de QR em runtime — `/qr` fica preso em "connected". Precisa reiniciar o processo (`kill <pid>`; systemd/launchd com restart automático religa sozinho). Logout pelo celular já zera `whatsmeow_device`; aí o bridge sobe limpo e gera QR.
- History sync ao parear é limitado (poucas msgs/conversas recentes), não histórico completo — normal do multi-device. Backup `messages.db` antes (INSERT OR REPLACE pode sobrescrever).

## DNS GitHub (herdado do upstream — rede do Rodrigo, não a sua)

- Diagnóstico feito na rede do Rodrigo; mantido aqui porque a receita serve se você bater no mesmo bloqueio. `api.github.com` resolve globalmente pra `4.228.31.149` (Azure) — **inalcançável daquela rede** (timeout). IPs legados `140.82.x` (range oficial GitHub) roteiam OK.
- Fix: pin em `/etc/hosts` (bloco demarcado `claude-code: GitHub pin`): api→140.82.112.6, github→140.82.112.3, codeload→140.82.112.9. Remover bloco reverte. Bloqueio é da rede/ISP, não DNS local.

## Git

- Remotes: `origin` = `luisfmontes/whatsapp-mcp` (push aqui), `upstream` = `rodrigopg/whatsapp-mcp` (fetch para sincronizar). main rastreia `origin/main`. Cadeia: lharries → rodrigopg → luisfmontes.
