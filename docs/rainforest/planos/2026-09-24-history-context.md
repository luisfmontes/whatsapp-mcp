# Plano: History sync grava autor, citação e menções (issue #28)

Design: docs/rainforest/design/2026-09-24-history-context.md

## O que não pode quebrar
- `sender` (coluna curta) do sync fica idêntico ao de hoje.
- Stub REVOKE, `ProtocolMessage` pendente (#26) e desembrulho (#25) seguem iguais.
- Nenhum telefone/JID real em teste; `python scripts/check-personal-data.py` sai 0.

## Tarefas

### 1. Sync grava sender_jid, citação e menções [tipo: implementar]
atende: D1, D2, D3, D4
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: não

`historySyncSender` devolve `(sender, senderJID string, isFromMe bool)` pelo D1. Em `storeHistoryConversation`, depois do `StoreMessage` bem-sucedido: `StoreMessageSenderJID(msgID, chatJID, senderJID)` e, se `extractContextInfo(inner) != nil`, `StoreMessageContext`. Linha exata do participante:
`			senderJID = pn.String()`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `			senderJID = pn.String()`
  para: `			senderJID = ""`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncContext -v .`
  fixture: `TestHistorySyncContext/grupo_resposta_com_mencao`

pronto quando: com uma conversa de grupo de history sync em que uma mensagem de outro participante (em `EphemeralMessage`) responde a outra e menciona alguém, `storeHistoryConversation` grava `sender_jid` do participante, `quoted_message_id` e `mentions`, e `buildQuoteContextInfo` aceita citá-la; mensagem própria grava o JID da conta sem aparelho; conversa 1:1 grava o JID do chat; grupo sem participante fica com `sender_jid` vazio — provado por `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncContext -v .` devolvendo `PASS` em `grupo_resposta_com_mencao`, `propria_sem_aparelho`, `conversa_1a1` e `grupo_sem_participante`, e ficando vermelho com a mutação acima.
