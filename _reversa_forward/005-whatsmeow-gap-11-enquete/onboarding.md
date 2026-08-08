# Onboarding / roteiro de smoke — ciclo 005 (enquete)

Ambiente igual ao do ciclo 004: bridge em `127.0.0.1:3005`, sem `API_AUTH_TOKEN`, log em
`C:\Projetos\whatsapp-mcp\bridge.log`, auto-start pela tarefa `WhatsAppMCPBridge`.
Alvo: **grupo descartável**, criado só para o teste e abandonado ao final.

## O que este smoke precisa provar, além do óbvio

Os ciclos anteriores testavam request/response: mandou, respondeu, acabou. Este tem estado, então
o roteiro tem dois passos que não existiam antes:

1. **O voto sobrevive ao restart da bridge.** Se a apuração vier de memória, ela zera no restart e
   ninguém percebe até o dia em que precisar. Votar → reiniciar → apurar → tem que bater.
2. **O voto órfão não some.** Votar numa enquete cujas opções a bridge não tem precisa aparecer
   como `unresolved_votes`, não sumir (RN-05).

## Roteiro

### Preparação
```
create_group "smoke 005 - apagar" com o próprio número  ->  $G
```

### 1. Criar
```bash
curl -s -X POST http://127.0.0.1:3005/api/create_poll -H 'Content-Type: application/json' \
  -d '{"chat_jid":"<G>","question":"smoke 005?","options":["alpha","beta","gama"],"selectable_count":1}'
# esperado: success + message_id  ->  $P
```
Conferir no app do celular que a enquete apareceu com as 3 opções.

### 2. A enquete virou linha no histórico (RN-02)
```bash
curl -s -X POST http://127.0.0.1:3005/api/messages -H 'Content-Type: application/json' \
  -d '{"chat_jid":"<G>","limit":5}'
# esperado: a mensagem mais recente tem content = "smoke 005?"
# (antes deste ciclo a enquete sumia daqui — handleMessage a descartava)
```

### 3. Votar
```bash
curl -s -X POST http://127.0.0.1:3005/api/vote_poll -H 'Content-Type: application/json' \
  -d '{"chat_jid":"<G>","poll_id":"<P>","options":["beta"]}'
curl -s -X POST http://127.0.0.1:3005/api/poll_results -H 'Content-Type: application/json' \
  -d '{"chat_jid":"<G>","poll_id":"<P>"}'
# esperado: 3 opções listadas INCLUSIVE as de count 0; beta com count 1; total_voters 1
```

### 4. Revoto substitui, não soma (RN-03)
```bash
# votar de novo, agora em "gama"
# esperado: beta volta a 0, gama vai a 1, total_voters CONTINUA 1
```
Este é o passo que pega o bug mais provável do ciclo: acumular linha em vez de substituir.

### 5. Retirar o voto
```bash
# vote_poll com "options": []
# esperado: todas as opções com count 0, total_voters 0
```

### 6. **Sobrevivência a restart** — o passo que só existe neste ciclo
```bash
# votar em "alpha"
# apurar -> alpha 1
Stop-Process -Name whatsapp-bridge -Force; Start-ScheduledTask -TaskName WhatsAppMCPBridge
# esperar conectar, apurar de novo
# esperado: alpha continua 1. Se zerou, a apuracao esta em memoria e o ciclo NAO esta pronto.
```

### 7. Caminhos de erro
```bash
# poll_id inexistente em poll_results  -> 404
# vote_poll em opção que não existe na enquete -> 400 listando a inválida
# create_poll com ["a","a"] -> 400   |   com 1 opção -> 400   |   selectable_count 0 e 99 -> 400
```

### 8. Voto órfão (RN-05) — só se der para simular
Exige um `PollUpdateMessage` de enquete que a bridge não conhece. Não é provocável pelo REST; se não
der para forçar de forma honesta, **não inventar** — anotar em `regression-watch.md` que o caminho
ficou coberto só por teste unitário e dizer isso na PR.

### Limpeza
```
leave_group no grupo descartável
```

## O que registrar em `regression-watch.md`

- Resultado do passo 6 (sobrevivência a restart), que é o critério de pronto nº 3.
- Se o voto órfão foi exercitado de verdade ou só em teste unitário.
- Se `PollCreationMessageV2/V3` existem no protobuf pinado e foram tratados.
