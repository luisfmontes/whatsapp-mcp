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
  de: `chats.last_message_time,\n\t\t\tNULL as last_message` (o sítio do `listChats` — a coluna anterior com o prefixo `chats.` é o que o torna único)
  para: `chats.last_message_time,\n\t\t\tmessages.content as last_message`
  bateria: `bash <wrapper>` que exporta `CGO_ENABLED=0` e roda `go test ./... -count=1 -run 'ListChats|GetChat'`, com `--raiz whatsapp-bridge/`
  segundo sítio (rodar separado, o mesmo bloco não cobre os dois):
    de: `c.last_message_time,\n\t\t\tNULL as last_message` (o sítio do `getChat`, alias `c.`)
    para: `c.last_message_time,\n\t\t\tm.content as last_message`
  corrigido em 2026-08-23, na integração: a declaração original (`de: NULL as last_message`) casava **2 vezes** e a catraca recusava com exit 4 — "não dá para medir", não "reprovado". Duas formas do mesmo defeito de plano: `--de` ambíguo quando o conserto tem dois sítios, e bateria com `cd x && VAR=1 cmd`, que o `spawnSync(shell:true)` do script entrega ao `cmd.exe` no Windows e ele não entende. Rodar um sítio por vez é mais forte que o bloco único: prova que a bateria sabe falhar por **cada** endpoint, não por um deles.
pronto quando: com uma **cópia somente-leitura do store real** (`whatsapp-bridge/store/messages.db`, 2.476 chats e 121.050 mensagens), `listChats` e `getChat` chamados com `includeLastMessage=false` devolvem linha com `last_message`, `last_sender` e `last_is_from_me` nulos e **sem erro de SQL**, e com `includeLastMessage=true` devolvem os três preenchidos — provado por `cd whatsapp-bridge && CGO_ENABLED=0 go test ./... -run 'ListChats|GetChat' -v` mostrando `PASS` em ambos os pares de caso. Hoje o caminho `false` devolve `no such column: messages.content` em `listChats` e `no such column: m.content` em `getChat`.

### 2. O 5xx do bridge deixa de virar lista vazia [tipo: implementar]
atende: D2, D3
arquivos: `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/test_api_errors.py`
depende de: nenhuma
paralela: sim
mutacao:
  arquivo: `whatsapp-mcp-server/whatsapp.py`
  de: o bloco literal de 4 linhas que começa em `if response.status_code >= 500:` e termina na `)` do `raise ValueError(...)`, inclusive a indentação final — 183 bytes, uma ocorrência só
  para: `return None` com a mesma indentação final
  bateria: `bash <wrapper>` que roda `python -m pytest test_api_errors.py -q`, com `--raiz whatsapp-mcp-server/`
  corrigido em 2026-08-23, na integração: a declaração original dizia "o `raise ValueError(...)` do ramo de status 5xx", que é **intenção, não padrão** — a catraca exige literal e recusaria com exit 3. Duas armadilhas medidas ao derivar o literal: o arquivo é LF puro (0 CRLF em 1.622 linhas), e `sys.stdout.write` no Windows converte `\n` em `\r\n`, então capturar o trecho por `$(python -c ...)` produz um `--de` que não casa — derive com `sys.stdout.buffer.write`. Nota sobre o `--para`: substituir o bloco inteiro por `return None` também derruba o teste do 200, porque o `return` passa a vir antes da checagem de status 200. O veredito continua válido (o teste do 5xx falha, que é o que se mede), mas uma inversão mais cirúrgica trocaria só o `raise` pela linha equivalente.
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

### 5. Os artefatos do fluxo entram no commit [tipo: docs]
atende: D1, D2, D3, D4, D5
arquivos: `docs/rainforest/design/2026-08-23-erro-de-api-lido-como-lista-vazia.md`, `docs/rainforest/planos/2026-08-23-erro-de-api-lido-como-lista-vazia.md`, `docs/rainforest/estado/2026-08-23-erro-de-api-lido-como-lista-vazia.json`
depende de: nenhuma
paralela: nao
mutacao: n/a
  motivo: documento não tem comportamento a inverter. A falsificação dele é outra: as decisões `D<n>` que ele declara têm de casar com as tarefas que as realizam, e é o `conferir-fluxo.cjs cobertura` que mede isso — não uma bateria.
pronto quando: `git diff --stat f77d6e5...HEAD -- docs/rainforest/` lista os três arquivos, e `node scripts/conferir-fluxo.cjs cobertura --slug 2026-08-23-erro-de-api-lido-como-lista-vazia` sai `0`

**Tarefa acrescentada em 2026-08-23, durante o `revisar`.** Não é escopo novo: os três arquivos já estavam no commit desde o começo, porque é por eles que outra sessão retoma o trabalho. O que faltava era o plano DIZER isso — e sem a tarefa eles apareciam no diff sem casar com o `arquivos:` de ninguém, que é a definição de creep. A emenda existe para deixar rastro de que o escopo cresceu conscientemente, em vez de justificar em prosa que "era necessário".

## Paralelismo

Tarefas 1 e 2 são independentes — arquivos disjuntos (`whatsapp-bridge/*.go` contra
`whatsapp-mcp-server/*.py`), linguagens diferentes, baterias diferentes. Vão juntas.
As tarefas 3 e 4 são serialmente dependentes por construção: a 3 espera as duas
primeiras, e a 4 espera a CI da 3 (D5).
