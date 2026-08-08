# Actions: Gaps whatsmeow #10, #12, #13 — info de usuário, convite e administração de grupo

> Identificador: `004-whatsmeow-gaps-10-12-13`
> Data: `2026-08-08`
> Contratos: `interfaces/contracts.md` (fonte única para os dois trilhos)

## Resumo

| Métrica | Valor |
|---------|-------|
| Total de ações | 9 |
| Trilhos paralelos | 2 (Go e Python, guiados pelos contratos) |
| Maior cadeia de dependência | 4 (T001→T002→T003→T007→T009) |

Sequenciamento: T001–T003 são logicamente independentes mas compartilham `main.go` — encadeadas dentro do mesmo trilho para evitar conflito de edição. O trilho Python (T005→T006) roda em paralelo com todo o trilho Go, em worktree separada, tocando arquivos disjuntos (`whatsapp.py`/`main.py` vs `main.go`/`main_test.go`).

## Fase 1, Preparação

n/a — sem scaffolding, sem migração (`data-delta.md`: nenhuma mudança de dados).

## Fase 2, Testes

| ID | Descrição | Dependências | Arquivo alvo | Confidência | Status |
|----|-----------|--------------|--------------|-------------|--------|
| T004 | Testes dos 7 handlers no molde `TestHandleReact` (main_test.go:152): 405, JSON malformado, campo obrigatório ausente, `group_jid` não-`@g.us` → 400, `group_settings` sem nenhum campo → 400, `group_photo` com `media_path`+`remove` juntos → 400 e com nenhum dos dois → 400, `user_info` com 21 JIDs → 400. Cobrir também as funções puras novas (merge de `user_info`) por tabela | T003 | `whatsapp-bridge/main_test.go` | 🟢 | `[ ]` |

## Fase 3, Núcleo

| ID | Descrição | Dependências | Arquivo alvo | Confidência | Status |
|----|-----------|--------------|--------------|-------------|--------|
| T001 | **#12** — structs + `handleGroupInviteLink`, `handleGroupInviteInfo`, `handleJoinGroup` + registro de `/api/group_invite_link`, `/api/group_invite_info`, `/api/join_group_with_link` (junto do bloco em main.go:~3224) | - | `whatsapp-bridge/main.go` | 🟢 | `[ ]` |
| T002 | **#13** — structs de ponteiro + `handleGroupSettings` (aplicação campo a campo, ordem fixa, resultado por campo) e `handleGroupPhoto` (leitura de arquivo / remove) + registro de `/api/group_settings`, `/api/group_photo` | T001 | `whatsapp-bridge/main.go` | 🟢 | `[ ]` |
| T003 | **#10** — structs + `maxUserInfoJIDs=20` + merge query↔resposta em função pura + `handleUserInfo` e `handleProfilePicture` (erros sentinela de foto como 200/`success:false`) + registro de `/api/user_info`, `/api/profile_picture` | T002 | `whatsapp-bridge/main.go` | 🟢 | `[ ]` |
| T005 | 7 helpers Python conforme contratos, molde `update_group_participants` (whatsapp.py:808). Atenção: `update_group_settings` omite do payload as chaves cujo argumento é `None` | - | `whatsapp-mcp-server/whatsapp.py` | 🟢 | `[ ]` |

## Fase 4, Integração

| ID | Descrição | Dependências | Arquivo alvo | Confidência | Status |
|----|-----------|--------------|--------------|-------------|--------|
| T006 | 7 tools `@mcp.tool` embrulhando T005, com as docstrings orientadas da tabela final de `interfaces/contracts.md` (RF-08) | T005 | `whatsapp-mcp-server/main.py` | 🟢 | `[ ]` |
| T007 | `cd whatsapp-bridge && go build ./... && go vet ./... && go test ./...` verde | T004 | `whatsapp-bridge/` | 🟢 | `[ ]` |
| T008 | Suíte Python verde (regressão) + import sanity dos novos helpers/tools | T006 | `whatsapp-mcp-server/` | 🟢 | `[ ]` |
| T009 | Reiniciar bridge com binário novo + smoke curl nos 7 endpoints com client conectado, incluindo um caminho de falha esperado (grupo sem admin → erro legível, não 500) | T007, T008 | `whatsapp-bridge/` | 🟢 | `[ ]` |

## Fase 5, Polimento

| ID | Descrição | Dependências | Arquivo alvo | Confidência | Status |
|----|-----------|--------------|--------------|-------------|--------|
| T010 | README (lista de tools MCP) e `whatsmeow-gap-analysis.md` (seção "Estado atual" + tabela: marcar #10, #12, #13 cobertos) | T006 | `README.md` | 🟢 | `[ ]` |
