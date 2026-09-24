# History sync desembrulha mensagem comum (issue #25)

## Objetivo
No re-pareamento, mensagem comum embrulhada — conversa com mensagens temporárias (`EphemeralMessage`), enviada de outro aparelho (`DeviceSentMessage`), visualização única — passa a ser gravada com texto e mídia, como no caminho live, em vez de descartada inteira.

## Decisões fechadas
- **D1 — Texto e mídia do sync saem da mensagem desembrulhada** — `storeHistoryConversation` passa `unwrapHistoryMessage(msg.Message.Message)` (o `UnwrapRaw` do whatsmeow) para `extractTextContent` e `extractMediaInfo`. — porquê: é a mesma forma que o live recebe; hoje a camada de fora não tem `Conversation` nem mídia e a entrada cai no corte de vazia.
- **D2 — Sem gravar sender_jid, citação e menções no sync** — fica como está; vira issue própria. — porquê: defeito anterior e diferente, que muda mais o que o sync grava; esta entrega fica só no desembrulho.
- **D3 — Visualização única igual ao live** — mídia de visualização única desembrulhada é gravada como o live já grava. — porquê: duas regras para a mesma mensagem conforme o caminho de chegada seria incoerente.
- **D4 — Validação por sync sintético** — testes com `waHistorySync`/`waWeb` reais (texto e imagem com legenda em `EphemeralMessage`, texto em `DeviceSentMessage`) e mutação que tira o desembrulho; o PR declara que não houve re-pareamento real. — porquê: o único gatilho real é re-parear.

## Avaliado e descartado
- Desembrulhar dentro de `extractTextContent`/`extractMediaInfo`: mudaria também o caminho live, que já recebe a mensagem desembrulhada.

## Fora de escopo
- `sender_jid`, citação, menções e enquete no history sync (D2).

## Em aberto
