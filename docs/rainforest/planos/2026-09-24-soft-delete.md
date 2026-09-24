# Plano: Soft delete — mensagem apagada mantém conteúdo e pode ser consultada

Design: docs/rainforest/design/2026-09-24-soft-delete.md

## O que não pode quebrar
- Leitura normal de mensagem apagada continua `[mensagem apagada]` sem mídia; `download_media` continua recusando.
- Mensagem comum (não apagada, não editada) sai idêntica em todas as leituras.
- `StoreMessage` segue sem reescrever linha revogada e com o `COALESCE` de transcrição.
- A ponte sobe com o banco que já existe (`previous_content` entra por `ensureMessagesSchema`).
- Nenhum telefone/JID real em teste; `python scripts/check-personal-data.py` sai 0.

## Tarefas

### 1. Revogar só marca, e nenhuma leitura normal vaza o conteúdo guardado [tipo: implementar]
atende: D1, D2
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

`MarkMessageRevoked` passa a só gravar `revoked_at` (e não apaga o cache em disco em `applyProtocolMessage`). Todo caminho de leitura normal esconde: busca por texto com `messages.revoked_at IS NULL`; `last_message` de `listChats`, `getChat` e o chat por contato via `CASE WHEN ... revoked_at IS NOT NULL THEN '[mensagem apagada]' ELSE ... content END`; `GetMessageForQuote` recusa mensagem revogada. Ajustar os testes do #22 que exigiam conteúdo limpo/cache apagado para o novo contrato (conteúdo guardado no banco, escondido na leitura). A linha da busca tem de ser exatamente:
`		whereClauses = append(whereClauses, "unaccent(messages.content) LIKE unaccent(?) AND messages.revoked_at IS NULL")`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `		whereClauses = append(whereClauses, "unaccent(messages.content) LIKE unaccent(?) AND messages.revoked_at IS NULL")`
  para: `		whereClauses = append(whereClauses, "unaccent(messages.content) LIKE unaccent(?)")`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestSoftDelete -v .`
  fixture: `TestSoftDelete/busca_nao_casa_com_apagada` — imagem com legenda revogada; busca pela legenda na conexão de leitura com unaccent devolve 0

pronto quando: com uma imagem com legenda salva e revogada por `applyProtocolMessage`, o banco ainda tem `content` e `media_key` da linha e o arquivo em cache existe, enquanto `listMessages`, `getMessageContext`, o `last_message` de `listChats`/`getChat`, a busca pela legenda e `GetMessageForQuote` não expõem a legenda nem a mídia — provado por `cd whatsapp-bridge && go test -count=1 -run TestSoftDelete -v .` devolvendo `PASS` em `conteudo_guardado_no_banco`, `leituras_escondem`, `busca_nao_casa_com_apagada`, `last_message_esconde` e `citar_apagada_recusa`.

### 2. Edição guarda o texto original [tipo: implementar]
atende: D4
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

Coluna `previous_content` (CREATE TABLE + `ensureMessagesSchema`); `ApplyMessageEdit` grava `previous_content = COALESCE(previous_content, content)` antes de trocar `content`. A atribuição tem de ser exatamente:
`		"UPDATE messages SET previous_content = COALESCE(previous_content, content), content = ?, edited_at = ? WHERE id = ? AND chat_jid = ? AND revoked_at IS NULL",`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `		"UPDATE messages SET previous_content = COALESCE(previous_content, content), content = ?, edited_at = ? WHERE id = ? AND chat_jid = ? AND revoked_at IS NULL",`
  para: `		"UPDATE messages SET previous_content = COALESCE(previous_content, NULL), content = ?, edited_at = ? WHERE id = ? AND chat_jid = ? AND revoked_at IS NULL",`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestEditPreservesOriginal -v .`
  fixture: `TestEditPreservesOriginal/duas_edicoes_guardam_o_original`

pronto quando: com um texto salvo e editado duas vezes por `MESSAGE_EDIT`, a linha tem `content` igual à última edição e `previous_content` igual ao texto de antes da primeira — provado por `cd whatsapp-bridge && go test -count=1 -run TestEditPreservesOriginal -v .` devolvendo `PASS` em `duas_edicoes_guardam_o_original`.

### 3. Consulta explícita do apagado/editado na bridge [tipo: implementar]
atende: D3
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 2
paralela: nao

Endpoint `POST /api/deleted_message` (`message_id`, `chat_jid`, `download`): devolve `content` original, `previous_content`, `media_type`, `revoked_at`, `edited_at`; com `download: true` baixa a mídia por `downloadMedia` com a recusa de revogada desligada só para esta chamada (parâmetro explícito, não global). Mensagem nem revogada nem editada → 404 com mensagem clara. Função testável `getDeletedMessage(db, req)` separada do handler. A recusa de revogada no `downloadMedia` tem de ficar exatamente:
`	if !allowRevoked {`

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: `	if !allowRevoked {`
  para: `	if true {`
  bateria: `cd whatsapp-bridge && go test -count=1 -run TestDeletedMessageEndpoint -v .`
  fixture: `TestDeletedMessageEndpoint/download_da_apagada_pela_consulta` — imagem revogada com arquivo em cache; `downloadMedia(..., allowRevoked=true)` devolve o arquivo

pronto quando: com uma imagem com legenda revogada e um texto editado no store, `getDeletedMessage` devolve a legenda original com `revoked_at`, e o texto editado devolve `previous_content`; uma mensagem comum é recusada; `downloadMedia` com a flag explícita devolve o arquivo em cache e sem a flag segue recusando — provado por `cd whatsapp-bridge && go test -count=1 -run TestDeletedMessageEndpoint -v .` devolvendo `PASS` em `apagada_devolve_original`, `editada_devolve_anterior`, `comum_recusada` e `download_da_apagada_pela_consulta`.

### 4. Tool MCP get_deleted_message e varredura ignorando apagada [tipo: implementar]
atende: D2, D3
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/transcribe.py`, `whatsapp-mcp-server/recover_audios.py`, `whatsapp-mcp-server/test_deleted_message.py`
depende de: 3
paralela: nao

Tool `get_deleted_message(message_id, chat_jid, download=False, account=None)` chamando o endpoint da tarefa 3, com docstring dizendo que é o único caminho para ver o apagado e que só deve ser usada quando o usuário pedir. `pending_audios` (`transcribe.py`) e a consulta de `recover_audios.py` ganham `AND revoked_at IS NULL`. A condição em `transcribe.py` tem de conter exatamente `AND revoked_at IS NULL`.

mutacao:
  arquivo: `whatsapp-mcp-server/transcribe.py`
  de: `AND revoked_at IS NULL`
  para: ``
  bateria: `cd whatsapp-mcp-server && uv run python -m unittest test_deleted_message -v`
  fixture: `test_deleted_message.TestSweepSkipsRevoked.test_audio_apagado_fora_da_varredura`

pronto quando: com um SQLite de teste com o schema real (áudio pendente apagado e áudio pendente comum), `pending_audios` devolve só o comum; a tool `get_deleted_message` repassa o conteúdo do endpoint e a recusa dele — provado por `cd whatsapp-mcp-server && uv run python -m unittest test_deleted_message -v` devolvendo `OK`.

### 5. Validação real entre as contas pessoal e trabalho [tipo: teste]
atende: D1, D2, D3, D4, D5
arquivos: `docs/rainforest/estado/2026-09-24-soft-delete.json`
depende de: 4
paralela: nao

Binário recompilado e as duas pontes reiniciadas pelo PID da porta, com backup; servidor MCP reconectado pelo usuário (`/mcp`). A imagem apagada no teste do #22 (10:20) segue sem conteúdo — é a perda aceita do D5, e a evidência registra isso. No grupo de teste: `trabalho` manda uma imagem e um texto; `pessoal` baixa a imagem; `trabalho` edita o texto e apaga a imagem.

mutacao: n/a
  motivo: validação de ambiente real; não há linha de código nova a inverter nesta tarefa.

pronto quando: com a imagem e o texto reais enviados pela conta `trabalho`, na conta `pessoal` o `list_messages` mostra a imagem como `[mensagem apagada]` e o texto editado, `download_media` recusa, e `get_deleted_message` devolve o tipo de mídia e o horário da exclusão da imagem (com `download=True` devolvendo o arquivo) e o texto original do texto editado — provado pelas saídas das tools MCP coladas na evidência do `verificar`.
