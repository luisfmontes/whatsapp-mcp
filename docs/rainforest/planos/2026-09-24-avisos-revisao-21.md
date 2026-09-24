# Plano: Mídia apagada regravada pelo mediaretry e chat_jid sem normalizar em revoke/edit

Design: docs/rainforest/design/2026-09-24-avisos-revisao-21.md

## O que não pode quebrar
- Contrato de log do mediaretry: toda saída terminal continua sendo exatamente uma de `SUCCESS`, `NOTONPHONE`, `ERROR` (lida por `whatsapp-mcp-server/recover_audios.py`).
- Recuperação de mídia de mensagem NÃO apagada continua gravando o arquivo e logando `SUCCESS`.
- `SendMessage` de revoke/edit segue para o `chatJID` do chamador; só a chave do store local muda.
- Nenhum telefone/JID real em teste; `python scripts/check-personal-data.py` sai 0.

## Tarefas

### 1. mediaretry não regrava mídia de mensagem apagada [tipo: implementar]
atende: D1
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

Extrair de `handleMediaRetry` a gravação para `writeRecoveredMedia(messageStore *MessageStore, messageID, chatJID, filename string, data []byte) (string, error)`: consulta `IsMessageRevoked` e, se revogada, devolve erro `message was deleted by the sender` sem criar arquivo; senão faz o `MkdirAll` + `safeMediaPath` + `WriteFile` de hoje. `handleMediaRetry` loga `ERROR <erro>` ou `SUCCESS` como hoje.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `	if deleted {`
  para: `	if deleted && false {`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestWriteRecoveredMedia -v .`
  fixture: `TestWriteRecoveredMedia/apagada_nao_grava` — mensagem de imagem salva, REVOKE aplicado, `writeRecoveredMedia` tem de devolver erro e o arquivo não pode existir

pronto quando: com uma mensagem de imagem salva e revogada por `applyProtocolMessage`, os bytes que a resposta do telefone traria não chegam ao disco e o erro diz `deleted by the sender`; com a mesma mensagem não revogada, o arquivo é gravado em `store/<chat>/<id>_<filename>` — provado por `cd whatsapp-bridge && go test -count=1 -run TestWriteRecoveredMedia -v .` devolvendo `PASS` em `apagada_nao_grava` e `nao_apagada_grava`.

### 2. revoke/edit aplicam no store pela chave normalizada [tipo: implementar]
atende: D2
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

Função `localChatKey(client *whatsmeow.Client, chatJID types.JID) string` = `resolveToPN(client, chatJID).String()`, usada nas chamadas de `applyProtocolMessage` de `handleRevoke` e `handleEdit` no lugar de `req.ChatJID`.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `	return resolveToPN(client, chatJID).String()`
  para: `	return chatJID.String()`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestLocalChatKey -v .`
  fixture: `TestLocalChatKey/lid_vira_pn` — `whatsmeow.Client` com `Store.LIDs` falso mapeando um `@lid` sintético para um PN sintético; `localChatKey` do `@lid` tem de devolver o PN

pronto quando: com um `chat_jid` em forma `@lid` cujo PN o store de LIDs conhece, `localChatKey` devolve o `@s.whatsapp.net` com que `handleMessage` gravou a linha, e um JID já PN ou de grupo volta inalterado — provado por `cd whatsapp-bridge && go test -count=1 -run TestLocalChatKey -v .` devolvendo `PASS` em `lid_vira_pn` e `pn_e_grupo_inalterados`, e `grep -n "localChatKey(client" whatsapp-bridge/main.go` mostrando as chamadas em `handleRevoke` e `handleEdit`.
