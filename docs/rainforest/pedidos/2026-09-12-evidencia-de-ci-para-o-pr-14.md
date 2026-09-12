# Evidência de CI para o PR #14 do upstream

Plano: docs/rainforest/planos/2026-09-12-evidencia-de-ci-para-o-pr-14.md
Design: docs/rainforest/design/2026-09-12-evidencia-de-ci-para-o-pr-14.md

## O que foi pedido

O pedido não é deste lado — é do mantenedor do `rodrigopg/whatsapp-mcp`, escrito
no próprio PR #14 em 2026-08-26:

> Aprovado — mesclei o #15 primeiro e agora ficou conflito de merge aqui
> (main.go/main_test.go). Pode rebasear/mesclar main pra resolver? Assim que
> verde, mesclam

A primeira metade foi atendida em 2026-08-31: o rebase foi feito, o conflito
resolvido, e o PR está `MERGEABLE`/`CLEAN` desde então. A segunda metade —
"assim que verde" — é o que manteve o PR parado por doze dias, e não por falta
de trabalho: **o repositório do mantenedor não tem CI**, então não existe nada
lá capaz de ficar verde.

## Por que o verde não podia vir de lá

| O que se quis saber | Comando | Resposta |
|---|---|---|
| O PR tem check? | `gh pr view 14 --repo rodrigopg/whatsapp-mcp --json statusCheckRollup` | `[]` |
| Existe workflow no upstream? | `git ls-tree -r upstream/main -- .github` | vazio |
| Existe na branch do PR? | `git ls-tree -r origin/pr/fix-media-and-reliability -- .github` | vazio |
| O rebase de 31/08 ainda vale? | `git merge-base --is-ancestor upstream/main origin/pr/fix-media-and-reliability` | exit 0 |

Sem workflow em nenhuma das duas refs, nem um `rerun` produziria check: o
GitHub Actions resolve o workflow **a partir da ref em que roda**.

## O que foi feito

Uma branch de validação no fork, `ci/pr14-evidencia`, que é a cabeça da branch
do PR **mais um único arquivo** — o workflow — e nada mais:

```
git diff --name-only origin/pr/fix-media-and-reliability origin/ci/pr14-evidencia
.github/workflows/pr14-evidencia.yml
```

A branch do PR não foi tocada: continua em `7721256`, o mesmo SHA que o
mantenedor leu. Push nela reabriria a revisão dele.

O workflow é o `build.yml` deste fork com dois cortes, os dois medidos naquela
árvore e comentados dentro do arquivo: sai o job `personal-data` (o
`scripts/check-personal-data.py` não existe naquela árvore) e o Windows compila
com `CGO_ENABLED=1` (aquela árvore só tem o driver `mattn/go-sqlite3`, sem build
tag — CGO desligado não compila ali). Vermelho por arquivo ausente ou por build
tag que a árvore não tem não diria nada sobre o PR.

## O verde só valeu depois do vermelho

Uma segunda branch, `ci/pr14-falsificacao`, é a de validação mais uma linha
invertida no código do próprio PR: a query string voltou a ser recortada em
`extractDirectPathFromURL` — exatamente o defeito que o PR conserta. A mutação
compila, então o vermelho tem de vir do teste, não do build.

O run de `ci/pr14-falsificacao`, sobre `8088b9b`: `conclusion: failure`, com

```
--- FAIL: TestExtractDirectPathFromURL (0.00s)
```

em `bridge (ubuntu-latest)`, `bridge (macos-latest)` e `bridge
(windows-latest)` — as três. O build compilou nas três, então o vermelho é do
teste e não do compilador, que é a distinção que a D5 exige: vermelho de build
é vermelho fraco e já enganou esta sessão uma vez. A branch foi apagada depois
de lido o log (`git ls-remote --heads origin ci/pr14-falsificacao`, vazio).

## Resultado

O run de `ci/pr14-evidencia`, sobre `e092361`, comparado job a job com o run
de `ci/pr14-base`, a branch de base — `3b68feb`, que é `upstream/main` sem
alteração nenhuma mais o mesmo workflow:

| job | árvore do PR | base |
|---|---|---|
| bridge (ubuntu-latest) | success | success |
| bridge (macos-latest) | success | success |
| bridge (windows-latest) | success | success |
| gofmt | success | success |
| mcp server (ubuntu-latest) | success | success |
| mcp server (macos-latest) | success | success |
| mcp server (windows-latest) | failure | failure |

Cada job do `bridge` roda `go build`, `go vet`, `go test` e `go test -race
-count=3`. As duas colunas coincidem job a job, e o único vermelho é o mesmo nos
dois lados, com as mesmas duas linhas `FAILED` de
`test_db_path.py::TestResolveMessagesDb` e o mesmo `2 failed, 16 passed`: é o
`endswith("whatsapp-bridge/store/messages.db")`, com barra de POSIX, num runner
Windows. O PR não toca esse arquivo.

O que isso permite afirmar sem arredondar: **o PR #14 não deixa vermelho nada
que já não estivesse vermelho na base**, e tudo que é dele passa nas três
plataformas, com detector de corrida.

Comentário publicado em 2026-09-12T12:08:18Z:
o comentario do PR #14 publicado em 2026-09-12T12:08:18Z

Ele cita os três runs, o SHA da branch de validação, o SHA intocado do PR, a
tabela acima, o fato de o delta ser um arquivo só, e a falsificação. Duas coisas
ficaram oferecidas ao mantenedor em vez de feitas por conta própria: o conserto
do teste que quebra no Windows, e CI no repositório dele — as duas plantadas
como ideia, com gancho na resposta dele ou no merge.

## Ponta solta

O upstream continua sem CI. Esta entrega resolve o **caso** (o PR #14 agora tem
evidência), não a **causa** (todo PR ali nasce com `statusCheckRollup` vazio).
A causa está plantada em `ideias.jsonl` como
`2026-09-12-ci-para-o-upstream-do-whatsapp-mcp`.
