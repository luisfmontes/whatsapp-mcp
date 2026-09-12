# Plano: Evidência de CI verde para o PR 14 no upstream

Design: docs/rainforest/design/2026-09-12-evidencia-de-ci-para-o-pr-14.md

## O que não pode quebrar

- **A branch do PR #14 não é tocada.** `origin/pr/fix-media-and-reliability`
  precisa continuar apontando para a mesma cabeça no fim do trabalho: o PR está
  `MERGEABLE`/`CLEAN` e em revisão, e um push nela reabre a revisão do
  mantenedor e invalida o que ele já leu.
- **A `main` do upstream não recebe push.** Nada aqui escreve no repositório de
  outra pessoa além de **um comentário** no PR.
- **Nenhum telefone, JID ou nome de pessoa em arquivo, commit ou comentário**
  (D7) — invariante do repositório, e em rede de fork o objeto empurrado segue
  alcançável por SHA mesmo depois de reescrito. Vale para este plano também:
  a mutação da tarefa 4 é **descrita**, nunca escrita com o literal.
- **Verde relatado só com run real lido.** Nenhuma afirmação de "passou" sem o
  `conclusion` vindo do `gh`, e nenhum verde vale antes de a tarefa 2 ter
  provado que aquela CI sabe ficar vermelha.
- **A branch descartável da falsificação não fica no fork.** Ela existe para
  produzir um vermelho e é apagada no mesmo trabalho.

## Tarefas

### 1. Branch de validação com a CI derivada [tipo: configurar]
atende: D2, D3, D4
arquivos: `.github/workflows/pr14-evidencia.yml` (só na branch `ci/pr14-evidencia`, que nasce na cabeça de `origin/pr/fix-media-and-reliability`)
depende de: nenhuma
paralela: sim
mutacao: n/a
  motivo: o arquivo é configuração de CI, não comportamento do produto — inverter uma linha dele mede o GitHub Actions, não esta entrega. A inversão que valida o que esta tarefa monta é a da tarefa 2, que mexe no **código sob teste** e exige a mesma CI vermelha; é lá que o vermelho tem valor de prova.
pronto quando: com a branch `ci/pr14-evidencia` empurrada no fork, o único arquivo diferente em relação à cabeça do PR #14 é o workflow, e a CI conclui verde sobre aquela árvore — provado por `git diff --name-only origin/pr/fix-media-and-reliability origin/ci/pr14-evidencia` devolvendo exatamente a linha `.github/workflows/pr14-evidencia.yml`, e por `gh run list --repo luisfmontes/whatsapp-mcp --branch ci/pr14-evidencia --json headSha,conclusion,databaseId` devolvendo `conclusion: success` com `headSha` igual a `git rev-parse origin/ci/pr14-evidencia`

### 2. Falsificação: a CI sabe reprovar aquela árvore [tipo: teste]
atende: D5
arquivos: `whatsapp-bridge/main.go` (só na branch descartável `ci/pr14-falsificacao`; nunca na branch do PR nem na de validação)
depende de: 1
paralela: nao
mutacao: n/a
  motivo: esta tarefa **é** o experimento de mutação — o alvo (`return "/" + parts[1]` de `extractDirectPathFromURL`, revertido para recortar a query com `strings.SplitN(parts[1], "?", 2)[0]`, que compila) e o caso que o alcança (`TestExtractDirectPathFromURL`) estão no `pronto quando:`. Declarar aqui um bloco `mutacao:` com `bateria:` seria fingir uma trava que não existe: `conferir-mutacao.cjs` roda a bateria **localmente**, e a árvore do PR só tem o driver SQLite com CGO, que não compila nesta máquina — o vermelho local viria de falha de build, que é vermelho fraco e já enganou esta mesma sessão uma vez.
pronto quando: com a branch `ci/pr14-falsificacao` = `ci/pr14-evidencia` mais a remoção da query string em `extractDirectPathFromURL`, o mesmo workflow conclui **vermelho** e o vermelho é do teste, não do build — provado por `gh run list --repo luisfmontes/whatsapp-mcp --branch ci/pr14-falsificacao --json conclusion` devolvendo `failure` e por `gh run view <id> --repo luisfmontes/whatsapp-mcp --log-failed` contendo a linha de falha de `TestExtractDirectPathFromURL`; e, no fim, `git ls-remote --heads origin ci/pr14-falsificacao` devolvendo vazio

### 3. Registro da evidência e comentário no PR [tipo: docs]
atende: D1, D6
arquivos: `docs/rainforest/pedidos/2026-09-12-evidencia-de-ci-para-o-pr-14.md`
depende de: 1, 2
paralela: nao
mutacao: n/a
  motivo: documento e comentário não têm ramo de execução a inverter; a falsificação deles é a coerência com o mundo — o SHA, o id de run e a lista de jobs que eles afirmam são relidos do `gh` no `pronto quando:`, e divergência em qualquer um reprova.
pronto quando: o comentário publicado no PR #14 afirma exatamente o que os comandos devolvem — provado por: o SHA citado no comentário é igual a `git rev-parse origin/ci/pr14-evidencia`; o id/URL de run citado é o mesmo de `gh run list --repo luisfmontes/whatsapp-mcp --branch ci/pr14-evidencia --json databaseId,url`; a lista de jobs citada casa nome a nome com `gh run view <id> --repo luisfmontes/whatsapp-mcp --json jobs --jq '.jobs[].name'`; e o comentário está no PR, verificável por `gh pr view 14 --repo rodrigopg/whatsapp-mcp --json comments` trazendo o corpo com o mesmo SHA e a mesma URL. O comentário declara também, em uma frase cada, que o único arquivo a mais em relação ao PR é o workflow, e que a CI foi falsificada (tarefa 2) — sem essas duas frases o critério falha, porque é a informação de que o mantenedor precisa para decidir o merge.

### 4. A trava de dado pessoal cobre o que sai [tipo: teste]
atende: D7
arquivos: `docs/rainforest/pedidos/2026-09-12-evidencia-de-ci-para-o-pr-14.md`, `docs/rainforest/design/2026-09-12-evidencia-de-ci-para-o-pr-14.md`, `docs/rainforest/planos/2026-09-12-evidencia-de-ci-para-o-pr-14.md`
depende de: 3
paralela: nao
mutacao:
  arquivo: `docs/rainforest/pedidos/2026-09-12-evidencia-de-ci-para-o-pr-14.md`
  de: o título de seção `## O que foi pedido`
  para: o mesmo título seguido de uma sequência com forma de telefone brasileiro — 13 dígitos abrindo em `55` —, **montada no argumento do comando em tempo de execução**, nunca escrita neste arquivo nem em qualquer arquivo rastreado
  bateria: `python scripts/check-personal-data.py`
  fixture: `check-personal-data.py, padrão ("telefone", r"\b55\d{10,11}\b") aplicado ao arquivo de pedido recém-rastreado — a saída tem de nomear o identificador E o caminho do arquivo`
pronto quando: com os arquivos desta entrega rastreados pelo git, o verificador do repositório não acha identificador novo — provado por `python scripts/check-personal-data.py` imprimindo `ok` com a mesma contagem de identificadores conhecidos de antes da entrega, e por `grep -rnE '\b55[0-9]{10,11}\b|@s\.whatsapp\.net|@g\.us|@lid' docs/rainforest/*/2026-09-12-evidencia-de-ci-para-o-pr-14.*` não casando nada

### 5. Medição de base: o mesmo workflow sobre a árvore antes do PR [tipo: teste]
atende: D1, D6
arquivos: `.github/workflows/pr14-evidencia.yml` (só na branch `ci/pr14-base`, que nasce em `upstream/main`)
depende de: 1
paralela: nao
mutacao: n/a
  motivo: a branch é a árvore de base **sem alteração nenhuma** mais o mesmo workflow já validado pela tarefa 2 — o experimento desta tarefa é justamente a ausência de mutação, e inverter algo aqui destruiria o que ela mede.
pronto quando: o único job vermelho do run da branch de validação é vermelho **também** na base, com a mesma falha, e nenhum job é vermelho só no PR — provado por `gh run view <run da evidência> --repo luisfmontes/whatsapp-mcp --json jobs --jq '[.jobs[]|{name,conclusion}]'` e o mesmo comando no run da base devolvendo listas cujas conclusões coincidem job a job, e por `gh api .../jobs/<id>/logs` das duas mostrando a **mesma** linha `FAILED`; o comentário da tarefa 3 cita as duas listas.

## Emendas

- **2026-09-12 — a tarefa 1 ganhou um terceiro corte, e ele apareceu rodando,
  não lendo.** O design previa dois deltas em relação ao `build.yml` do fork (o
  job `personal-data` e o `CGO_ENABLED` do Windows), os dois derivados de
  `git ls-tree` e `git grep`. O primeiro run da branch de validação reprovou
  `mcp server` nas **três** plataformas com `No module named pytest`: aquela
  árvore traz `test_db_path.py` e `test_transcribe.py` e o `pyproject.toml` dela
  declara `httpx`, `mcp[cli]` e `requests` — **nenhuma dependência declarada
  sabe rodar os testes que existem ali**. O workflow passou a fornecer o runner
  com `uv run --with pytest`, que é camada efêmera e não toca `pyproject.toml`
  nem `uv.lock`, que são arquivos do PR. O invariante "só configuração de CI
  muda" continua valendo: `git diff --name-only origin/pr/fix-media-and-reliability
  origin/ci/pr14-evidencia` continua devolvendo uma linha só.
  Isto também é informação para o mantenedor, e entra no comentário da tarefa 3:
  o repositório dele tem teste que nenhuma configuração declarada executa.
- **2026-09-12 — as tarefas 1 e 2 rodaram na janela principal, sem despacho.**
  As duas são orquestração de branch e de CI remota (escrever um YAML, empurrar,
  ler o run), não edição paralela de código, e cada uma ficou muito abaixo do
  limiar de 3.000 tokens da regra 10 — despachar sairia mais caro que fazer, e o
  agente em worktree isolado não tem como empurrar branch para o fork nem
  esperar run.
- **2026-09-12 — nasceu a tarefa 5, porque o primeiro verde não era verde.** Com
  o `pytest` fornecido, o `mcp server` passou em Linux e macOS e continuou
  vermelho no **Windows**, com duas falhas em
  `test_db_path.py::TestResolveMessagesDb`: o teste afirma
  `result.endswith("whatsapp-bridge/store/messages.db")`, com barra de POSIX, e
  no Windows o separador é outro. `git diff --name-only upstream/main
  origin/pr/fix-media-and-reliability -- whatsapp-mcp-server/test_db_path.py`
  devolve **vazio** — o PR #14 não toca esse arquivo, que entrou na main do
  upstream pelo `0307d9c`. Relatar "vermelho" sem essa distinção diria ao
  mantenedor que o PR quebra o Windows, o que é falso; e esconder o job diria
  que está tudo verde, o que também é falso. A saída é a **medição de base**:
  rodar o mesmo workflow em `upstream/main` e mostrar que o vermelho é o mesmo
  antes e depois do PR. É a tarefa 5.
- **2026-09-12 — a falsificação da tarefa 2 fechou nas três plataformas.** Run de `ci/pr14-falsificacao` (`8088b9b`): `--- FAIL: TestExtractDirectPathFromURL` em `bridge
  (ubuntu-latest)`, `bridge (macos-latest)` e `bridge (windows-latest)`, com o
  build compilando. Vermelho de teste, não de build — que é o que a D5 exige.
- **2026-09-12 — o `pronto quando:` da tarefa 1 foi corrigido, e a correção é
  uma admissão.** Ele exigia `conclusion: success` no run inteiro. Isso é
  inalcançável nesta árvore por um motivo que não tem nada a ver com o PR (o
  teste com barra de POSIX, tarefa 5), e um critério inalcançável só tem duas
  saídas: esconder o job vermelho, ou afrouxar o critério na surdina. Nenhuma
  serve. O critério passa a ser: **todo job verde, exceto job que esteja
  identicamente vermelho na base medida pela tarefa 5, com a mesma linha
  `FAILED`** — e o comentário da tarefa 3 cita as duas listas de jobs, lado a
  lado, para que quem lê confira em vez de acreditar. Medido no run de `ci/pr14-evidencia` (`e092361`): 6 de 7 jobs verdes (as quatro do `bridge` nas três plataformas,
  com `-race -count=3`, mais `gofmt` e `mcp server` em Linux e macOS), e o
  único vermelho é o `mcp server (windows-latest)`.
- **2026-09-12 — o `grep` da tarefa 4 casa a si mesmo, e o critério foi
  corrigido em vez de declarado cumprido.** O `pronto quando:` da tarefa 4 pedia
  que o `grep -rnE` não casasse nada nos arquivos da entrega — mas o padrão está
  escrito **dentro** deste plano, que é um dos arquivos da entrega, então ele
  casa a própria linha do critério e nunca poderia voltar vazio. O critério passa
  a ser: o `grep` não casa nada **exceto a linha que enuncia o próprio critério**,
  o que se confere com `| grep -v "^docs/rainforest/planos/...:.*pronto quando:"`
  — e assim voltou vazio. A prova de fundo continua sendo o
  `check-personal-data.py`, que é verde em 24 identificadores conhecidos, os
  mesmos de antes da entrega.
- **2026-09-12 — a primeira tentativa da catraca da tarefa 4 voltou VERDE, e o
  verde estava certo.** O valor com forma de telefone que eu escolhi para mutar
  já constava do `scripts/personal-data-baseline.txt` (em duas linhas, como
  `telefone:` e como `jid:`) — ou seja, a guarda o conhecia e o liberava de
  propósito, que é a razão de o baseline existir. Refeita com um valor ausente do
  baseline e do repositório, a catraca ficou **vermelha** nomeando o
  identificador **e** o caminho do arquivo, que era a `fixture` declarada.
  Fica a lição para a próxima catraca desta guarda: mutação tem de escolher valor
  **fora** do baseline, senão ela mede o baseline e não a guarda.
- **2026-09-12 (revisão) — dois achados, os dois de texto desatualizado, os dois
  consertados.** O revisor rodou contra o diff real e contra os artefatos
  remotos, e o núcleo da entrega bateu item a item (jobs idênticos nos dois
  runs, branch do PR intocada em `7721256`, delta de um arquivo, falsificação
  com `go build`/`go vet` passando antes do `--- FAIL`, comentário coerente com
  os runs, guarda de dado pessoal verde). Os dois achados:
  1. O `estado.json` tinha `tarefas_feitas: [1, 2]` e uma lista de `branches`
     sem a `ci/pr14-base` — valores congelados no marcador `parcial`, de quando
     só existiam as tarefas 1 e 2, contradizendo o `tarefas_ok: 5` do mesmo
     bloco. Quem fosse ao `verificar` lendo só aquele resumo concluiria que
     metade do trabalho não existiu. Remarcado com os cinco números e as três
     branches, e com o motivo da correção escrito no campo `nota`.
  2. A D3 do design dizia "**um único commit**", e a branch de validação tem
     dois (o segundo é o corte do `pytest` da emenda 1). A contagem de commits
     nunca foi o invariante — o invariante é `git diff --name-only` devolver só
     o workflow, e isso continua verdade. A D3 foi reescrita para dizer o que
     de fato se manteve, e a D4 pelo mesmo motivo: ela dizia "dois cortes" e a
     execução achou três.
  O revisor registrou uma lacuna honesta: não encontrou o `ideias.jsonl` para
  conferir as ideias plantadas. Ele mora em `<home>/.rainforest/`, fora
  de qualquer repositório — o que explica a busca falhar e não afeta o veredito.
