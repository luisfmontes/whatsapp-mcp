# Plano: History sync desembrulha mensagem comum (issue #25)

Design: docs/rainforest/design/2026-09-24-history-unwrap.md

## O que não pode quebrar
- Mensagem não embrulhada do sync grava idêntica a hoje.
- Detecção de stub REVOKE e de `ProtocolMessage` (#26) segue igual.
- Nenhum telefone/JID real em teste; `python scripts/check-personal-data.py` sai 0.

## Tarefas

### 1. Texto e mídia do sync vêm da mensagem desembrulhada [tipo: implementar]
atende: D1, D3, D4
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

Em `storeHistoryConversation`, `inner := unwrapHistoryMessage(msg.Message.Message)` antes da extração, e `extractTextContent(inner)`/`extractMediaInfo(inner)`. Linha exata:
`		inner := unwrapHistoryMessage(msg.Message.Message)`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `		inner := unwrapHistoryMessage(msg.Message.Message)`
  para: `		inner := msg.Message.Message`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncUnwrap -v .`
  fixture: `TestHistorySyncUnwrap/texto_em_ephemeral`

pronto quando: com uma conversa de history sync em que um texto e uma imagem com legenda vêm em `EphemeralMessage` e um texto vem em `DeviceSentMessage`, `storeHistoryConversation` grava os três, com o texto, a legenda e `media_type == "image"` — provado por `cd whatsapp-bridge && go test -count=1 -run TestHistorySyncUnwrap -v .` devolvendo `PASS` em `texto_em_ephemeral`, `imagem_com_legenda_em_ephemeral` e `texto_em_device_sent`, e ficando vermelho com a mutação acima.

### 2. Issue para sender_jid, citação e menções no sync [tipo: docs]
atende: D2
arquivos: `docs/rainforest/estado/2026-09-24-history-unwrap.json`
depende de: nenhuma
paralela: sim

mutacao: n/a
  motivo: abertura de issue; não há comportamento de código a inverter.

pronto quando: com a lacuna verificada no código (`storeHistoryConversation` não chama `StoreMessageSenderJID` nem `StoreMessageContext`), existe uma issue aberta no repositório descrevendo-a com critério falsificável — provado por `gh issue list --search "history sync sender_jid" --state open` devolvendo a issue.
