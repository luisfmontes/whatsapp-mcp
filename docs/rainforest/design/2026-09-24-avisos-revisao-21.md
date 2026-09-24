# Mídia apagada regravada pelo mediaretry e chat_jid sem normalizar em revoke/edit

## Objetivo
Fechar dois avisos da revisão do PR #22: a resposta tardia do telefone a um `/api/mediaretry` não pode regravar no disco a mídia de uma mensagem já apagada, e `/api/revoke`/`/api/edit` precisam atualizar o store pela mesma chave de chat que o caminho live grava.

## Decisões fechadas
- **D1 — mediaretry confere revogação antes de gravar** — a escrita do arquivo recuperado passa por um helper que consulta `IsMessageRevoked(id, chat)` e, se revogada, não grava e loga `MEDIA RETRY <id>: ERROR message was deleted by the sender`. — porquê: D4 do #22 promete a mídia fora do disco; a tag `ERROR` mantém o contrato de log que o `recover_audios.py` lê (SUCCESS|NOTONPHONE|ERROR), sem tag nova.
- **D2 — revoke/edit usam a chave de chat normalizada** — `handleRevoke`/`handleEdit` aplicam o protocolo no store com `resolveToPN(client, chatJID).String()`, não com `req.ChatJID` cru. — porquê: é a chave com que `handleMessage` grava a linha; um `chat_jid` em forma `@lid` batia em linha nenhuma e o store ficava com a versão antiga, sem erro.

## Avaliado e descartado
- Tag de log nova (`REVOKED`) no mediaretry: quebraria o contrato estável com `recover_audios.py`, que classifica por regex só as três tags existentes.

## Fora de escopo
- History sync aplicando REVOKE/EDIT: issue #23, precisa de design próprio.
- O `SendMessage` de revoke/edit continua indo para o `chatJID` que o chamador passou: só a chave do store local muda.

## Em aberto
