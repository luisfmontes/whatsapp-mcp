# whatsmeow → MCP: análise de features ausentes

**Data:** 2026-07-07
**whatsmeow:** `v0.0.0-20260516102357-8d3700152a69`
**Camadas:** whatsmeow (lib Go) → bridge REST (`main.go`) → tools MCP (`main.py`)

## Estado atual (o que já existe)

> **Atualizado em 2026-08-08** pelo ciclo `004-whatsmeow-gaps-10-12-13`, que cobriu os gaps #10, #12 e #13.

**32 tools MCP:** `search_contacts`, `list_messages`, `list_chats`, `get_chat`, `get_direct_chat_by_contact`, `get_contact_chats`, `get_last_interaction`, `get_message_context`, `send_message`, `send_file`, `send_audio_message`, `download_media`, `create_group`, `leave_group`, `mark_chat_as_read`, `mark_chat_as_unread`, `get_group_info`, `archive_chat`, `resolve_contact`, `react_to_message`, `edit_message`, `delete_message`, `update_group_participants`, `send_chat_presence`, `check_whatsapp`, `get_group_invite_link`, `get_group_invite_info`, `join_group_with_link`, `update_group_settings`, `set_group_photo`, `get_user_info`, `get_profile_picture`.

**26 REST handlers:** `/api/send`, `/api/download`, `/api/mediaretry`, `/api/create_group`, `/api/group_info`, `/api/leave_group`, `/api/mark_chat_read`, `/api/mark_chat_unread`, `/api/archive_chat`, `/api/resolve_contact`, `/api/search_contacts`, `/api/react`, `/api/edit`, `/api/revoke`, `/api/group_participants`, `/api/chat_presence`, `/api/is_on_whatsapp`, `/api/group_invite_link`, `/api/group_invite_info`, `/api/join_group_with_link`, `/api/group_settings`, `/api/group_photo`, `/api/user_info`, `/api/profile_picture` (+ `/qr`, `/qr.png`).

## Legenda de esforço

| Tier | Significado | Trabalho |
|------|-------------|----------|
| **A** | REST handler **já existe** na bridge; falta só wrapper Python | ~1 helper em `whatsapp.py` + `@mcp.tool` em `main.py`. **Zero Go.** |
| **B** | Método whatsmeow existe; sem REST handler | Handler Go (`main.go`) + helper + tool Python |
| **C** | Pesado/nicho; API existe mas custo alto ou pouco uso pessoal | Handler Go não-trivial + modelagem |

---

## Tabela de gaps (priorizada)

| # | Feature | whatsmeow | REST hoje | MCP hoje | Tier | Valor |
|---|---------|-----------|-----------|----------|------|-------|
| 1 | **Group info** (metadados, participantes) | `GetGroupInfo` | ✅ `/api/group_info` | ❌ | **A** | Alto |
| 2 | **Arquivar/desarquivar chat** | `SendAppState` | ✅ `/api/archive_chat` | ❌ | **A** | Médio |
| 3 | **Resolver contato** (QR/link → JID) | `ResolveContactQRLink` | ✅ `/api/resolve_contact` | ❌ | **A** | Médio |
| 4 | **Reagir a mensagem** (emoji) | `BuildReaction`+`SendMessage` | ❌ | ❌ | **B** | Alto |
| 5 | **Editar mensagem** | `BuildEdit`+`SendMessage` | ❌ | ❌ | **B** | Alto |
| 6 | **Apagar p/ todos** (revoke) | `BuildRevoke`/`RevokeMessage` | ❌ | ❌ | **B** | Alto |
| 7 | **Gerenciar participantes** (add/remove/promote/demote) | `UpdateGroupParticipants` | ✅ | ✅ | **B** | **Alto — maior gap único** |
| 8 | **Typing/presença no chat** | `SendChatPresence` | ✅ | ✅ | **B** | Alto (bot mais natural) |
| 9 | **Está no WhatsApp?** (valida número) | `IsOnWhatsApp` | ✅ | ✅ | **B** | Alto |
| 10 | **Info de usuário** (status, pic, devices) | `GetUserInfo`/`GetProfilePictureInfo` | ✅ | ✅ | **B** | Médio |
| 11 | **Enquete** (criar + ler votos) | `BuildPollCreation`/`DecryptPollVote` | ❌ | ❌ | **B** | Médio |
| 12 | **Link de convite do grupo** (get/join) | `GetGroupInviteLink`/`JoinGroupWithLink` | ✅ | ✅ | **B** | Médio |
| 13 | **Setters de grupo** (nome/tópico/foto/anúncio/locked) | `SetGroupName`/`SetGroupTopic`/`SetGroupPhoto`/`SetGroupAnnounce`/`SetGroupLocked` | ✅ | ✅ | **B** | Médio |
| 14 | **Read receipt explícito** (marcar lida via app-state ✅; receipt real ❌) | `MarkRead` | parcial | parcial | **B** | Médio |
| 15 | **Presença global** (online/offline) | `SendPresence`/`SubscribePresence` | ❌ | ❌ | **B** | Baixo |
| 16 | **Bloquear/desbloquear** | `UpdateBlocklist`/`GetBlocklist` | ❌ | ❌ | **B** | Baixo |
| 17 | **Business profile** | `GetBusinessProfile` | ❌ | ❌ | **B** | Baixo |
| 18 | **Mensagens efêmeras** (timer) | `SetDisappearingTimer` | ❌ | ❌ | **C** | Baixo |
| 19 | **Newsletters/Canais** (follow/read/react) | `FollowNewsletter`, `GetNewsletterMessages`, … | ❌ | ❌ | **C** | Baixo (nicho) |
| 20 | **Proxy / privacy settings** | `SetProxy`, `GetPrivacySettings` | ❌ | ❌ | **C** | Baixo |

> Excluído como plumbing (não é "feature"): `Connect/Disconnect/ResetConnection`, `Upload*/Download*` (uso interno), `Encrypt*/Decrypt*`, `Build*` internos, `AddEventHandler*`, `Set*HTTPClient`, `DangerousInternals`, `PairPhone`, `Logout`.

---

## Histórico de colheita

| Ciclo | Gaps | Entregue em |
|-------|------|-------------|
| `002-whatsmeow-quick-wins` | #1, #2, #3 (Tier A) + #4, #5, #6 | 2026-07-07 |
| `003-whatsmeow-gaps-7-8-9` | #7, #8, #9 | 2026-07-09 |
| `004-whatsmeow-gaps-10-12-13` | #10, #12, #13 | 2026-08-08 |

Todos seguiram o mesmo shape: handler REST curto na bridge → helper `whatsapp.py` → tool `@mcp.tool`.

## O que ainda falta

### Próximo, se houver demanda — #11 Enquete
Único Tier B restante. **Não** é do mesmo shape dos anteriores: criar a enquete é trivial
(`BuildPollCreation`), mas *ler votos* exige event handler para `events.Message` com voto e
persistir as opções da enquete para conseguir decriptar (`DecryptPollVote`). Muda o modelo de
dados — merece ciclo próprio, não entra de carona num PR de handlers.

### Adiar — Tier C e baixo valor
#14 read receipt explícito, #15 presença global, #16 bloquear/desbloquear, #17 business profile,
#18 mensagens efêmeras, #19 newsletters/canais, #20 proxy/privacy. API existe, uso pessoal baixo.
Só sob demanda.

### Manutenção, independente de feature
Bump do `go.mau.fi/whatsmeow` (pinado em `v0.0.0-20260529101937-a7ea56383ec4`). Deliberadamente
fora dos ciclos de feature: misturar os dois torna impossível atribuir uma quebra à lib ou ao
código novo.
