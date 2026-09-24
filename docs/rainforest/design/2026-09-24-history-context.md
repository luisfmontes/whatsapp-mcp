# History sync grava autor, citação e menções (issue #28)

## Objetivo
No re-pareamento, mensagem gravada pelo history sync passa a ter `sender_jid`, `quoted_message_id`/`quoted_sender`/`quoted_content` e `mentions`, como o caminho live grava — e dá para citar, reagir e revogar mensagem de outro participante do grupo que só chegou pelo sync.

## Decisões fechadas
- **D1 — `sender_jid` pelo mesmo critério do live** — `historySyncSender` passa a devolver também o JID completo: participante da chave (grupo, mensagem alheia) por `resolveToPN`; o próprio chat por `resolveToPN` em conversa 1:1; o JID da conta sem aparelho (`ToNonAD`) em mensagem própria. Participante que não parseia, ou entrada sem participante fora de conversa 1:1 (grupo, `status@broadcast`, newsletter), fica vazio (autor desconhecido, D9), nunca o JID do chat — `isUnknownAuthor` só pega isso em `@g.us` (revisão). Mensagem própria fica sem aparelho de propósito, onde o live às vezes grava `:N` do aparelho que enviou: o sync não sabe de qual aparelho saiu, e `Participant` de citação não leva aparelho. — porquê: o critério do live; autor inventado é pior que autor desconhecido.
- **D2 — Citação e menções da mensagem desembrulhada** — `StoreMessageContext(extractContextInfo(inner))`, com o `inner` da #25; só quando há `ContextInfo`. — porquê: mesma forma do live; mensagem temporária carrega o contexto dentro do `EphemeralMessage`.
- **D3 — Sobrescreve linha existente** — as duas gravações rodam depois do `StoreMessage` bem-sucedido, também quando a linha já veio do live. — porquê: vêm do mesmo proto; o mapa LID→número só ganha entradas, então o sync não piora o que o live gravou; sem `ContextInfo` nada é tocado.
- **D4 — Validação por sync sintético** — testes com `waHistorySync`/`waWeb` reais e mutação; o PR declara que não houve re-pareamento real. — porquê: o único gatilho real é re-parear.

## Avaliado e descartado
- Preencher `sender_jid` só quando vazio: exigiria outro caminho de escrita sem ganho, pelo D3.

## Fora de escopo
- Linhas já gravadas sem `sender_jid` (~128 mil na conta pessoal): só um novo sync as preenche.
- `sender_jid` no stub REVOKE (tombstone): citar mensagem apagada já é recusado.

## Em aberto
