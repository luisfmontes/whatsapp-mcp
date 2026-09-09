# Pedido: responder citando mensagem (reply/quote) — inclusive de terceiro em grupo

**Data:** 2026-09-08 · **Origem:** sessão do Tech Challenge FIAP (grupo WhatsApp, conta `pessoal`)
**Estado:** pedido, sem design ainda. Entrada para o fluxo `brainstorm → plano → executar`.
**Destino sugerido no repo:** `docs/rainforest/pedidos/2026-09-08-responder-citando-mensagem.md`

## O que aconteceu

No grupo "Tech Challenge FIAP", um participante mandou uma mensagem curta logo depois de
um link, e a resposta certa era **citar a mensagem dele** e confirmar que aquilo já estava
aprovado. A ponte não tem essa capacidade. Saiu texto solto, abrindo com o teor da
mensagem dele parafraseado, o que funciona, mas perde o vínculo visual e obriga a repetir
o conteúdo dele na mão.

(Nomes e citações literais de terceiros foram retirados deste registro: o repositório é
público. O original, com eles, está fora do repo.)

A mesma limitação já está documentada para reagir e apagar: `react_to_message` e
`delete_message` com `from_me=False` são recusados em grupo.

## Evidência no código (branch `main`, commit `36defce`)

| Onde | O que |
|---|---|
| `whatsapp-bridge/main.go:595-599` | `SendMessageRequest` tem só `recipient`, `message`, `media_path`. Nenhum campo de citação. |
| `whatsapp-bridge/main.go:602` | `sendWhatsAppMessage` monta a mensagem sem `ContextInfo`. `grep ContextInfo\|QuotedMessage\|StanzaID main.go` = zero ocorrências. |
| `whatsapp-bridge/main.go:1235-1246` | `actionSenderJID`: com `fromMe=false` cai em `chatJID`, correto só em 1:1. Comentário do próprio código: "needs the participant JID plumbed in from the caller (not yet supported)". |
| `whatsapp-bridge/main.go:1271` | `handleReact` rejeita explicitamente terceiro em grupo. |
| `whatsapp-bridge/main.go:263-278` | Tabela `messages` **já guarda `sender`** por mensagem. |
| `whatsapp-bridge/main.go:1017-1018` | `sender` é gravado como `resolveToPN(...).User` (só o número); o JID completo `senderJID` é calculado na mesma linha e **não é persistido**. |
| `whatsapp-mcp-server/whatsapp.py:583` | `send_message(recipient, message, account)` — sem parâmetro de resposta. |

Conclusão: o dado que falta (quem mandou a mensagem citada) **já passa pela ponte** na
hora de gravar; só não é levado até a hora de enviar.

## Proposta (para o brainstorm validar, não decidir aqui)

1. **Ponte — `/api/send`:** aceitar `quoted_message_id` (opcional). A ponte busca em
   `messages` por `(id, chat_jid)` → `sender`, `is_from_me`, `content`; monta
   `waE2E.ExtendedTextMessage{Text, ContextInfo{StanzaID, Participant, QuotedMessage}}`.
   `Participant` = `sender@s.whatsapp.net` (ou o JID do próprio número quando
   `is_from_me`). Em 1:1 o `Participant` é o JID do chat.
2. **Reaproveitar para react/revoke:** o mesmo lookup por `(id, chat_jid)` resolve o
   `participant` e elimina a recusa de `main.go:1271` e a homóloga do revoke, em vez de
   pedir `from_me` ao chamador.
3. **MCP — `send_message`:** novo parâmetro `reply_to_message_id: Optional[str]`.
   Docstring do `react_to_message`/`delete_message` atualizada quando (2) entrar.
4. **Conta:** respeitar `account`, como o resto.

Ponto a decidir no brainstorm: persistir `sender_jid` completo na tabela (migração) ou
reconstruir `sender@s.whatsapp.net` a partir do número. LID vs PN: `resolveToPN` já
normaliza para número na gravação — confirmar que o `Participant` na citação aceita PN
quando a mensagem original chegou por LID.

## Critério de aceite (falsificável)

- `POST /api/send` com `quoted_message_id` de mensagem **de terceiro em grupo** → a
  mensagem chega no celular **com o balão de citação** mostrando autor e trecho.
- Mesma chamada em chat 1:1 citando mensagem do outro lado → idem.
- `quoted_message_id` inexistente → HTTP 4xx com erro claro, **nada enviado**.
- Sem `quoted_message_id` → comportamento idêntico ao atual (regressão zero nos testes
  existentes da ponte e do servidor MCP).
- `react_to_message(from_me=False)` em grupo passa a funcionar, se (2) entrar.

## Fora de escopo

Citar mídia (imagem/áudio) na resposta; menção (@) automática do autor citado.
