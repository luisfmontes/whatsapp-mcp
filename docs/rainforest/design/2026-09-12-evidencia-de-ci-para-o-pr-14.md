# Evidência de CI verde para o PR 14 no upstream

## Objetivo

Produzir a evidência de CI verde que o mantenedor pediu no PR #14 do
`rodrigopg/whatsapp-mcp` ("assim que verde, mesclam") e que o repositório dele
não tem como produzir, porque **não tem CI nenhuma** — e postar essa evidência
no PR, para que a condição declarada por ele possa ser cumprida.

## O que foi medido antes de decidir

Tudo aqui é saída de comando, não suposição:

| Medição | Comando | Resultado |
|---|---|---|
| PR #14 continua aberto e sem conflito | `gh pr view 14 --repo rodrigopg/whatsapp-mcp` | `OPEN`, `MERGEABLE`, `CLEAN`, último toque em 2026-08-31 |
| E sem check nenhum | idem, `statusCheckRollup` | `[]` |
| Porque o upstream não tem workflow | `git ls-tree -r upstream/main -- .github` | vazio |
| O rebase de 31/08 continua válido | `git merge-base --is-ancestor upstream/main origin/pr/...` | é ancestral |
| A branch do PR também não tem workflow | `git ls-tree -r origin/pr/fix-media-and-reliability -- .github` | vazio |
| Nem o verificador de dado pessoal | `git ls-tree -r ... -- scripts` | vazio |
| E o SQLite daquela árvore exige CGO | `git grep -E 'go-sqlite3|modernc' origin/pr/...` | só `mattn/go-sqlite3`, importado sem build tag |

As três últimas linhas são o que decide o desenho: **a CI do fork, como está,
não roda naquela árvore** — o Actions resolve o workflow a partir da própria
ref, o `personal-data` chamaria um script que não existe ali, e o job de
Windows compila com `CGO_ENABLED=0`, que naquela árvore não compila.

## Decisões fechadas

- **D1 — A entrega é evidência, não código.** O PR #14 já está mesclável e
  rebaseado; o que falta não é conserto, é o sinal verde que o mantenedor pôs
  como condição. Porquê: o próprio PR já satisfaz o pedido anterior dele
  ("rebasear/mesclar main") desde 31/08, e ficou 12 dias parado esperando uma
  condição que o repositório dele não consegue avaliar.

- **D2 — A CI que roda é a do fork.** Porquê: o upstream não tem `.github`
  nenhum (medido acima), então nenhum push, merge ou rerun no repositório dele
  produz check. Ou a evidência vem de fora, ou não vem.

- **D3 — Roda numa branch de validação separada, não na branch do PR.** A branch
  `ci/pr14-evidencia` nasce **exatamente** na cabeça de
  `pr/fix-media-and-reliability` e recebe **somente commit de configuração de
  CI** — o invariante é o conjunto de arquivos, não a contagem de commits:
  `git diff --name-only` contra a cabeça do PR tem de devolver uma linha só, o
  workflow. (Esta frase dizia "um único commit" até a revisão de 2026-09-12
  medir dois: o segundo é o corte do `pytest` da emenda 1, e a contagem nunca
  foi o que importava — o que importa é nenhum arquivo de produto mudar.) Porquê: o Actions só executa workflow que exista na ref, e mexer na
  branch do PR mudaria o conteúdo que está em revisão — um PR de conserto de
  mídia não deve virar um PR de infraestrutura no meio da revisão.

- **D4 — O workflow de validação é derivado do `build.yml` do fork, com os
  cortes que aquela árvore exigir, cada um medido, e a lista completa desses
  cortes mora aqui.** O diff inteiro dos dois YAML é outra coisa, e está
  delimitado mais abaixo. São cinco cortes, e o número não era previsível na mesa: o design dizia
  "dois", a execução achou o terceiro rodando, e a segunda rodada da revisão
  achou o quarto e o quinto lendo o arquivo publicado contra o original.

  | # | Corte | Medição que o justifica | Comentado no YAML? |
  |---|---|---|---|
  | 1 | Sai o job `personal-data` | `git ls-tree -r <ref> -- scripts` vazio: o `check-personal-data.py` que ele chama não existe naquela árvore | sim |
  | 2 | Windows com `CGO_ENABLED=1` | `git grep -E 'go-sqlite3\|modernc'` acha só o `mattn/go-sqlite3`, sem build tag — CGO desligado não compila ali | sim |
  | 3 | `pytest` por `uv run --with pytest` | `pyproject.toml` declara só `httpx`, `mcp[cli]` e `requests`, e a árvore traz `test_db_path.py` e `test_transcribe.py`: os testes existem e nada declarado os roda | sim |
  | 4 | Sai o `actions/setup-go` do job `mcp server` | o passo existe no `build.yml` por causa do `test_account_routing.py`, que sobe processo real do bridge — e `git ls-tree` mostra que esse arquivo **não existe** naquela árvore; `git grep` por invocação de `go` no python dela não casa nada | **não** |
  | 5 | `uv` por `pipx install uv` em vez de `astral-sh/setup-uv` | tira a dependência de action de terceiro: o workflow passa a depender só de `actions/*` | sim |

  Os cortes 4 e 5 não estavam nesta lista até 2026-09-12, e o 4 não tem
  comentário no YAML. Isso importa e fica escrito: **a frase anterior desta
  decisão afirmava que todo corte estava comentado no arquivo, e era falsa.**
  Uma decisão que afirma completude sem tê-la é pior que uma decisão que lista
  menos, porque quem lê para de conferir. A lista acima é a fonte; o YAML é
  cortesia para quem abrir só o arquivo. Porquê dos cortes em geral: um vermelho
  causado por arquivo ausente, por build tag que aquela árvore não tem ou por
  ferramenta que ela não usa não diz nada sobre o PR — e diria a coisa errada
  para quem lê.

  **O que a tabela cobre, exatamente:** os cortes feitos porque **aquela árvore
  não suporta** o que o `build.yml` faz. Ela não é o diff dos dois YAML. Há mais
  duas diferenças, e as duas são consequência de outras decisões já tomadas
  aqui, não cortes de compatibilidade:
  - o gatilho é `push: branches: ['ci/pr14-**']` em vez de `['**']` mais
    `pull_request` — decorre da D3: este workflow existe para rodar nas branches
    de validação e em mais nada;
  - a matriz do job `bridge` deixou de ser `include:` com par `os`/`cgo` e virou
    lista simples de `os` com `CGO_ENABLED: '1'` fixo, e o passo do detector de
    corrida perdeu o `if: matrix.cgo == '1'` — decorre mecanicamente do corte 2:
    com CGO ligado nas três plataformas, aquele `if` seria sempre verdadeiro.
  Quem quiser o diff inteiro, e não a lista de cortes, roda
  `diff <(git show origin/main:.github/workflows/build.yml) <(git show origin/ci/pr14-evidencia:.github/workflows/pr14-evidencia.yml)`
  — e é isso que a terceira rodada da revisão fez para achar estas duas.

- **D5 — Verde só conta depois de a bateria ter provado que sabe ficar
  vermelha.** Antes de postar, uma branch descartável com um erro deliberado no
  código do PR precisa deixar a mesma CI vermelha. Porquê: CI que passa em tudo
  não é evidência de nada, e essa exata armadilha já apareceu nesta máquina —
  catraca verde por erro de compilação é vermelho fraco, não prova.

- **D6 — O comentário no PR declara o SHA e o delta, sem arredondar.** Diz qual
  árvore foi validada, que o único arquivo a mais em relação ao PR é o workflow,
  quais jobs rodaram e em que plataformas, e o link do run. Porquê: evidência
  que descreve a si mesma de forma imprecisa é pior que nenhuma — o mantenedor
  vai decidir um merge com base nela.

- **D7 — Nenhum telefone, JID ou identificador pessoal em nada que sai.** Vale
  para o workflow, o comentário e o commit. Porquê: é invariante do repositório,
  e em rede de fork o que é empurrado continua alcançável por SHA mesmo depois
  de reescrito.

## Avaliado e descartado

- **Rodar a suíte aqui e colar a saída.** Sem toolchain C nesta máquina, a
  árvore do PR (que só tem o driver CGO) não compila localmente; e mesmo que
  compilasse, "verde na minha máquina" não é o que o mantenedor pediu. A CI do
  fork valida nas três plataformas e, por D5, também se deixa falsificar.
- **Abrir um PR da branch do PR #14 contra a `main` do fork.** A `main` do fork
  carregou trabalho não relacionado (citação e menção), então o check rodaria
  sobre a árvore do merge, não sobre o código do PR — mediria outra coisa e
  chamaria de evidência.
- **Adicionar o `build.yml` ao próprio PR #14.** Muda o que está em revisão, e
  o `personal-data` iria vermelho por falta do script — vermelho que não é do
  PR, dentro do PR.
- **Pedir ao mantenedor que ligue CI no repositório dele.** É a solução de
  fundo, mas não é desta entrega: transformaria uma evidência de meia hora numa
  negociação de infraestrutura no repositório de outra pessoa, com o PR #14
  continuando parado enquanto isso.

## Fora de escopo

- Oferecer CI ao upstream como contribuição própria (fica plantado como ideia,
  com gancho: quando o #14 for mesclado).
- Qualquer mudança no código do PR #14 — exceto se D5 revelar vermelho real, e
  aí o conserto entra como emenda a este plano, medido.
- A branch `feat/citacao-e-mencao` e o trabalho de menção, já entregues.

## Em aberto

- (vazio)
