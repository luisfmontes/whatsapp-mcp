# Requirements: Gaps whatsmeow #10, #12, #13 — info de usuário, convite de grupo e administração de grupo

> Identificador: `004-whatsmeow-gaps-10-12-13`
> Data: `2026-08-08`
> Base: `whatsmeow-gap-analysis.md` (2026-07-07), features `002-whatsmeow-quick-wins` (#1–#6) e `003-whatsmeow-gaps-7-8-9` (#7–#9)
> Confidência: 🟢 CONFIRMADO, 🟡 INFERIDO, 🔴 LACUNA / DÚVIDA

## 1. Resumo executivo

Fechar os três gaps Tier B restantes de valor médio da tabela do `whatsmeow-gap-analysis.md`:

- **#12 Link de convite de grupo** — obter/revogar o link, pré-visualizar um grupo a partir de um link e entrar no grupo.
- **#13 Setters de grupo** — nome, tópico, foto, modo anúncio e trava de edição.
- **#10 Info de usuário** — status, foto de perfil, dispositivos e nome verificado.

Os três compartilham exatamente o shape de entrega das features 002 e 003: **handler REST curto na bridge Go → helper em `whatsapp.py` → tool `@mcp.tool` em `main.py`**. Nenhum evento novo, nenhuma tabela nova, nenhuma migração.

**Fora de escopo, com motivo:**
- **#11 Enquete** — criar é trivial, mas ler votos exige event handler + persistir as opções da enquete para decriptar (`DecryptPollVote`). Muda o modelo de dados; ciclo próprio (005).
- **#14–#20 (Tier C e baixo valor)** — a própria análise recomendou adiar (newsletters, proxy, privacy, disappearing timer).
- **Bump do `whatsmeow`** (pinado em `v0.0.0-20260529101937-a7ea56383ec4`) — trabalho separado, deliberadamente não misturado com feature, para que uma quebra seja atribuível.

## 2. Contexto a partir do legado

| Fonte | Trecho relevante | Confidência |
|-------|------------------|-------------|
| `whatsmeow-gap-analysis.md#Tabela de gaps` | #10 `GetUserInfo`/`GetProfilePictureInfo`, #12 `GetGroupInviteLink`/`JoinGroupWithLink`, #13 `SetGroup*` — métodos existem na lib, sem REST handler | 🟢 |
| `main.go:1156` `handleGroupParticipants` | Molde canônico do handler: guard de método → decode → validação de JID → whitelist → guard de conexão → chamada whatsmeow com `r.Context()` → resposta JSON | 🟢 |
| `main.go:1324` `mergeIsOnWhatsAppResults` | Precedente de correlacionar resposta parcial do WhatsApp com a lista de entrada, para a API sempre devolver um item por item pedido | 🟢 |
| `main.go:1296` `maxIsOnWhatsAppPhones = 50` | Precedente de cap em endpoint de consulta em lote, explicitamente para fechar vetor de varredura em massa | 🟢 |
| `main.go:919` `ArchiveChatRequest.Archive *bool` | Precedente de campo ponteiro para distinguir "omitido" de "false" — necessário em todos os setters do #13 | 🟢 |
| `main_test.go:152` `TestHandleReact` | Molde de teste de handler com `handler := handleX(nil)`: 405, JSON malformado, campo obrigatório ausente, validação de domínio | 🟢 |
| `whatsapp.py:808` `update_group_participants` | Molde de helper: validação local → `_api_request` → `.json()` com guard de `JSONDecodeError` → tupla `(bool, str, payload)` | 🟢 |
| `main.py:444` `update_group_participants` | Molde de tool: docstring com `Args:`/`Returns:` e nota de comportamento parcial | 🟢 |

### Assinaturas whatsmeow verificadas no módulo pinado 🟢

```go
GetGroupInviteLink(ctx, jid types.JID, reset bool) (string, error)   // devolve InviteLinkPrefix + code
GetGroupInfoFromLink(ctx, code string) (*types.GroupInfo, error)     // stripURLPrefix interno: aceita link OU código
JoinGroupWithLink(ctx, code string) (types.JID, error)               // idem
SetGroupName(ctx, jid types.JID, name string) error
SetGroupTopic(ctx, jid types.JID, previousID, newID, topic string) error  // ""/"" = auto; topic "" apaga o tópico
SetGroupPhoto(ctx, jid types.JID, avatar []byte) (string, error)     // avatar nil remove; JPEG
SetGroupAnnounce(ctx, jid types.JID, announce bool) error
SetGroupLocked(ctx, jid types.JID, locked bool) error
GetUserInfo(ctx, jids []types.JID) (map[types.JID]types.UserInfo, error)
GetProfilePictureInfo(ctx, jid types.JID, params *GetProfilePictureParams) (*types.ProfilePictureInfo, error)
```

`const InviteLinkPrefix = "https://chat.whatsapp.com/"`
`types.UserInfo{VerifiedName *VerifiedName; Status string; PictureID string; Devices []JID; LID JID}`
`types.ProfilePictureInfo{URL, ID, Type, DirectPath string; Hash []byte}`
Erros sentinela: `ErrProfilePictureUnauthorized`, `ErrProfilePictureNotSet`, `ErrGroupInviteLinkUnauthorized`, `ErrInviteLinkRevoked`, `ErrInviteLinkInvalid`, `ErrNotInGroup`, `ErrGroupNotFound`.

## 3. Personas e cenários de uso

| Persona | Objetivo | Cenário-chave |
|---------|----------|---------------|
| Assistente de IA (Claude via MCP) | Administrar grupos do dono | "Renomeia o grupo TBC Agro pra 'TBC Agro 2026' e tranca pra só admin editar" |
| Assistente de IA | Convidar sem expor números | "Me passa o link do grupo X pra eu mandar pro fornecedor" |
| Assistente de IA | Entrar com segurança | "Recebi esse link, que grupo é?" → preview → só então entrar |
| Assistente de IA | Contexto sobre um contato | "Esse número tem recado de status? Tem foto?" antes de redigir mensagem |

## 4. Regras de negócio novas ou alteradas

1. **RN-01:** Toda operação de grupo (#12 e #13) exige JID com servidor `@g.us`; JID de outro servidor → **400 sem chamada ao WhatsApp**. 🟢
   - Origem: `handleGroupParticipants` (main.go:1168) já aplica essa guarda; herdada literalmente.
2. **RN-02:** `reset=true` em `get_group_invite_link` **revoga o link antigo** — quem tiver o link velho perde o acesso. É ação destrutiva de efeito externo: `reset` default `false`, e a docstring da tool declara a revogação em primeira linha. 🟢
3. **RN-03:** Pré-visualizar um convite (`GetGroupInfoFromLink`) **não entra no grupo**; entrar é operação separada e explícita. Nunca colapsar as duas numa tool só. 🟢
4. **RN-04:** Os setters de grupo (#13) são **aplicados campo a campo**, na ordem `name → topic → announce → locked`, e o resultado é **por campo**. Um campo pode falhar (sem ser admin, por exemplo) e os outros aplicarem — a resposta nunca colapsa isso num sucesso/falha único. 🟢
   - Origem: mesmo princípio de RN-02 da feature 003 (resultado por participante).
5. **RN-05:** Campo de setter **omitido não é alterado**. Como `topic: ""` significa *apagar o tópico* no whatsmeow e `announce: false` é um valor legítimo, todos os campos do request são **ponteiros**; ausência ≠ zero-value. 🟢
   - Origem: `ArchiveChatRequest.Archive *bool` (main.go:919).
6. **RN-06:** Request de setters sem **nenhum** campo preenchido → 400 (chamada sem efeito é erro do chamador, não no-op silencioso). 🟢
7. **RN-07:** Nome de grupo segue a mesma regra do `create_group` existente; tópico não tem limite imposto pela bridge. 🟡 (o executor confere a validação real em `createWhatsAppGroup` e reusa; não inventa limite novo)
8. **RN-08:** `set_group_photo` aceita **caminho de arquivo local** lido pela bridge (mesmo modelo do `send_file`), JPEG; `remove: true` remove a foto (`avatar nil`). `media_path` e `remove` são mutuamente exclusivos. 🟢
9. **RN-09:** Consulta de info de usuário é **em lote com cap de 20 JIDs**, pelo mesmo motivo do cap de 50 do `is_on_whatsapp`: limitar custo da consulta e fechar vetor de varredura em massa. 🟢
10. **RN-10:** `GetUserInfo` devolve `map[JID]UserInfo` e **pode omitir** JIDs não encontrados. A resposta da API devolve **um item por JID pedido, na ordem de entrada**, com `found: false` no que faltou. 🟢
    - Origem: `mergeIsOnWhatsAppResults` (main.go:1324) — mesmo problema, mesma solução.
11. **RN-11:** Foto de perfil ausente (`ErrProfilePictureNotSet`) ou oculta por privacidade (`ErrProfilePictureUnauthorized`) **não é erro de servidor**: HTTP 200 com `success: false` e mensagem legível. Só falha de transporte/protocolo vira 500. 🟢
12. **RN-12:** A bridge **devolve a URL** da foto, não baixa a imagem. Download fica com o chamador. 🟢
13. **RN-13:** Nenhuma das operações escreve em `messages.db`. 🟢

## 5. Requisitos funcionais

| ID | Requisito | Confidência |
|----|-----------|-------------|
| RF-01 | `POST /api/group_invite_link` devolve o link de convite do grupo; `reset` opcional revoga e gera novo | 🟢 |
| RF-02 | `POST /api/group_invite_info` resolve um link/código em metadados do grupo, sem entrar | 🟢 |
| RF-03 | `POST /api/join_group_with_link` entra no grupo e devolve o JID resultante | 🟢 |
| RF-04 | `POST /api/group_settings` aplica nome/tópico/announce/locked com resultado por campo | 🟢 |
| RF-05 | `POST /api/group_photo` define ou remove a foto do grupo, devolvendo o novo `picture_id` | 🟢 |
| RF-06 | `POST /api/user_info` devolve status, `picture_id`, `verified_name`, `lid` e dispositivos, um item por JID pedido | 🟢 |
| RF-07 | `POST /api/profile_picture` devolve URL/ID/tipo da foto de perfil de usuário ou grupo | 🟢 |
| RF-08 | 7 tools MCP correspondentes, com docstrings que orientam o chamador: revogação em `reset`, preview antes de entrar, resultado parcial nos setters | 🟢 |
| RF-09 | Testes de handler no molde `TestHandleReact` para os 7 endpoints: 405, JSON malformado, campo obrigatório ausente, JID não-`@g.us`, request de setter vazio, cap de 20 JIDs, `media_path`+`remove` juntos | 🟢 |

## 6. Requisitos não-funcionais

| ID | Requisito | Confidência |
|----|-----------|-------------|
| RNF-01 | Endpoints herdam o modelo de auth existente da bridge (`API_AUTH_TOKEN`), sem exceção | 🟢 |
| RNF-02 | Toda chamada whatsmeow recebe `r.Context()` — nenhum `context.Background()` em handler | 🟢 |
| RNF-03 | `go build ./...` e `go test ./...` verdes; `go vet` limpo | 🟢 |
| RNF-04 | Suíte Python existente continua verde; helpers/tools novos importam sem erro | 🟢 |
| RNF-05 | Sem regressão nas 25 tools atuais — nenhuma assinatura existente muda | 🟢 |

## 7. Critério de pronto

1. `cd whatsapp-bridge && go build ./... && go vet ./... && go test ./...` — verde.
2. `cd whatsapp-mcp-server && python -m unittest discover -v` — verde.
3. Bridge reiniciada com o binário novo e **smoke real** nos 7 endpoints com client conectado (roteiro em `onboarding.md`), incluindo pelo menos um caminho de falha esperado (grupo em que não sou admin → erro legível, não 500).
4. README e `whatsmeow-gap-analysis.md` atualizados marcando #10, #12 e #13 como cobertos.

## 8. Lacunas assumidas

- 🔴 **`GetUserInfo` em número que não é contato**: comportamento do WhatsApp (retorno vazio vs. erro) não é determinístico na doc da lib. RN-10 já cobre o caso omitido; o smoke deve exercitar um número desconhecido e o resultado observado vira nota em `regression-watch.md`.
- 🔴 **`SetGroupPhoto` com JPEG fora de proporção**: o WhatsApp pode rejeitar com `ErrInvalidImageFormat`. Sem pré-validação de imagem na bridge — o erro da lib é repassado como mensagem legível.
