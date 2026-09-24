# Plano: History sync aplica mensagem apagada ou editada (issue #23)

Design: docs/rainforest/design/2026-09-24-history-sync-revoke.md

## O que não pode quebrar
- Mensagem comum do history sync continua gravada como hoje (conteúdo, mídia, remetente, `COALESCE` que preserva transcrição).
- `StoreMessage` não ressuscita linha revogada (guard do #22).
- `SyncAllContacts` e a contagem `History sync complete. Stored %d messages.` seguem no fim do handler.
- Nenhum telefone/JID real em teste; `python scripts/check-personal-data.py` sai 0.

## Tarefas

### 1. Stub REVOKE do sync revoga ou grava linha revogada [tipo: implementar]
atende: D1, D2, D4
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

Extrair o corpo por conversa de `handleHistorySync` para `storeHistoryConversation(client *whatsmeow.Client, messageStore *MessageStore, jid types.JID, chatJID string, messages []*waHistorySync.HistorySyncMsg, logger waLog.Logger) int` (devolve quantas gravou), chamado pelo handler. No loop, antes do corte `content == "" && mediaType == ""`, a entrada com stub REVOKE (linha exata abaixo) chama `applyProtocolMessage` com REVOKE do `Key.ID` e depois `messageStore.StoreRevokedTombstone(id, chatJID, sender, timestamp, isFromMe)` — método novo: `INSERT ... ON CONFLICT(id, chat_jid) DO NOTHING` com `content = ''` e `revoked_at` preenchido. A linha do stub no código é exatamente:
`				if msg.Message.GetMessageStubType() == waWeb.WebMessageInfo_REVOKE {`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `				if msg.Message.GetMessageStubType() == waWeb.WebMessageInfo_REVOKE {`
  para: `				if msg.Message.GetMessageStubType() == waWeb.WebMessageInfo_REVOKE && false {`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncRevoke -v .`
  fixture: `TestHistorySyncRevoke/stub_revoga_linha_existente` — imagem com legenda já no store e em cache; sync com stub REVOKE do mesmo id; `listMessages` tem de dar `[mensagem apagada]` e o arquivo em cache sumir

pronto quando: com uma conversa de history sync montada com `waWeb.WebMessageInfo` reais — (a) stub REVOKE para uma imagem já salva e em cache, (b) stub REVOKE para um id ausente do store —, `storeHistoryConversation` deixa (a) como `[mensagem apagada]` sem mídia e sem o arquivo em disco, e (b) como uma linha nova `[mensagem apagada]` no chat — provado por `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncRevoke -v .` devolvendo `PASS` em `stub_revoga_linha_existente` e `stub_sem_linha_grava_revogada`.

### 2. ProtocolMessage do sync aplicado depois das mensagens [tipo: implementar]
atende: D3, D4
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

Em `storeHistoryConversation`, entrada cujo `msg.Message.GetMessage().GetProtocolMessage()` é REVOKE ou MESSAGE_EDIT não passa pelo `StoreMessage`: vai para `pending []*waProto.ProtocolMessage` (com seu timestamp); depois do loop de gravação, `for _, pm := range pending {` aplica cada um com `applyProtocolMessage(messageStore, chatJID, pm.pm, pm.at, logger)`. Use a linha exata:
`	for _, pm := range pending {`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `	for _, pm := range pending {`
  para: `	for _, pm := range pending[:0] {`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncProtocol -v .`
  fixture: `TestHistorySyncProtocol/revoke_antes_do_alvo` — conversa na ordem do sync (mais nova primeiro): `ProtocolMessage` REVOKE e depois a mensagem-alvo; o alvo tem de terminar `[mensagem apagada]`

pronto quando: com uma conversa na ordem real do sync (mais nova primeiro) em que um `ProtocolMessage` REVOKE e um MESSAGE_EDIT vêm antes das mensagens que alteram, `storeHistoryConversation` termina com a primeira como `[mensagem apagada]` e a segunda com o texto editado e `edited == true`, e nenhum dos dois protocolos vira linha própria — provado por `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncProtocol -v .` devolvendo `PASS` em `revoke_antes_do_alvo`, `edit_antes_do_alvo` e `protocolo_nao_vira_linha`.

### 3. Protocolo embrulhado no sync é detectado [tipo: implementar]
atende: D5
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 2
paralela: nao

Achado da revisão: `unwrapHistoryMessage` (via `events.Message.UnwrapRaw`) antes do `GetProtocolMessage()` em `storeHistoryConversation`; subtestes `revoke_embrulhado_em_device_sent` e `protocolo_sem_alvo_nao_cria_linha`.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `	return (&events.Message{RawMessage: m}).UnwrapRaw().Message`
  para: `	return m`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncProtocol -v .`
  fixture: `TestHistorySyncProtocol/revoke_embrulhado_em_device_sent`

pronto quando: com um REVOKE do sync embrulhado em `DeviceSentMessage` (revogação feita em outro aparelho do usuário) e um MESSAGE_EDIT no formato de fio que o próprio `BuildEdit` do whatsmeow produz (`EditedMessage` embrulhando o `ProtocolMessage`), os dois antes do alvo, o primeiro alvo termina `[mensagem apagada]` e o segundo com o texto editado — provado por `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncProtocol -v .` devolvendo `PASS` em `revoke_embrulhado_em_device_sent` e `edit_no_formato_do_buildedit`, e os dois ficando vermelhos com a mutação acima. `protocolo_sem_alvo_nao_cria_linha` é guarda de D2/D3 (alvo que nunca chega), não prova de D5.
