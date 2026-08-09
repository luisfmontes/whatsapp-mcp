# Regression watch — ciclo 006 (health-check + watchdog)

Comportamento observado com a bridge conectada na conta do dono. Diferente dos ciclos anteriores,
este smoke não cria nem altera nada no WhatsApp: o endpoint é leitura de estado local.

## 1. `go test -race` verde não provava nada ⚠️ a lição do ciclo

A primeira execução do detector de corrida (WSL, com CGO) passou. Estava passando **vazia**: nenhum
teste iniciava a goroutine do watchdog nem o callback de evento, e o `-race` só acha corrida em
caminho que o teste de fato executa.

Lendo o código, havia uma corrida real no laço:

```go
watchdogState.Unlock()
logger.Warnf("... attempt %d", watchdogState.reconnects)  // leitura sem sincronização
```

Estado protegido por mutex lido com o mutex solto, correndo com todo `GET /api/status` concorrente.

**Medição depois de corrigir** (`TestWatchdogStateConcurrency`, que existe só para dar ao detector o
que inspecionar):

| Cenário | Corridas detectadas |
|---|---|
| leitura solta reintroduzida | **3 de 6** execuções |
| código corrigido, suíte inteira | **0 de 6** |

**Se regredir:** detecção é probabilística — 3 de 6 significa que *uma* execução verde não é prova.
Por isso o CI roda `-race -count=3` nos jobs com CGO. Uma execução única verde deve ser tratada como
"não observei", não como "não existe".

## 2. Dois bugs de comportamento achados na mesma leitura

| Bug | Efeito real |
|---|---|
| Aviso de deslogado só saía no 10º tick | Sessão morta ficaria **10 minutos em silêncio** no intervalo padrão — o oposto do propósito da fatia |
| `loggedOutWarnTick` não zerava ao reconectar | O logout seguinte herdava o contador antigo e podia demorar a avisar |

Nenhum dos dois quebrava teste, build ou lint. Os dois estavam no caminho que o ciclo existe para
cobrir.

## 3. O watchdog era invisível ⚠️ achado ao planejar o smoke

Não havia como provar que o laço está vivo: ele só loga quando age, então um watchdog rodando e um
travado são idênticos vistos de fora — a mesma cegueira que o ciclo remove, um nível acima.

Resolvido com `watchdog.last_tick_at`, carimbado **antes** de qualquer decisão, para tick sem ação
contar como tick. Junto, `interval_seconds` passou a reportar o valor que o laço resolveu na
inicialização em vez de o handler reler o ambiente por conta própria — os dois podiam divergir.

**Se regredir:** alguém "simplificar" carimbando o tick só quando há ação volta a esconder o laço
saudável, que é o caso comum.

## 4. Confirmado em execução

| Comportamento | Observado |
|---|---|
| `/api/status` com a bridge conectada | HTTP 200, `healthy:true`, `connected:true`, `logged_in:true` |
| Método diferente de GET | HTTP 405 |
| `Content-Type` | `application/json` |
| Campos zerados omitidos | `last_tick_at` ausente logo após o start, presente depois do primeiro tick |
| Tool MCP ponta a ponta | 36 tools registradas; `get_bridge_status()` devolve o dict completo |
| **Bridge fora do ar é distinguível** | erro de transporte com `payload: None`, em **2,1s** (timeout curto de 5s, não os 30s padrão) — diferente de `healthy:false` com corpo |

## 5. Não coberto pelo smoke

- **Reconexão de verdade.** Provar o caminho `reconnect` exige derrubar a conexão com o WhatsApp, e
  não há caminho limpo para isso pela API. Derrubar rede ou mexer no firewall da máquina do dono
  seria invasivo demais para o ganho. A máquina de estados está coberta por
  `TestApplyWatchdogDecision` (um tick ruim não reconecta, dois consecutivos sim, tick saudável zera
  a sequência), mas **a chamada real a `client.Connect()` nunca foi exercitada**.
- **Estado deslogado em execução.** Mesma limitação: exigiria deslogar o dispositivo do dono.
- **Alertar quando `healthy` vira falso.** Fora de escopo por decisão, registrada na seção 8 do
  `requirements.md`. É o passo seguinte e conversa com a ideia `vigia-de-resposta-whatsapp-sem-token`.

## 6. Nota sobre o `-race` na máquina do dono

Não roda no Windows: exige CGO, e a ausência de toolchain C é justamente o ponto do port nativo. A
validação foi feita em WSL com Go instalado num diretório descartável, depois removido. O `~/go` e o
`~/.cache/go-build` que existem lá são de 2026-07-31, anteriores e intocados. O CI cobre isso de
forma permanente.
