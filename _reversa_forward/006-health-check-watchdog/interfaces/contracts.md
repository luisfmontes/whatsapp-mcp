# Contratos — ciclo 006 (health-check + watchdog)

## `GET /api/status`

**GET, não POST.** Os ciclos 003–005 padronizaram POST, mas um health-check precisa ser
`curl http://127.0.0.1:3005/api/status` sem corpo, chamável de um script de monitoramento ou de uma
tarefa agendada. Método diferente de GET → **405**.

```go
type WatchdogStatus struct {
    IntervalSeconds int    `json:"interval_seconds"`
    Reconnects      int    `json:"reconnects"`
    LastAction      string `json:"last_action,omitempty"`     // "none" | "reconnect" | "logged-out"
    LastActionAt    string `json:"last_action_at,omitempty"`  // RFC3339
    LastTickAt      string `json:"last_tick_at,omitempty"`    // RFC3339
}

type StatusResponse struct {
    Success               bool           `json:"success"`
    Healthy               bool           `json:"healthy"`
    Reason                string         `json:"reason,omitempty"`
    Connected             bool           `json:"connected"`
    LoggedIn              bool           `json:"logged_in"`
    JID                   string         `json:"jid,omitempty"`
    LastSuccessfulConnect string         `json:"last_successful_connect,omitempty"` // RFC3339
    AutoReconnectErrors   int            `json:"auto_reconnect_errors"`
    LastEventAt           string         `json:"last_event_at,omitempty"`           // RFC3339
    UptimeSeconds         int64          `json:"uptime_seconds"`
    Watchdog              WatchdogStatus `json:"watchdog"`
}
```

Regras do handler:

- **Sempre HTTP 200** enquanto o processo responde (RN-01). Nunca 503 aqui, nem com `client == nil`.
  Quem chama distingue "bridge morta" por conexão recusada, não por status code.
- `Success` é sempre `true` — significa "consegui responder", não "está tudo bem". Quem quer saber
  se está bem lê `Healthy`.
- `client == nil` → `Connected:false`, `LoggedIn:false`, `Healthy:false`, com `Reason` dizendo que o
  client não foi inicializado.
- **`LastTickAt`** (acrescentado ao planejar o smoke): é como um chamador distingue watchdog vivo de
  watchdog morto. Um watchdog saudável **só loga quando age**, então rodando e travado são
  indistinguíveis de fora — a mesma cegueira que este endpoint existe para remover, um nível acima.
  Deve avançar a cada `IntervalSeconds`. Vazio só é esperado logo depois do start.
- **`IntervalSeconds` vem do valor que o laço resolveu na inicialização**, não de uma releitura do
  ambiente pelo handler. Reportar um número que o laço não está usando é pior que não reportar.
- Campos vindos direto da lib: `client.IsConnected()`, `client.Store.ID != nil` (logado),
  `client.Store.ID.String()`, `client.LastSuccessfulConnect`, `client.AutoReconnectErrors`.
- `LastSuccessfulConnect` e `LastEventAt` **omitidos quando zero** — data zero formatada engana mais
  que campo ausente.
- **Não chamar nada de rede** (RN-08).

### Função pura obrigatória (RN-02/RN-03)

```go
// evaluateHealth decides the single boolean a monitor reads, plus the reason a
// human needs. Pure so the decision is testable without a client.
func evaluateHealth(connected, loggedIn bool) (healthy bool, reason string)
```

| connected | loggedIn | healthy | reason |
|-----------|----------|---------|--------|
| true | true | `true` | `""` |
| false | true | `false` | `"disconnected from WhatsApp"` |
| true | false | `false` | `"not logged in — scan the QR code at /qr"` |
| false | false | `false` | `"not logged in — scan the QR code at /qr"` |

Deslogado ganha da desconexão porque é o estado **acionável**: reconectar não resolve, só reparear.

---

## Watchdog

### Função pura obrigatória (RN-07)

```go
type watchdogDecision string

const (
    watchdogNone      watchdogDecision = "none"
    watchdogReconnect watchdogDecision = "reconnect"
    watchdogLoggedOut watchdogDecision = "logged-out"
)

// decideWatchdogAction is the whole policy, separated from the effect so it can
// be tested without a live client.
func decideWatchdogAction(connected, loggedIn bool) watchdogDecision
```

| connected | loggedIn | decisão | por quê |
|-----------|----------|---------|---------|
| true | true | `none` | nada a fazer |
| true | false | `logged-out` | conectado sem sessão: só QR resolve |
| false | false | `logged-out` | `autoReconnect` da lib nem tenta com `Store.ID == nil` (RN-05) |
| false | true | `reconnect` | é o único caso em que reconectar tem chance |

### Laço

- Intervalo: `WHATSAPP_WATCHDOG_INTERVAL` em segundos, default **60**, **mínimo 10** (valor menor,
  inválido ou não-numérico cai no default; registrar no log qual valor foi usado).
- `reconnect`: só depois de a bridge ter sido vista desconectada em **dois ticks consecutivos**
  (RN-06) — um tick isolado é provavelmente o backoff da lib já trabalhando. Ao agir:
  `client.Connect()`, incrementar `Reconnects`, gravar `LastAction`/`LastActionAt`. Erro do
  `Connect()` é logado e **não** derruba o laço.
- `logged-out`: gravar `LastAction`/`LastActionAt` e logar em nível **Warn**, no máximo uma vez a
  cada 10 ticks para não inundar o log de uma sessão morta.
- `none`: zera o contador de ticks desconectados. Não escreve `LastAction`.

### Estado compartilhado

`lastEventAt` é escrito pelo callback de eventos do whatsmeow e lido pelo handler HTTP — threads
diferentes. Usar `atomic.Int64` com unix nanos, **não** uma variável solta: é corrida de dados de
verdade, e o `-race` do CI pega.

O mesmo vale para os contadores do watchdog: proteger com `sync.RWMutex` ou atômicos.

### Registro do último evento (RF-02)

No `switch` do `client.AddEventHandler`, antes do `switch`, marcar o instante para **qualquer**
evento — não só `*events.Message`. O objetivo é "está chegando tráfego", e recibo de entrega,
presença e sincronização também são tráfego.

---

## Tool MCP

| Tool | Assinatura | Ponto obrigatório da docstring |
|------|-----------|--------------------------------|
| `get_bridge_status` | `()` | Diz se a bridge está funcionando **sem tentar usá-la**. `healthy:false` com `reason` de sessão deslogada significa que só reparear pelo QR resolve — reconectar não adianta. Falha de conexão com a bridge (exceção de transporte) significa processo fora do ar, que é diferente de `healthy:false` |

Helper Python `get_bridge_status() -> Tuple[bool, str, Optional[dict]]`, usando `_api_request("GET", "/status")`, molde `get_group_info` (whatsapp.py). Timeout curto: um health-check que trava 30s não serve para diagnosticar nada — usar `timeout=5`.
