# Plano: O 500 do include_last_message, e o erro de API que lê como lista vazia

Design: docs/rainforest/design/2026-08-23-erro-de-api-lido-como-lista-vazia.md

## O que não pode quebrar

- `scanAPIChatRow` continua escaneando 6 colunas, e os 4 chamadores
  (`listChats`, `getContactChats`, `getChat`, `getDirectChatByContact`) continuam
  passando por ele sem alteração.
- `include_last_message: true` — o caminho que 100% do uso real exercita hoje —
  continua devolvendo `last_message`, `last_sender` e `last_is_from_me`
  preenchidos. Isso é regressão de leitura, não detalhe.
- O contrato de wire não muda: `APIChat` mantém os campos como ponteiros sem
  `omitempty`, e o cliente continua recebendo as chaves, com `null` quando não há
  última mensagem.
- Leitura com bridge fora, timeout ou 4xx continua devolvendo lista vazia, não
  exceção — só 5xx passa a levantar.
- `go test ./...` e os 94 testes de `pytest` continuam verdes; teste existente que
  hoje afirma lista vazia em 500 é achado a investigar, não asserção a atualizar
  no automático.
- Nenhuma escrita no WhatsApp durante a validação: a conferência bate só em
  `/api/chats` e `/api/chat`.

## Tarefas

### 1. As duas queries param de projetar coluna que o JOIN não trouxe [tipo: implementar]
atende: D1, D3
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim
mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: o `NULL as last_message` da projeção do ramo sem JOIN de `listChats`
  para: `messages.content as last_message`
  bateria: `cd whatsapp-bridge && CGO_ENABLED=0 go test ./... -run 'ListChats|GetChat'`
pronto quando: com uma **cópia somente-leitura do store real** (`whatsapp-bridge/store/messages.db`, 2.476 chats e 121.050 mensagens), `listChats` e `getChat` chamados com `includeLastMessage=false` devolvem linha com `last_message`, `last_sender` e `last_is_from_me` nulos e **sem erro de SQL**, e com `includeLastMessage=true` devolvem os três preenchidos — provado por `cd whatsapp-bridge && CGO_ENABLED=0 go test ./... -run 'ListChats|GetChat' -v` mostrando `PASS` em ambos os pares de caso. Hoje o caminho `false` devolve `no such column: messages.content` em `listChats` e `no such column: m.content` em `getChat`.

### 2. O 5xx do bridge deixa de virar lista vazia [tipo: implementar]
atende: D2, D3
arquivos: `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/test_api_errors.py`
depende de: nenhuma
paralela: sim
mutacao:
  arquivo: `whatsapp-mcp-server/whatsapp.py`
  de: o `raise ValueError(...)` do ramo de status 5xx em `_api_post`
  para: `return None`
  bateria: `cd whatsapp-mcp-server && python -m pytest test_api_errors.py -q`
pronto quando: com um servidor HTTP local devolvendo **o corpo real que o bridge devolve hoje** (`{"error":"SQL logic error: no such column: messages.content (1)"}` com status 500), `_api_post` levanta `ValueError` cujo texto cita o path, o status e o corpo; e com 404, com timeout e com conexão recusada continua devolvendo `None` — provado por `cd whatsapp-mcp-server && python -m pytest test_api_errors.py -q` devolvendo `passed` sem `failed`, e por `python -m pytest -q` (suíte inteira) devolvendo no mínimo os 94 que passam hoje.

### 3. A entrega vira PR no fork, com CI verde [tipo: configurar]
atende: D4
arquivos: `.git` (branch e PR — nenhum arquivo de código)
depende de: 1, 2
paralela: nao
mutacao: n/a
  motivo: não há comportamento de código a inverter — esta tarefa move trabalho já validado para revisão. A falsificação dela é externa: se a CI reprovar, a tarefa não fecha.
pronto quando: existe PR aberto em `luisfmontes/whatsapp-mcp` contra `main` com os commits das tarefas 1 e 2, e `gh pr view <n> --repo luisfmontes/whatsapp-mcp --json state,statusCheckRollup` mostra `"state":"OPEN"` com todos os checks em `SUCCESS` — nenhum `FAILURE` nem `PENDING`.

### 4. O mesmo conserto sobe para o upstream [tipo: configurar]
atende: D5
arquivos: `.git` (branch e PR — nenhum arquivo de código)
depende de: 3
paralela: nao
mutacao: n/a
  motivo: é o mesmo diff da tarefa 1, já validado por mutação lá; aqui não há código novo, só destino. A falsificação é o diff do PR tocar as duas funções, não uma.
pronto quando: existe PR aberto em `rodrigopg/whatsapp-mcp` e `gh pr diff <n> --repo rodrigopg/whatsapp-mcp` mostra alteração **nas duas** funções (`listChats` e `getChat`), com o repro de `curl` no corpo do PR — confirmado por `gh pr view <n> --repo rodrigopg/whatsapp-mcp --json state` devolvendo `"state":"OPEN"`. **O texto do PR passa pelo Luís antes de submeter**: é repositório de outra pessoa.

## Paralelismo

Tarefas 1 e 2 são independentes — arquivos disjuntos (`whatsapp-bridge/*.go` contra
`whatsapp-mcp-server/*.py`), linguagens diferentes, baterias diferentes. Vão juntas.
As tarefas 3 e 4 são serialmente dependentes por construção: a 3 espera as duas
primeiras, e a 4 espera a CI da 3 (D5).
