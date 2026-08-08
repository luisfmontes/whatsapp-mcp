# Onboarding / roteiro de smoke — ciclo 004

## Ambiente desta máquina

| Item | Valor |
|------|-------|
| Bridge | `whatsapp-bridge.exe`, processo único, auto-start via Task Scheduler (`WhatsAppMCPBridge` → `wscript.exe` → `start-bridge-hidden.vbs` → `start-bridge.ps1`) |
| Porta | `127.0.0.1:3005` (`WHATSAPP_BRIDGE_PORT` no `start-bridge.ps1`; 8080 é reservada pelo Hyper-V/WSL nesta máquina) |
| Auth | **sem** `API_AUTH_TOKEN` — bind loopback, modelo ADR-001. Smoke não precisa de header `Authorization` |
| Log | `C:\Projetos\whatsapp-mcp\bridge.log` |
| Base URL do smoke | `http://127.0.0.1:3005/api` |

## Reinício da bridge com binário novo

```powershell
Stop-Process -Name whatsapp-bridge -Force
# recompilar a partir do repo integrado
cd C:\Projetos\whatsapp-mcp\whatsapp-bridge; go build -o whatsapp-bridge.exe .
Start-ScheduledTask -TaskName WhatsAppMCPBridge
# confirmar que subiu na 3005 antes de qualquer curl
Get-NetTCPConnection -State Listen -OwningProcess (Get-Process whatsapp-bridge).Id | Select LocalAddress, LocalPort
```

## Princípio do smoke deste ciclo

Cinco dos sete endpoints **mudam estado real e visível para outras pessoas** (nome/foto/trava de
grupo, revogação de link, entrada em grupo). Smoke não se faz em grupo de verdade.

**O alvo é um grupo descartável, criado só para isto, com o dono como único participante** — via a
tool `create_group`, que já existe. Nele, toda mutação é observável e não afeta terceiros. Ao final,
`leave_group`.

Os dois endpoints de leitura (`/user_info`, `/profile_picture`) rodam contra o próprio número do dono.

## Roteiro

Preencher `$G` com o JID do grupo descartável recém-criado e `$ME` com o próprio número.

### Leitura — sem efeito colateral

```bash
# 1. user_info do próprio número + um número inexistente (exercita RN-10: found:false)
curl -s -X POST http://127.0.0.1:3005/api/user_info \
  -H 'Content-Type: application/json' \
  -d '{"jids":["<ME>","5562999999999"]}' | jq .
# esperado: results com 2 itens, na ordem pedida, o segundo com found:false

# 2. profile_picture do próprio número
curl -s -X POST http://127.0.0.1:3005/api/profile_picture \
  -H 'Content-Type: application/json' -d '{"jid":"<ME>","preview":true}' | jq .
# esperado: success:true com url/id, OU success:false legível se não houver foto — NUNCA 500

# 3. link de convite SEM reset
curl -s -X POST http://127.0.0.1:3005/api/group_invite_link \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>"}' | jq .
# esperado: link começando com https://chat.whatsapp.com/

# 4. preview do próprio link obtido em (3) — NÃO entra, já sou membro
curl -s -X POST http://127.0.0.1:3005/api/group_invite_info \
  -H 'Content-Type: application/json' -d '{"link":"<LINK_DE_3>"}' | jq .
# esperado: name igual ao do grupo descartável
```

### Mutação — no grupo descartável

```bash
# 5. setters, com falha parcial deliberada não sendo esperada aqui (sou admin do meu grupo)
curl -s -X POST http://127.0.0.1:3005/api/group_settings \
  -H 'Content-Type: application/json' \
  -d '{"group_jid":"<G>","name":"smoke 004","topic":"teste ciclo 004","locked":true}' | jq .
# esperado: success:true, results com 3 itens (name, topic, locked), announce AUSENTE

# 6. campo omitido não muda + topic:"" apaga
curl -s -X POST http://127.0.0.1:3005/api/group_settings \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>","topic":""}' | jq .
# esperado: results com 1 item só; conferir no app que o nome continua "smoke 004"

# 7. request vazio -> 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:3005/api/group_settings \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>"}'
# esperado: 400

# 8. foto do grupo
curl -s -X POST http://127.0.0.1:3005/api/group_photo \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>","media_path":"<JPEG_LOCAL>"}' | jq .
curl -s -X POST http://127.0.0.1:3005/api/group_photo \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>","remove":true}' | jq .

# 9. reset do link (destrutivo por design — por isso só no grupo descartável)
curl -s -X POST http://127.0.0.1:3005/api/group_invite_link \
  -H 'Content-Type: application/json' -d '{"group_jid":"<G>","reset":true}' | jq .
# esperado: link DIFERENTE do obtido em (3)

# 10. join com o link novo -> já sou membro; documentar a resposta observada
curl -s -X POST http://127.0.0.1:3005/api/join_group_with_link \
  -H 'Content-Type: application/json' -d '{"link":"<LINK_DE_9>"}' | jq .
```

### Caminho de falha obrigatório

```bash
# 11. setter em grupo onde NÃO sou admin -> erro legível, não 500 opaco
curl -s -X POST http://127.0.0.1:3005/api/group_settings \
  -H 'Content-Type: application/json' \
  -d '{"group_jid":"<GRUPO_SEM_ADMIN>","name":"nao deve aplicar"}' | jq .
# esperado: results[0].success:false com error legível; o nome do grupo NÃO muda
```

Passo 11 é leitura-que-falha por design: a chamada é rejeitada pelo WhatsApp, nada muda. É o único
toque em grupo real e existe justamente para provar que a rejeição não vira 500.

### Limpeza

```bash
# leave_group no grupo descartável
```

## O que registrar em `regression-watch.md`

- Resposta observada de `/user_info` para número desconhecido (lacuna 🔴 da seção 8 do requirements).
- Resposta observada de `join_group_with_link` quando já se é membro.
- Se `profile_picture` do próprio número devolveu `success:false` e por qual motivo.
