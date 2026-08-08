# Contratos — ciclo 005 (gap #11, enquete)

> Fonte única para o trilho Go e o trilho Python. Os dois implementam contra este arquivo.
> Convenções de handler herdadas do ciclo 004: molde `handleGroupParticipants` (main.go) —
> 405 em método != POST, 400 em decode/campo faltando, validação de domínio antes de falar com o
> WhatsApp, 503 no guard de conexão, 500 com mensagem legível em erro de lib, `r.Context()` sempre.

---

## Schema — messages.db

Acrescentar ao bloco `CREATE TABLE IF NOT EXISTS` que já existe (main.go ~linha 82). **Não** criar
framework de migração: o bloco é idempotente e roda em todo start.

```sql
CREATE TABLE IF NOT EXISTS polls (
    id TEXT,                      -- id da mensagem de criação da enquete
    chat_jid TEXT,
    sender TEXT,                  -- quem criou (JID normalizado por resolveToPN)
    name TEXT,                    -- a pergunta
    options TEXT,                 -- JSON array de nomes, NA ORDEM ORIGINAL (RN-01)
    selectable_count INTEGER,
    timestamp TIMESTAMP,
    PRIMARY KEY (id, chat_jid),
    FOREIGN KEY (chat_jid) REFERENCES chats(jid)
);

-- Voto é substituição, não incremento (RN-03): a PK por votante faz o upsert
-- naturalmente, e revotar sobrescreve em vez de acumular linha duplicada.
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id TEXT,
    chat_jid TEXT,
    voter_jid TEXT,
    selected TEXT,                -- JSON array de NOMES quando resolved=1; [] quando resolved=0
    resolved INTEGER NOT NULL DEFAULT 1,  -- 0 = opções desconhecidas, voto guardado mesmo assim (RN-05)
    timestamp TIMESTAMP,
    PRIMARY KEY (poll_id, chat_jid, voter_jid)
);

CREATE INDEX IF NOT EXISTS idx_poll_votes_poll ON poll_votes(poll_id, chat_jid);
```

Upsert obrigatório (RN-04) — a guarda de timestamp não é opcional:

```sql
INSERT INTO poll_votes (poll_id, chat_jid, voter_jid, selected, resolved, timestamp)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(poll_id, chat_jid, voter_jid) DO UPDATE SET
    selected  = excluded.selected,
    resolved  = excluded.resolved,
    timestamp = excluded.timestamp
WHERE excluded.timestamp >= poll_votes.timestamp;
```

---

## Tratamento de evento — `handleMessage`

Entra **antes** do `if content == "" && mediaType == "" { return }`.

### Criação de enquete

```go
if poll := msg.Message.GetPollCreationMessage(); poll != nil {
    // 1. nomes das opções na ordem original -> JSON
    // 2. StorePoll(id, chatJID, senderJID, poll.GetName(), optionsJSON,
    //               int(poll.GetSelectableOptionsCount()), timestamp)
    // 3. content = poll.GetName() para a enquete aparecer no histórico (RN-02),
    //    seguindo o fluxo normal de StoreMessage abaixo — NÃO retornar aqui.
}
```

`PollCreationMessage` também chega em `PollCreationMessageV2`/`V3` em alguns clientes 🟡 — se os
getters existirem no protobuf pinado, tratar os três; se não, tratar o que existir e registrar no
relatório qual foi.

### Voto

```go
if upd := msg.Message.GetPollUpdateMessage(); upd != nil {
    vote, err := client.DecryptPollVote(context.Background(), msg)
    // pollID vem de upd.GetPollCreationMessageKey().GetID()
    // 1. buscar options do poll em `polls` por (pollID, chatJID)
    // 2. montar mapa hex(sha256(nome)) -> nome com HashPollOptions(options)
    // 3. resolver vote.GetSelectedOptions() ([][]byte) para nomes
    //    - todos resolvidos -> selected = JSON dos nomes, resolved = 1
    //    - poll desconhecido -> selected = "[]", resolved = 0  (RN-05, NÃO descartar)
    // 4. upsert em poll_votes com msg.Info.Timestamp
    return  // voto não vira linha em messages
}
```

Erro de `DecryptPollVote` → logar e seguir; **nunca** derrubar o handler de mensagem por causa de
um voto.

---

## Endpoints

### 1. `POST /api/create_poll`

```go
type CreatePollRequest struct {
    ChatJID         string   `json:"chat_jid"`
    Question        string   `json:"question"`
    Options         []string `json:"options"`
    SelectableCount int      `json:"selectable_count"`
}
type CreatePollResponse struct {
    Success   bool   `json:"success"`
    Message   string `json:"message"`
    MessageID string `json:"message_id,omitempty"`
}
```

Validações, todas antes de falar com o WhatsApp:
- `chat_jid` obrigatório, `types.ParseJID` válido → senão 400.
- `question` não vazia após `TrimSpace` → senão 400.
- `len(options)` entre **2 e 12** → senão 400 (RN-08).
- opções **únicas** após `TrimSpace`, nenhuma vazia → senão 400 `"Invalid options: names must be unique and non-empty"` (RN-08 — duplicata colide no hash).
- `selectable_count` em `[1, len(options)]` → senão 400 (RN-07; **não** repassar valor inválido, a lib troca por 0 em silêncio).

Fluxo: `client.BuildPollCreation(question, options, selectableCount)` → `client.SendMessage(ctx, chatJID, msg)` → gravar em `polls` com o ID devolvido → responder.

Python: `create_poll(chat_jid, question, options, selectable_count=1) -> Tuple[bool, str, str]`.

### 2. `POST /api/vote_poll`

```go
type VotePollRequest struct {
    ChatJID string   `json:"chat_jid"`
    PollID  string   `json:"poll_id"`
    Options []string `json:"options"`   // vazio = retirar o voto
}
```
Resposta no molde `MarkChatResponse` (`success`/`message`).

- `chat_jid` e `poll_id` obrigatórios → senão 400.
- Buscar o poll em `polls`; **não encontrado → 404** com mensagem dizendo que a bridge não conhece
  essa enquete (pode ter sido criada antes da feature).
- Toda opção pedida tem que existir na lista do poll → senão 400 listando as inválidas. Votar em
  opção inexistente gera hash que ninguém resolve.
- `len(options) > selectable_count` (quando `selectable_count > 0`) → 400.
- Reconstruir `types.MessageInfo` do poll a partir das colunas gravadas (ID, chat, sender) e chamar
  `client.BuildPollVote(ctx, pollInfo, options)`, depois `SendMessage`.

Python: `vote_in_poll(chat_jid, poll_id, options) -> Tuple[bool, str]`.

### 3. `POST /api/poll_results`

```go
type PollResultsRequest struct {
    ChatJID string `json:"chat_jid"`
    PollID  string `json:"poll_id"`
}
type PollOptionResult struct {
    Option string   `json:"option"`
    Count  int      `json:"count"`
    Voters []string `json:"voters,omitempty"`
}
type PollResultsResponse struct {
    Success        bool               `json:"success"`
    Message        string             `json:"message"`
    Question       string             `json:"question,omitempty"`
    Results        []PollOptionResult `json:"results,omitempty"`
    TotalVoters    int                `json:"total_voters"`
    UnresolvedVotes int               `json:"unresolved_votes"`
}
```

- Poll não encontrado → 404.
- `Results` traz **todas** as opções do poll, na ordem original, inclusive as com `count: 0`.
  Omitir opção sem voto faria o LLM concluir que ela não existia.
- `TotalVoters` = votantes distintos com `resolved = 1`.
- `UnresolvedVotes` = linhas com `resolved = 0` (RN-05). Sempre presente, mesmo zero.
- Não fala com o WhatsApp: é leitura de `messages.db`. Sem guard de conexão.

Python: `get_poll_results(chat_jid, poll_id) -> Tuple[bool, str, Optional[dict]]`.

---

## Tools MCP

| Tool | Assinatura | Ponto obrigatório da docstring |
|------|-----------|--------------------------------|
| `create_poll` | `(chat_jid, question, options: List[str], selectable_count: int = 1)` | 2–12 opções, nomes únicos; `selectable_count` = quantas o votante pode marcar |
| `vote_in_poll` | `(chat_jid, poll_id, options: List[str])` | `poll_id` é o id da **mensagem de criação**; lista vazia retira o voto |
| `get_poll_results` | `(chat_jid, poll_id)` | **Ressalva obrigatória (RN-06):** o resultado é o que esta bridge viu chegar. O WhatsApp não oferece consulta de votos — voto recebido enquanto a bridge estava fora do ar não está aqui e não há como detectar que faltou. Nunca apresentar como contagem oficial; `unresolved_votes > 0` significa votos de enquete cujas opções a bridge não conhece |
