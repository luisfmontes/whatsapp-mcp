# Requirements: Gap whatsmeow #11 — enquete (criar, votar e apurar)

> Identificador: `005-whatsmeow-gap-11-enquete`
> Data: `2026-08-08`
> Base: `60cea35` (main após o bump do whatsmeow para 2026-08-06)
> Confidência: 🟢 CONFIRMADO, 🟡 INFERIDO, 🔴 LACUNA / DÚVIDA

## 1. Resumo executivo

Último gap Tier B da tabela do `whatsmeow-gap-analysis.md`. **Não é do mesmo shape dos ciclos 002,
003 e 004** — todos eles eram request/response contra o WhatsApp, sem estado. Este exige que a
bridge seja um **ouvinte durável**: acumular votos que chegam como evento.

## 2. O fato que define a arquitetura 🟢

**Não existe API de "ler resultado da enquete."** Não há `GetPollVotes` no whatsmeow nem consulta
equivalente no protocolo. Verificado por varredura em `msgsecret.go` do módulo pinado: as únicas
funções de enquete são `BuildPollCreation`, `BuildPollVote`, `EncryptPollVote`, `DecryptPollVote` e
`HashPollOptions`.

Consequências, todas 🟢:

1. **Apuração é acumulada, não consultada.** Cada voto chega como um `events.Message` com
   `PollUpdateMessage`. Quem quiser o resultado precisa ter guardado cada um.
2. **Voto que chega com a bridge fora do ar se perde.** Não há como pedir de volta. 🔴 Não foi
   possível determinar se o history sync recarrega votos — tratar como "não recarrega" até prova
   em contrário.
3. **Voto vem como hash, não como texto.** `PollVoteMessage.SelectedOptions` é `[][]byte` de
   SHA-256 dos nomes das opções (`HashPollOptions`, msgsecret.go:309). Sem a lista original de
   opções, o voto é hash sem significado — **é isso, e só isso, que obriga a persistir dados novos**.
4. **A parte criptográfica não é nossa.** whatsmeow já persiste o `msgSecret` de toda mensagem
   sozinho (`storeMessageSecret`, message.go:945), então `DecryptPollVote` funciona sem nada nosso.

## 3. O ponto de entrada 🟢

`handleMessage` (main.go) retorna cedo:

```go
if content == "" && mediaType == "" {
    return
}
```

`PollCreationMessage` e `PollUpdateMessage` não têm texto nem mídia. **Hoje os dois são
silenciosamente descartados** — enquete não aparece no histórico e voto nenhum é registrado. É
exatamente aí que o tratamento entra, antes desse return.

## 4. Regras de negócio

1. **RN-01:** A enquete criada ou recebida é gravada em `polls` com a **lista de opções na ordem
   original**. Sem isso nenhum voto daquela enquete é resolvível — nem retroativamente. 🟢
2. **RN-02:** Enquete também vira linha em `messages`, com `content` = a pergunta. Sem isso ela
   some do histórico de conversa, e o `list_messages` mostraria um buraco onde houve enquete. 🟢
3. **RN-03:** Voto é **substituição, não incremento**. O WhatsApp manda a seleção completa a cada
   voto; revotar substitui. Chave `(poll_id, chat_jid, voter_jid)` com upsert. 🟢
4. **RN-04:** Upsert só sobrescreve se o voto novo for **mais recente ou igual** em timestamp.
   Evento fora de ordem (reconexão, history sync) não pode ressuscitar voto antigo por cima do
   atual. 🟢
5. **RN-05:** Voto de enquete cujas opções a bridge não tem — criada antes desta feature ou com a
   bridge fora do ar — é gravado com `resolved = 0` e sem nomes. **Não é descartado em silêncio**:
   a apuração reporta quantos votos não foram resolvidos. 🟢
6. **RN-06:** `get_poll_results` **sempre** declara que o resultado é o que a bridge viu, não o que
   o WhatsApp tem. Um voto recebido offline não está lá e não há como saber que faltou. A docstring
   carrega isso para o LLM nunca apresentar tally parcial como definitivo. 🟢
7. **RN-07:** `selectable_count` fora de `[1, len(options)]` é erro do chamador → 400. A lib
   silenciosamente troca por 0 (msgsecret.go:344), o que significaria "sem limite" — comportamento
   diferente do pedido, então não repassar. 🟢
8. **RN-08:** Enquete exige no mínimo 2 opções e no máximo 12 (limite do WhatsApp) 🟡, e nomes de
   opção **únicos** — opções duplicadas colidem no hash e tornam o voto ambíguo por construção. 🟢

## 5. Requisitos funcionais

| ID | Requisito | Confidência |
|----|-----------|-------------|
| RF-01 | `POST /api/create_poll` cria e envia a enquete, grava em `polls` e devolve o `message_id` | 🟢 |
| RF-02 | `POST /api/vote_poll` vota numa enquete existente pelo id da mensagem de criação | 🟢 |
| RF-03 | `POST /api/poll_results` devolve a apuração acumulada: por opção, contagem e votantes | 🟢 |
| RF-04 | `handleMessage` passa a tratar `PollCreationMessage` e `PollUpdateMessage` antes do return de mensagem vazia | 🟢 |
| RF-05 | 3 tools MCP correspondentes; a de apuração declara a ressalva de uptime na docstring | 🟢 |
| RF-06 | Testes: mapeamento hash→nome, upsert de revoto, guarda de timestamp fora de ordem, votos não resolvidos, validações de request | 🟢 |

## 6. Delta de dados

Primeiro ciclo com mudança de dados. Duas tabelas novas, **aditivas**, criadas no mesmo bloco
`CREATE TABLE IF NOT EXISTS` que já existe — sem framework de migração, sem alterar tabela
existente, sem backfill.

Ver `data-delta.md`.

## 7. Critério de pronto

1. `go build ./... && go vet ./... && go test ./...` verde; suíte Python verde.
2. Smoke real: enquete criada num grupo descartável, voto emitido, apuração conferida contra o que
   aparece no app.
3. Prova de que o voto sobrevive a **restart da bridge** — a apuração vem do banco, não da memória.
4. README e `whatsmeow-gap-analysis.md` marcando #11 coberto e a colheita encerrada.

## 8. Lacunas assumidas

- 🔴 **History sync recarrega votos?** Não determinado. O desenho não conta com isso.
- 🔴 **Limite de 12 opções** é o que o app impõe; não achei a constante na lib. Validar em 12 e, se
  o WhatsApp recusar antes, ajustar com o erro observado.
