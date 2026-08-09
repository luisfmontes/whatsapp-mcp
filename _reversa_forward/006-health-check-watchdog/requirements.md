# Requirements: health-check `/api/status` + watchdog de reconexão

> Identificador: `006-health-check-watchdog`
> Data: `2026-08-09`
> Origem: fatia **(b)** do `ao_colher` da ideia `whatsapp-mcp-windows-multiconta` (`ideias.jsonl`,
> status `em-colheita`). A fatia (a), port Windows nativo, já está mesclada.
> Confidência: 🟢 CONFIRMADO, 🟡 INFERIDO, 🔴 LACUNA

## 1. Por que esta fatia existe

A ideia foi plantada depois de uma falha real (2026-08-06 14:52): a bridge caiu e **os vigias
reportam por WhatsApp**, então a queda parou trabalho. A fatia (a) matou a causa daquela vez (WSL
hibernando), mas não deu nenhuma forma de **perceber** que caiu — hoje toda falha é descoberta por
uma tool falhando no meio de outra coisa.

## 2. O que a lib já resolve, e o que ela não resolve 🟢

Verificado na fonte do whatsmeow pinado (`client.go`, `connectionevents.go`), não deduzido:

| Situação | whatsmeow sozinho |
|----------|-------------------|
| Queda transitória de rede | **Resolve.** `EnableAutoReconnect` vem `true` (client.go:288) e o loop tenta indefinidamente |
| Backoff crescendo pra sempre | **Não acontece.** `AutoReconnectErrors` volta a 0 no connect bem-sucedido (connectionevents.go:165) |
| **Sessão deslogada** | **Não resolve, e nem tenta:** `autoReconnect` retorna de cara quando `Store.ID == nil` (client.go:625). Fica parado para sempre |
| Saber se está funcionando | **Não oferece nada.** Nenhum endpoint, nenhum sinal |

Ou seja: o problema **não** é a reconexão em si — é **cegueira**. O sistema não tem como responder
"você está funcionando?" sem tentar usar e falhar.

Isso muda o peso da fatia: o `/api/status` é a parte que entrega valor; o watchdog é um complemento
para o caso em que a lib fica em backoff longo ou em um estado que ela mesma não retoma.

## 3. Estado atual 🟢

- **29 endpoints REST, nenhum de status.**
- O handler de eventos trata `Connected` e `LoggedOut`, mas `LoggedOut` só faz `logger.Warnf` — o
  processo continua de pé respondendo 503 em tudo, indefinidamente, sem nada que sinalize.
- Não existe watchdog, nem registro de "última vez que chegou qualquer evento".

## 4. Regras de negócio

1. **RN-01:** `/api/status` responde **HTTP 200 sempre que a bridge está de pé**, mesmo desconectada
   ou deslogada. O código HTTP separa "processo alcançável" de "WhatsApp utilizável"; colapsar os
   dois destrói a informação que o chamador precisa — conexão recusada passa a ser indistinguível
   de bridge viva e deslogada. 🟢
2. **RN-02:** A resposta carrega **um booleano `healthy`** para quem só quer uma resposta, e um
   `reason` legível quando `healthy` é falso. Sem `reason`, "não saudável" não diz o que fazer. 🟢
3. **RN-03:** `healthy = connected && logged_in`. Nada mais entra nessa conta — em particular,
   ausência de eventos recentes **não** deixa a bridge não-saudável: conversa parada é normal. 🟢
4. **RN-04:** `last_event_at` é exposto como **dado, não como critério**. Serve para o humano
   distinguir "socket diz conectado" de "está realmente recebendo tráfego". 🟢
5. **RN-05:** O watchdog **não tenta reconectar quando a sessão está deslogada** — é impossível sem
   QR novo. Nesse estado ele registra e expõe, e para. Tentar em loop só gera ruído. 🟢
6. **RN-06:** O watchdog só age depois de a bridge estar desconectada por **pelo menos um tick
   inteiro**, para não competir com o backoff da própria lib. 🟢
7. **RN-07:** A decisão do watchdog é uma **função pura** de (conectado, logado): `none`,
   `reconnect` ou `logged-out`. O efeito fica fora dela. Sem isso, a lógica só é testável com um
   client de verdade — ou seja, não é testável. 🟢
8. **RN-08:** `/api/status` **não fala com o WhatsApp**. Só lê estado local do client e do processo.
   Um health-check que faz I/O de rede falha junto com o que deveria diagnosticar. 🟢
9. **RN-09:** `/api/status` fica **atrás do mesmo `API_AUTH_TOKEN`** dos outros endpoints. Só `/qr`
   e `/qr.png` são abertos, e essa exceção existe por causa do pareamento. 🟢

## 5. Requisitos funcionais

| ID | Requisito | Confidência |
|----|-----------|-------------|
| RF-01 | `GET /api/status` devolve `healthy`, `reason`, `connected`, `logged_in`, `jid`, `last_successful_connect`, `auto_reconnect_errors`, `last_event_at`, `uptime_seconds` e o bloco `watchdog` | 🟢 |
| RF-02 | O handler de eventos passa a registrar o instante do **último evento de qualquer tipo** | 🟢 |
| RF-03 | Watchdog em goroutine, intervalo por `WHATSAPP_WATCHDOG_INTERVAL` (default 60s, mínimo 10s) | 🟢 |
| RF-04 | Deslogado → registra e expõe, **sem** tentar reconectar (RN-05) | 🟢 |
| RF-05 | Desconectado e logado → chama `Connect()`, conta a tentativa e registra a ação | 🟢 |
| RF-06 | Tool MCP `get_bridge_status` devolvendo o mesmo shape | 🟢 |
| RF-07 | Testes: função pura de decisão do watchdog, função pura de avaliação de saúde, e o handler com client nil devolvendo **200** com `healthy:false` | 🟢 |

## 6. Delta de dados

**Nenhum.** Sem tabela, sem coluna, sem migração. Todo o estado novo é em memória e morre com o
processo — de propósito: é estado de processo, não de domínio.

## 7. Critério de pronto

1. `go build`, `go vet`, `go test` verdes; suíte Python verde.
2. `curl http://127.0.0.1:3005/api/status` com a bridge conectada → `healthy: true`.
3. **Prova de que o 200 em estado ruim é real**: derrubar a conexão (ou simular via teste de
   handler com client nil) e obter **200** com `healthy:false` e `reason` preenchido — não 503.
4. **Prova de que o watchdog reconecta de verdade**: observar no log uma reconexão disparada por
   ele, não pela lib.

## 8. Lacunas assumidas

- 🔴 **Alertar não está nesta fatia.** O `ao_colher` pede health-check + watchdog; notificar o Luís
  quando `healthy` vira falso é o passo seguinte, e conversa com a ideia
  `vigia-de-resposta-whatsapp-sem-token` (já plantada), que também quer um script barato observando
  fora do modelo. Registrar como próximo passo em vez de embutir aqui.
- 🟡 **Derrubar a conexão de propósito** para o critério 4 não tem caminho limpo pela API. Se não
  der para provocar de forma honesta, dizer isso em vez de fabricar evidência.
