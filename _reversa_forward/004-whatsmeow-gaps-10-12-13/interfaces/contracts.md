# Contratos REST — ciclo 004 (gaps #10, #12, #13)

> Fonte única da verdade para o trilho Go (`main.go`) e o trilho Python (`whatsapp.py` + `main.py`).
> Os dois trilhos são implementados em paralelo **contra este arquivo** — não contra o código do outro.

## Convenções herdadas (valem para os 7 endpoints)

Molde: `handleGroupParticipants` (`main.go:1156`).

1. `r.Method != http.MethodPost` → `http.Error(..., 405)`.
2. Decode falhou **ou** campo obrigatório vazio → `http.Error(..., 400)` com mensagem dizendo o que falta.
3. Validação de domínio (JID, whitelist, cap) → `400`, **antes** de qualquer chamada ao WhatsApp.
4. `client == nil || !client.IsConnected()` → `503` + JSON `{success:false, message:"WhatsApp client not connected"}`.
5. Erro da lib → `500` + JSON `{success:false, message:"<Método> error: <err>"}` — **exceto** onde a tabela abaixo marcar erro sentinela como resultado normal (200 + `success:false`).
6. Sucesso → `w.Header().Set("Content-Type","application/json")` + encode do response struct.
7. Toda chamada whatsmeow recebe `r.Context()`.
8. Handler é `func handleX(client *whatsmeow.Client) http.HandlerFunc`, registrado em `main.go` junto do bloco `/api/group_participants` (linha ~3224).

Helper Python: molde `update_group_participants` (`whatsapp.py:808`) — valida localmente, chama `_api_request("POST", "/rota", json=payload)`, faz `.json()` dentro de `try/except json.JSONDecodeError`, devolve tupla, e captura `requests.RequestException` e `Exception` no fim.

---

## #12 — Link de convite

### 1. `POST /api/group_invite_link`

```go
type GroupInviteLinkRequest struct {
    GroupJID string `json:"group_jid"`
    Reset    bool   `json:"reset"`
}
type GroupInviteLinkResponse struct {
    Success bool   `json:"success"`
    Message string `json:"message"`
    Link    string `json:"link,omitempty"`
}
```

- `group_jid` obrigatório, servidor **deve** ser `types.GroupServer` → senão 400 `"Invalid group_jid: must be a @g.us JID"`.
- `whatsmeow.GetGroupInviteLink(r.Context(), jid, req.Reset)`.
- `Reset` é `bool` simples (não ponteiro): omitido = `false` = não revoga, que é o default seguro.
- Sucesso: `Message` = `"invite link retrieved"` ou `"invite link reset"` conforme `Reset`.
- Erro: 500 no molde padrão. `ErrGroupInviteLinkUnauthorized` (não sou admin) chega como erro e vira 500 com a mensagem da lib — aceitável, a mensagem já é legível.

Python: `get_group_invite_link(group_jid: str, reset: bool = False) -> Tuple[bool, str, str]` → `(success, message, link)`.

### 2. `POST /api/group_invite_info`

```go
type GroupInviteInfoRequest struct {
    Link string `json:"link"`
}
type GroupInviteInfoResponse struct {
    Success      bool     `json:"success"`
    Message      string   `json:"message"`
    JID          string   `json:"jid,omitempty"`
    Name         string   `json:"name,omitempty"`
    Topic        string   `json:"topic,omitempty"`
    Participants []string `json:"participants,omitempty"`
    IsLocked     bool     `json:"is_locked"`
    IsAnnounce   bool     `json:"is_announce"`
}
```

- `link` obrigatório → 400 se vazio. **Não** validar formato de URL: `GetGroupInfoFromLink` já faz `stripURLPrefix`, então link completo ou só o código funcionam.
- `whatsmeow.GetGroupInfoFromLink(r.Context(), req.Link)`.
- `Participants`: `p.JID.String()` de cada `info.Participants`.
- **Não entra no grupo.** A docstring da tool precisa dizer isso.

Python: `get_group_invite_info(link: str) -> Tuple[bool, str, Optional[dict]]`.

### 3. `POST /api/join_group_with_link`

```go
type JoinGroupRequest struct {
    Link string `json:"link"`
}
type JoinGroupResponse struct {
    Success bool   `json:"success"`
    Message string `json:"message"`
    JID     string `json:"jid,omitempty"`
}
```

- `link` obrigatório → 400 se vazio.
- `whatsmeow.JoinGroupWithLink(r.Context(), req.Link)`.
- `JID` = JID devolvido. Se o grupo exige aprovação, a lib devolve o JID do pedido de entrada — `Message` deve deixar claro que pode ser pedido pendente: `"joined group (or membership request sent)"`.

Python: `join_group_with_link(link: str) -> Tuple[bool, str, str]` → `(success, message, jid)`.

---

## #13 — Administração de grupo

### 4. `POST /api/group_settings`

```go
// Todos os campos são ponteiros: omitido != zero-value (RN-05).
// topic:"" apaga o tópico no whatsmeow; announce:false e locked:false são valores legítimos.
type GroupSettingsRequest struct {
    GroupJID string  `json:"group_jid"`
    Name     *string `json:"name"`
    Topic    *string `json:"topic"`
    Announce *bool   `json:"announce"`
    Locked   *bool   `json:"locked"`
}
type GroupSettingResult struct {
    Field   string `json:"field"`
    Success bool   `json:"success"`
    Error   string `json:"error,omitempty"`
}
type GroupSettingsResponse struct {
    Success bool                 `json:"success"`  // true só se TODOS os campos pedidos aplicaram
    Message string               `json:"message"`
    Results []GroupSettingResult `json:"results,omitempty"`
}
```

- `group_jid` obrigatório e `@g.us` → senão 400.
- **Nenhum** dos 4 campos presente → 400 `"Invalid request: at least one of name, topic, announce, locked is required"` (RN-06).
- `Name` presente: aplicar a **mesma** validação de nome que `createWhatsAppGroup` já faz em `main.go` (conferir no código e reusar — não inventar limite novo). Falha → 400.
- Ordem fixa de aplicação: `name → topic → announce → locked`. Cada um vira uma entrada em `Results`; um erro **não** interrompe os seguintes.
  - `SetGroupName(ctx, jid, *req.Name)`
  - `SetGroupTopic(ctx, jid, "", "", *req.Topic)` — previousID/newID vazios: a lib busca/gera sozinha.
  - `SetGroupAnnounce(ctx, jid, *req.Announce)`
  - `SetGroupLocked(ctx, jid, *req.Locked)`
- `Success` = `true` apenas se todo `Results[i].Success` for `true`. `Message` = `"N of M setting(s) applied"`.
- HTTP **200** mesmo com falha parcial (o corpo carrega o detalhe); 503 só no guard de conexão.

Python: `update_group_settings(group_jid, name=None, topic=None, announce=None, locked=None) -> Tuple[bool, str, List[dict]]`.
⚠️ O helper monta o payload **omitindo** as chaves cujo argumento é `None` — nunca mandar `"name": null`.

### 5. `POST /api/group_photo`

```go
type GroupPhotoRequest struct {
    GroupJID  string `json:"group_jid"`
    MediaPath string `json:"media_path"`
    Remove    bool   `json:"remove"`
}
type GroupPhotoResponse struct {
    Success   bool   `json:"success"`
    Message   string `json:"message"`
    PictureID string `json:"picture_id,omitempty"`
}
```

- `group_jid` obrigatório e `@g.us` → senão 400.
- `remove=false` exige `media_path` não vazio; `remove=true` exige `media_path` **vazio** → os dois juntos ou nenhum dos dois → 400.
- `remove=false`: ler o arquivo com `os.ReadFile(media_path)`; erro de leitura → 400 com a mensagem do OS (o caminho é do host da bridge, não do cliente).
- `remove=true`: `avatar = nil`.
- `whatsmeow.SetGroupPhoto(r.Context(), jid, avatar)` → `PictureID`.
- JPEG. `ErrInvalidImageFormat` da lib vira 500 com mensagem legível — sem pré-validação de imagem na bridge.

Python: `set_group_photo(group_jid: str, media_path: str = "", remove: bool = False) -> Tuple[bool, str, str]` → `(success, message, picture_id)`.

---

## #10 — Info de usuário

### 6. `POST /api/user_info`

```go
type UserInfoRequest struct {
    JIDs []string `json:"jids"`
}
type UserInfoResult struct {
    Query        string   `json:"query"`
    JID          string   `json:"jid,omitempty"`
    Found        bool     `json:"found"`
    Status       string   `json:"status,omitempty"`
    PictureID    string   `json:"picture_id,omitempty"`
    VerifiedName string   `json:"verified_name,omitempty"`
    LID          string   `json:"lid,omitempty"`
    Devices      []string `json:"devices,omitempty"`
}
type UserInfoResponse struct {
    Success bool             `json:"success"`
    Message string           `json:"message"`
    Results []UserInfoResult `json:"results,omitempty"`
}
```

- `jids` vazio → 400. `len(jids) > maxUserInfoJIDs` (**const = 20**) → 400 `"Too many jids: max 20, got N"` (RN-09).
- Parsing de cada item: **reusar `parseGroupParticipantJIDs`** (`main.go:1127`) — ela já trata número nu (via `normalizePhone` + `DefaultUserServer`) e JID completo, e só aceita `DefaultUserServer`/`HiddenUserServer`, que é exatamente o domínio aqui. Falha → 400.
- `GetUserInfo(r.Context(), jids)` devolve `map[types.JID]types.UserInfo` e **pode omitir** entradas.
- Correlação obrigatória (RN-10), no espírito de `mergeIsOnWhatsAppResults` (`main.go:1324`): **um `UserInfoResult` por JID de entrada, na ordem de entrada**. `Query` = o JID parseado (`jid.String()`); ausente no map → `Found:false` e nada mais preenchido.
- `VerifiedName`: `info.VerifiedName != nil && info.VerifiedName.Details != nil` → `GetVerifiedName()` (mesmo cuidado de nil de `mergeIsOnWhatsAppResults`).
- `LID`: só preencher se `!info.LID.IsEmpty()`.
- `Devices`: `d.String()` de cada `info.Devices`.

Python: `get_user_info(jids: List[str]) -> Tuple[bool, str, List[dict]]`.

### 7. `POST /api/profile_picture`

```go
type ProfilePictureRequest struct {
    JID     string `json:"jid"`
    Preview bool   `json:"preview"`
}
type ProfilePictureResponse struct {
    Success    bool   `json:"success"`
    Message    string `json:"message"`
    URL        string `json:"url,omitempty"`
    ID         string `json:"id,omitempty"`
    Type       string `json:"type,omitempty"`
    DirectPath string `json:"direct_path,omitempty"`
}
```

- `jid` obrigatório → 400. Aceita usuário **ou** grupo: parsear com `types.ParseJID` e aceitar `DefaultUserServer`, `HiddenUserServer` e `GroupServer`; outro servidor → 400.
- `GetProfilePictureInfo(r.Context(), jid, &whatsmeow.GetProfilePictureParams{Preview: req.Preview})`.
- **RN-11 — resultado normal, não erro:**
  - `errors.Is(err, whatsmeow.ErrProfilePictureNotSet)` → **200** `{success:false, message:"no profile picture set"}`
  - `errors.Is(err, whatsmeow.ErrProfilePictureUnauthorized)` → **200** `{success:false, message:"profile picture hidden by privacy settings"}`
  - qualquer outro erro → 500 no molde padrão.
- `info == nil` sem erro → 200 `{success:false, message:"no profile picture available"}` (defensivo).
- **Não baixar a imagem** — devolver `URL` (RN-12).

Python: `get_profile_picture(jid: str, preview: bool = False) -> Tuple[bool, str, Optional[dict]]`.

---

## Tools MCP (`main.py`)

Uma tool por endpoint, embrulhando o helper, retornando `Dict[str, Any]` com `{"success", "message", ...}` — molde `update_group_participants` (`main.py:444`).

| Tool | Assinatura | Ponto obrigatório da docstring |
|------|-----------|--------------------------------|
| `get_group_invite_link` | `(group_jid: str, reset: bool = False)` | **Primeira linha após o resumo:** `reset=True` revoga o link antigo e quem tiver ele perde o acesso |
| `get_group_invite_info` | `(link: str)` | Só consulta — **não entra** no grupo. Use antes de `join_group_with_link` |
| `join_group_with_link` | `(link: str)` | Entra de fato. Grupo com aprovação → vira pedido pendente |
| `update_group_settings` | `(group_jid, name=None, topic=None, announce=None, locked=None)` | Campo omitido não muda; `topic=""` **apaga** o tópico; resultado é por campo e pode ser parcial; exige ser admin |
| `set_group_photo` | `(group_jid, media_path="", remove=False)` | Caminho é lido **pela bridge** (host dela), JPEG; `remove=True` tira a foto |
| `get_user_info` | `(jids: List[str])` | Máx. 20 por chamada; `found=false` quando o WhatsApp não devolveu dados |
| `get_profile_picture` | `(jid, preview=False)` | Devolve URL para download, não a imagem; `success=false` legítimo quando não há foto ou está oculta; aceita JID de grupo |
