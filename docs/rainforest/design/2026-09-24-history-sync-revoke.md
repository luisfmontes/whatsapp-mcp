# History sync aplica mensagem apagada ou editada (issue #23)

## Objetivo
No re-pareamento, o history sync entrega de novo o histórico inteiro; mensagem apagada ou editada com a ponte fora do ar tem de sair desse sync como apagada/editada no store, e não com o conteúdo antigo.

## Decisões fechadas
- **D1 — Stub REVOKE aplica a revogação** — entrada do history sync com `MessageStubType == WebMessageInfo_REVOKE` revoga `Key.ID` no chat da conversa pelo mesmo caminho do live (`applyProtocolMessage` com um `ProtocolMessage` REVOKE montado do `Key.ID`: limpa conteúdo e mídia, apaga o cache em disco). — porquê: é assim que o sync entrega a mensagem apagada (mesmo id, sem conteúdo), e hoje ela é descartada pelo `content == "" && mediaType == ""`, deixando o conteúdo antigo no banco.
- **D2 — Stub sobre mensagem ausente vira linha revogada** — se o id não existe no store (banco novo), grava uma linha com `content = ''`, `revoked_at` preenchido, remetente e timestamp do stub; se existe, não mexe além do D1. — porquê: a conversa mantém o lugar da mensagem, como o WhatsApp mostra "Esta mensagem foi apagada"; mesmo critério da D1 do #21.
- **D3 — Duas passadas por conversa** — primeiro grava todas as mensagens da conversa; os `ProtocolMessage` REVOKE/MESSAGE_EDIT encontrados ficam pendentes e são aplicados depois, com `applyProtocolMessage`. — porquê: o sync vem da mais nova para a mais antiga, então o protocolo chega antes do alvo; aplicado na hora, não acharia a linha. Marcador antecipado criaria linha fantasma para edição cujo alvo nunca chega.
- **D4 — Validação por sync sintético com os tipos reais** — testes montam `waHistorySync.Conversation`/`waWeb.WebMessageInfo` reais (stub REVOKE, `ProtocolMessage` REVOKE e EDIT fora de ordem) e passam pelo mesmo código do `handleHistorySync`; mutações por tarefa. O PR declara que não houve sync real. — porquê: o único gatilho real é re-parear (operação de produção) ou ligar o `requestHistorySync`, que hoje não tem chamador — fora do escopo.
- **D5 — Protocolo embrulhado é desembrulhado antes da detecção** — a detecção do `ProtocolMessage` no sync passa pelo `UnwrapRaw` do whatsmeow (`DeviceSentMessage`, `EphemeralMessage`, ViewOnce, `EditedMessage`). — porquê: achado da revisão; revogação/edição feita em outro aparelho do próprio usuário chega embrulhada em `DeviceSentMessage`, e ler o `ProtocolMessage` da camada de fora deixava passar.

## Avaliado e descartado
- Marcador ("tombstone") antecipado para protocolo cujo alvo ainda não chegou: cria linha fantasma quando o alvo não vem no sync, e complica o `StoreMessage`.

## Fora de escopo
- Ligar o `requestHistorySync` (sync sob demanda) a algum endpoint.
- Saber se o sync entrega a edição como conteúdo já atualizado ou como `MESSAGE_EDIT` separado: não verificado; D3 cobre as duas formas.

## Em aberto
