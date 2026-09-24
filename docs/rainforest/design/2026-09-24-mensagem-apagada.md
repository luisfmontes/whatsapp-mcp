# Mensagem apagada ou editada pelo remetente (issue #21)

## Objetivo
Uma mensagem apagada "para todos" depois de armazenada passa a aparecer como apagada nas leituras e tem o download recusado; uma mensagem editada passa a mostrar o texto novo. Hoje o `handleMessage` ignora o `ProtocolMessage` e as duas coisas se perdem.

## Decisões fechadas
- **D1 — Apagada: a linha fica, o conteúdo sai** — `ProtocolMessage` `REVOKE` marca `revoked_at` na linha `(Key.ID, chat do evento)` e limpa `content`, `media_type`, `filename`, `url`, chaves de mídia, citação e menções. As leituras (`/api/messages`, `/api/message_context`, `/api/contacts/last_interaction`) mostram `[mensagem apagada]` com `revoked: true`, sem `media_type`. O `StoreMessage` não reescreve linha revogada (re-sync não ressuscita). O `download_media` recusa antes do atalho de cache local. — porquê: a conversa mantém a lacuna no lugar e o que o remetente retirou não fica guardado; manter o conteúdo só marcado deixaria o dado acessível.
- **D2 — Edição recebida entra no mesmo trabalho** — `ProtocolMessage` `MESSAGE_EDIT` troca `content` pelo texto editado e grava `edited_at`; a leitura expõe `edited: true`. Edição não toca linha revogada nem apaga conteúdo com texto vazio. — porquê: mesma causa e mesmo ponto do código; o whatsmeow já entrega a edição desembrulhada como `ProtocolMessage`.
- **D3 — Ação própria pelo MCP também atualiza o store** — `/api/revoke` e `/api/edit`, depois do `SendMessage` bem-sucedido, aplicam a mesma marcação localmente. — porquê: conta single-device não recebe o eco da própria ação, e o banco ficaria com a versão antiga.
- **D4 — Mídia em cache é apagada do disco** — ao revogar, o arquivo `store/<chat>/<id>_<filename>` (via `safeMediaPath`) é removido, best-effort. — porquê: a imagem sai do disco, não só do banco.
- **D5 — Escopo pelo chat do evento** — o alvo é `Key.ID` restrito ao `chat_jid` em que o protocolo chegou (já normalizado LID→PN). — porquê: um protocolo não pode tocar mensagem de outra conversa.

## Avaliado e descartado
- Apagar a linha do banco: some a lacuna na conversa e o `get_message_context` de mensagens vizinhas passa a mentir sobre a sequência.
- Só marcar `revoked_at` sem limpar: o conteúdo continuaria na tabela e em busca por texto, que é o dado que o remetente retirou.

## Fora de escopo
- Mensagens apagadas antes da correção (ex.: a de 24/09 09:25:48): o evento de revogação já se perdeu e não há como detectá-las; a correção vale do reinício da bridge em diante.
- Servidor MCP Python: lê tudo via REST da bridge; marcador no conteúdo e recusa do download bastam. Exceção achada na validação real (tarefa 5 do plano): a dica de falha do `download_media` mandava procurar o arquivo na pasta de downloads, o que para mensagem apagada é ir atrás do que o remetente retirou.
- `last_message` do `list_chats` para chat cuja última mensagem foi apagada: mostra texto vazio, não o marcador.

## Em aberto
