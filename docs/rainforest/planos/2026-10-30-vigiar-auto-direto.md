# Plano: vigiar --auto envia direto fora das exceções

Design: docs/rainforest/design/2026-10-30-vigiar-auto-direto.md

## O que não pode quebrar
- A seção 5 (modo padrão: rascunho e espera) fica byte a byte igual.
- As três exceções de "Always stop and ask", o rodapé de resposta automática e a regra do "me" ficam com o mesmo conteúdo.
- As seções 1 a 4 e 7 não mudam.

## Tarefas

### 1. A seção 6 nomeia o `--auto` como pedido explícito, cobre a mensagem do arme e fecha a lista de exceções [tipo: docs]
atende: D1, D2, D3, D4
arquivos: `commands/vigiar.md`
depende de: nenhuma
mutacao: n/a
  motivo: só instrução em texto; nenhum código lê a seção 6
pronto quando: com `commands/vigiar.md` no HEAD da tarefa, a seção 6 contém as quatro substrings `rule 3`, `explicit`, `same request` e `complete list` (D1, D2 e D3) e a seção 5 é igual à de `origin/main` — provado por um `node -e` que extrai as seções 5 e 6 (de `## 5.` até `## 6.`, e de `## 6.` até `## 7.`) do arquivo e de `git show origin/main:commands/vigiar.md`, imprime `4/4` para as substrings na seção 6 nova e `secao 5 igual` para a 5, e sai 0; e pelo mesmo `node -e` apontado para o arquivo de `origin/main` imprimindo menos de `4/4`.
