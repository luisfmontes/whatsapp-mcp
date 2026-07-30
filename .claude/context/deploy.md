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

## Build

- `cd whatsapp-bridge && go build -o whatsapp-bridge .` — recompilar após mudar main.go (binário stale não tem fixes).
- `go run main.go` = dev (binário temp em /var/folders, morre ao fechar terminal). Loga no terminal, NÃO em arquivo → recover_audios não funciona com go run.
- Matar órfãos de porta: `lsof -ti TCP:8081 | xargs kill`. `go run` spawna child `main` que sobrevive ao kill do pai.

## install.sh (para outros)

- Gate Go 1.25+ (casa com go.mod). Clona em `~/.whatsapp-mcp`, compila, escreve config Claude/Cursor, cria start-bridge.sh + plist (RunAtLoad/KeepAlive=false por padrão lá).
- One-line: `curl -fsSL https://raw.githubusercontent.com/rodrigopg/whatsapp-mcp/main/install.sh | bash`.

## QR / auth

- Local (install.sh default): `http://localhost:8081/qr` no browser. macOS salva `/tmp/whatsapp-qr.png` e abre no Preview.
- Bridge remota: `/qr`/`/qr.png` ficam **sempre sem auth** mesmo com `API_AUTH_TOKEN` setado (é o próprio fluxo de pareamento) — acessar via túnel SSH (`ssh -L 8081:<bind-addr>:8081 <host>`) ou direto pela rede privada se sua máquina já estiver nela.

## DNS GitHub (rede do Rodrigo)

- `api.github.com` resolve globalmente pra `4.228.31.149` (Azure) — **inalcançável desta rede** (timeout). IPs legados `140.82.x` (range oficial GitHub) roteiam OK.
- Fix: pin em `/etc/hosts` (bloco demarcado `claude-code: GitHub pin`): api→140.82.112.6, github→140.82.112.3, codeload→140.82.112.9. Remover bloco reverte. Bloqueio é da rede/ISP, não DNS local.

## Git

- Remotes: `rodrigopg` = fork (push aqui), `origin` = lharries upstream. main rastreia `rodrigopg/main`.
