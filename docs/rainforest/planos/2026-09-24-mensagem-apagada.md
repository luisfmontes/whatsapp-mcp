# Plano: Mensagem apagada ou editada pelo remetente (issue #21)

Design: docs/rainforest/design/2026-09-24-mensagem-apagada.md

## O que não pode quebrar

- A ponte sobe com o banco que já existe: `revoked_at`/`edited_at` entram por `ensureMessagesSchema`, sem perder linha.
- `StoreMessage` continua preservando `content` existente via `COALESCE(NULLIF(...))` (transcrição de áudio); o único acréscimo é o `WHERE messages.revoked_at IS NULL` no `DO UPDATE`.
- Mensagem comum (texto, mídia, enquete, voto) segue o caminho de hoje: `applyProtocolMessage` só consome `REVOKE` e `MESSAGE_EDIT`.
- Nenhum telefone ou JID real em teste, doc ou resposta; `python scripts/check-personal-data.py` sai 0.
- Nenhuma tarefa escreve nos stores reais fora do uso normal da ponte; migração conferida numa cópia no scratchpad.

## Tarefas

### 1. REVOKE apaga o conteúdo, marca a linha e recusa o download [tipo: implementar]
atende: D1, D4, D5
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

Colunas `revoked_at`/`edited_at` (CREATE TABLE + `ensureMessagesSchema`); `MarkMessageRevoked`, `IsMessageRevoked`, `applyProtocolMessage` chamado no `handleMessage` antes da extração de conteúdo; `APIMessage.Revoked` + `applyMessageFlags` nas quatro leituras; `downloadMedia` recusa antes do atalho de cache; arquivo em cache removido; `StoreMessage` não reescreve linha revogada.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `} else if revoked {`
  para: `} else if revoked && false {`
  bateria: `cd whatsapp-bridge && go test -run TestRevokedMessage ./...`
  fixture: `TestRevokedMessage/download_recusado_mesmo_com_cache` — mensagem de imagem salva com arquivo já em cache, REVOKE sintético, `downloadMedia` tem de devolver erro

pronto quando: com uma mensagem de imagem com legenda já salva no store e um `ProtocolMessage{Type: REVOKE, Key.ID: <id dela>}` chegando no mesmo chat, `listMessages` devolve essa linha com `content == "[mensagem apagada]"`, `revoked == true` e `media_type == nil`, a busca pela legenda não a acha, `downloadMedia` devolve erro mesmo com o arquivo em cache (e o arquivo some do disco), um REVOKE no mesmo id vindo de outro chat não toca a linha, e um `StoreMessage` posterior com o conteúdo original não a ressuscita — provado por `cd whatsapp-bridge && go test -run TestRevokedMessage -v ./...` devolvendo `PASS` em todos os subtestes e zero `SKIP`.

### 2. MESSAGE_EDIT troca o conteúdo e marca edited [tipo: implementar]
atende: D2
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

`ApplyMessageEdit` e o ramo `MESSAGE_EDIT` de `applyProtocolMessage`; `APIMessage.Edited`.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `case waProto.ProtocolMessage_MESSAGE_EDIT:`
  para: `case waProto.ProtocolMessage_Type(-1):`
  bateria: `cd whatsapp-bridge && go test -run TestEditedMessage ./...`
  fixture: `TestEditedMessage/conteudo_trocado` — texto salvo, `ProtocolMessage{Type: MESSAGE_EDIT, EditedMessage: Conversation "novo"}`, `listMessages` tem de devolver "novo" com `edited == true`

pronto quando: com um texto já salvo e um `MESSAGE_EDIT` apontando para ele no mesmo chat, `listMessages` devolve o texto editado com `edited == true`; edição sobre linha revogada não a altera e edição com texto vazio não apaga o conteúdo — provado por `cd whatsapp-bridge && go test -run TestEditedMessage -v ./...` devolvendo `PASS` em todos os subtestes.

### 3. /api/revoke e /api/edit atualizam o store local [tipo: implementar]
atende: D3
arquivos: `whatsapp-bridge/main.go`
depende de: 2
paralela: nao

Depois do `SendMessage` bem-sucedido, `handleRevoke` chama `applyProtocolMessage` com um `REVOKE` do id e `handleEdit` com um `MESSAGE_EDIT` com o texto novo, escopo `req.ChatJID`. `handleEdit` passa a receber o `messageStore`.

mutacao: n/a
  motivo: o ramo só roda depois de um `SendMessage` real contra o WhatsApp, que exige cliente conectado; a lógica que ele chama é a das tarefas 1 e 2, já com mutação própria, e a prova deste ramo é a tarefa 4 contra as contas reais.

pronto quando: com a conta `trabalho` apagando pelo `delete_message` uma mensagem que ela mesma enviou, o `list_messages` da própria conta `trabalho` mostra essa mensagem como `[mensagem apagada]` — provado pela tarefa 4.

### 4. Validação real entre as contas pessoal e trabalho [tipo: teste]
atende: D1, D2, D3, D4
arquivos: `docs/rainforest/estado/2026-09-24-mensagem-apagada.json`
depende de: 3
paralela: nao

Binário recompilado e as duas pontes reiniciadas pelo PID da porta de cada uma (nunca por nome). No único grupo entre as duas contas (descoberto por `list_chats`, nunca escrito em arquivo versionado): `trabalho` manda uma imagem com legenda e um texto; `pessoal` confirma que recebeu; `trabalho` edita o texto e apaga a imagem pelo MCP.

mutacao: n/a
  motivo: validação de ambiente real; não há linha de código nova a inverter nesta tarefa.

pronto quando: com a imagem e o texto reais enviados pela conta `trabalho`, a conta `pessoal` mostra no `list_messages` a imagem como `[mensagem apagada]` sem tipo de mídia e o texto com a versão editada, o `download_media` dela é recusado, e a conta `trabalho` mostra o mesmo no próprio `list_messages` — provado pelas saídas das tools MCP `list_messages`/`download_media` das duas contas coladas na evidência do `verificar`.
