# Regression watch — ciclo 005 (enquete)

Comportamento **observado no smoke real**: bridge conectada, conta do dono (single-device), grupo
descartável `smoke 005`, criado e abandonado ao fim.

## 1. Conta single-device não recebe echo do próprio evento — duas vezes ⚠️

Este é o tema do ciclo inteiro, e ele apareceu em **dois** lugares distintos.

| Handler | Sintoma | Achado por |
|---------|---------|-----------|
| `handleCreatePoll` | Enquete criada não virava linha em `messages`; `list_messages` mostrava buraco onde estava a enquete (RN-02 valia só para enquete de terceiro) | revisão |
| `handleVotePoll` | Voto enviado com sucesso (HTTP 200) e apuração **zerada**: nenhuma linha em `poll_votes` | **smoke** |

Mesma causa: em conta single-device o echo multi-device nunca dispara, então `handleMessage` nunca
vê o que a própria bridge mandou. O projeto já sabia disso — `sendWhatsAppMessage` documenta e trata
desde o PR #2 — mas os dois handlers novos não replicaram o padrão.

**Se regredir:** qualquer handler novo que envie mensagem e dependa de `handleMessage` para
persistir vai falhar silenciosamente nesta conta. O sintoma é sempre o mesmo: a ação funciona no
WhatsApp e não aparece no banco local. Teste unitário não pega; só smoke em conta real.

## 2. Confirmado em execução

| Comportamento | Resultado observado |
|---------------|---------------------|
| Enquete vira linha no histórico (RN-02) | `content = "smoke 005?"` presente em `/api/messages` |
| Opções trimadas são o que se grava | `polls.options = ["alpha","beta","gama"]` — o pedido tinha `" beta "` |
| Revoto substitui (RN-03) | beta 1 → revoto em gama → beta 0, gama 1, `total_voters` continua 1 |
| Voto retirado zera o votante | seleção vazia → todas as opções 0, `total_voters` 0 |
| **Apuração sobrevive ao restart** | alpha 1 antes, alpha 1 depois de matar e subir a bridge |
| Opções sem voto aparecem | beta e gama presentes com count 0 |
| Validações | 1 opção, duplicada, só-difere-por-espaço, `selectable_count` 0 e 99 → todas 400 |
| Erros | voto em opção inexistente → 400; apuração de poll inexistente → 404 |

## 3. Não coberto pelo smoke

- **Voto de terceiro.** Todos os votos do smoke foram do próprio dono, ou seja, passaram pelo
  caminho novo de persistência direta em `handleVotePoll` — **não** pelo `handleMessage`. O
  tratamento de `PollUpdateMessage` recebido de outra pessoa está coberto só por teste unitário
  (`TestResolvePollVote`, `TestPollVotesUpsertBehavior`). Exercitar de verdade exige uma segunda
  pessoa votando numa enquete do dono.
- **Voto órfão (`resolved = 0`) em execução real.** Mesma limitação: exige `PollUpdateMessage` de
  enquete desconhecida chegando de fora. Coberto por teste unitário apenas.
- **Voto em grupo LID-addressed.** A revisão foi à fonte do whatsmeow e mostrou que
  `GetMessageSecret` faz fallback LID↔PN, então não deve quebrar — mas isso é leitura de código,
  não observação.
- **`PollCreationMessageV2/V3`.** Não confirmado se existem no protobuf pinado nem se são tratados.

## 4. A limitação que não tem conserto

Não existe API para consultar votos de uma enquete. A apuração é **só** o que esta bridge viu
chegar; voto recebido com ela fora do ar não está lá e não há como detectar que faltou. Está na
docstring do `get_poll_results` e é o motivo de RN-06 existir. Não é bug, é o teto da feature.
