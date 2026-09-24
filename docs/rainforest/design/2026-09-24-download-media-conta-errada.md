# download_media sem account falha sem indicar a conta certa

## Objetivo
Quando `download_media` (ou `get_deleted_message` com `download=true`) é chamado numa conta
que não tem a mensagem, o erro deve dizer em qual conta ela está, em vez de devolver
`failed to find message: sql: no rows` com a dica enganosa da pasta de downloads.

## Decisões fechadas
- **D1 — Indicar, não rotear** — o erro nomeia a(s) conta(s) onde a mensagem existe e pede para repetir com `account="<alias>"`; o MCP não baixa sozinho pela outra conta. porquê: explícito, nunca traz mídia de conta errada em silêncio; custo é uma chamada a mais.
- **D2 — Localizar lendo o `messages.db` de cada conta no MCP** — só quando o bridge responder "failed to find message", o MCP consulta, somente leitura, `SELECT 1 FROM messages WHERE id=? AND chat_jid=?` no banco das demais contas (`known_aliases` + `account_dir`). Achou em mais de uma, lista todas. porquê: sem mudança no Go, funciona com o outro bridge fora do ar, e não custa nada no caminho feliz.
- **D3 — Dica honesta quando não existe em conta nenhuma** — "mensagem não encontrada em nenhuma conta; confira `message_id`/`chat_jid`". A dica da pasta de downloads fica só para falha real de download (mídia expirada, rede). porquê: a pasta de downloads não tem relação com mensagem inexistente e manda o agente procurar no lugar errado.
- **D4 — Escopo: `download_media` e `get_deleted_message(download=true)`** — as duas tools que baixam mídia. porquê: é onde o sintoma aparece; mesmo caminho de falha.

## Avaliado e descartado
- Rotear automático para a conta que tem a mensagem — descartado em D1 pelo risco de agir na conta errada sem o chamador perceber.
- Perguntar a cada bridge por HTTP — descartado em D2: exige endpoint novo no Go e falha quando o outro bridge está fora.

## Fora de escopo
- Demais tools que recebem id de mensagem (`react_to_message`, `edit_message`, `delete_message`, `get_message_context`) — ideia para depois, com o mesmo helper.
- Log no bridge para falhas anteriores ao "Attempting to download" — não muda o sintoma do agente.

## Em aberto
