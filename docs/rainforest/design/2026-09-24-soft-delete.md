# Soft delete: mensagem apagada mantém conteúdo no banco e pode ser consultada

## Objetivo
Por sugestão do Rodrigo (dono do upstream): mensagem apagada para todos continua no banco com conteúdo e mídia — só marcada —, e o usuário pode pedir explicitamente para ver o que a pessoa apagou ou o texto antes de uma edição. As leituras normais continuam escondendo o apagado, que é o que resolve a #21.

## Decisões fechadas
- **D1 — Revogar só marca** — `MarkMessageRevoked` grava `revoked_at` e não limpa mais conteúdo, mídia, chaves, citação nem menções; o arquivo em cache no disco fica. Vale para o caminho live, para `/api/revoke` e para o history sync (#26), que usam o mesmo método. — porquê: pedido do Rodrigo; o dado precisa existir para ser consultado depois. Inverte a D1/D4 do #22.
- **D2 — Leituras normais continuam escondendo** — `list_messages`, `get_message_context`, `last_interaction` seguem com `[mensagem apagada]`, sem mídia, citação e menções; a busca por texto não casa com o conteúdo de mensagem apagada; o `last_message` de `list_chats`/`get_chat`/chat por contato mostra `[mensagem apagada]`; `download_media` e o mediaretry seguem recusando; citar (`quoted_message_id`) mensagem apagada é recusado; a varredura de transcrição (`transcribe.py`, `recover_audios.py`) ignora mensagem apagada. — porquê: com o conteúdo guardado, todo caminho que lê `content`/mídia vira vazamento em potencial, e o problema da #21 era justamente o agente ler e agir sobre o que foi retirado.
- **D3 — Consulta explícita por tool própria** — nova tool MCP `get_deleted_message(message_id, chat_jid, download=False)`, via endpoint novo da bridge, devolve o conteúdo original, `revoked_at`, `media_type`, e o texto anterior à edição quando houver; com `download=True` baixa a mídia da mensagem apagada (o único caminho que passa pela recusa). Mensagem que não foi apagada nem editada é recusada. — porquê: pedir o apagado tem de ser ato explícito; o agente não cai nele listando mensagens.
- **D4 — Edição guarda o texto original** — nova coluna `previous_content`, preenchida na primeira edição com o texto de antes (`COALESCE(previous_content, content)`), e exposta só pela tool do D3. — porquê: mesmo pedido ("ver o que a pessoa tinha escrito"); hoje a edição sobrescreve sem rastro.
- **D5 — Perda aceita** — mensagens apagadas entre o deploy do #22 (24/09 10:19) e o deste trabalho tiveram o conteúdo limpo e não são recuperadas. — porquê: não há de onde restaurar; os backups do dia são anteriores às exclusões ou já limpos.

## Avaliado e descartado
- Expor o original no `list_messages` com `revoked: true` (ou `include_deleted`): o agente veria o apagado por acaso ao listar, que é o defeito da #21.
- Apagar o arquivo de mídia em cache: contradiz o soft delete e tiraria o que o `download=True` precisa.

## Fora de escopo
- PR no upstream (rodrigopg): o destino é este fork, conforme o AGENTS.md.
- `quoted_content` de OUTRAS mensagens que citaram a apagada (cópia do texto feita na hora da citação): comportamento anterior, igual ao do app.

## Em aberto
