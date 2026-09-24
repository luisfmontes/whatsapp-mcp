# Plano: download_media sem account falha sem indicar a conta certa

Design: docs/rainforest/design/2026-09-24-download-media-conta-errada.md

Entrada real dos critérios: `MSG=3EB03B32375462A658A721`, imagem recebida na conta `trabalho` em 2026-09-24. O chat dela fica fora do repo, que é público. Tire-o do banco local antes de rodar os critérios:
`CHAT=$(uv run python -c "import sqlite3;print(sqlite3.connect('../accounts/trabalho/store/messages.db').execute('select chat_jid from messages where id=?',('3EB03B32375462A658A721',)).fetchone()[0])")`

## O que não pode quebrar
- Caminho feliz de `download_media` e `get_deleted_message` inalterado: nenhuma leitura de banco extra quando o bridge acha a mensagem.
- Dica da mensagem apagada (Issue #21, `test_download_revoked.py`) continua valendo.
- Nenhuma escrita no `messages.db` de conta nenhuma: abertura somente leitura (`mode=ro`).
- Sem `accounts.json` (instalação de conta única), comportamento atual preservado.

## Tarefas

### 1. Helper que localiza a mensagem nas outras contas [tipo: implementar]
atende: D2
arquivos: `whatsapp-mcp-server/accounts.py`, `whatsapp-mcp-server/test_other_account.py`
depende de: nenhuma
paralela: sim
mutacao:
  arquivo: `whatsapp-mcp-server/accounts.py`
  de: `if alias == exclude:`
  para: `if alias == "__nunca__":`
  bateria: `cd whatsapp-mcp-server && uv run python -m pytest -q test_other_account.py`
  fixture: test_other_account.py::test_helper_ignora_a_conta_chamada
pronto quando: com o `accounts.json` real e a mensagem `$MSG`/`$CHAT`, `accounts.find_message_accounts(MSG, CHAT, exclude="pessoal")` devolve `['trabalho']` e com `exclude="trabalho"` devolve `[]` — provado por `uv run python -c "import accounts as a,sys;m,c=sys.argv[1:];print(a.find_message_accounts(m,c,exclude='pessoal'),a.find_message_accounts(m,c,exclude='trabalho'))" $MSG $CHAT` devolvendo `['trabalho'] []`

Assinatura: `find_message_accounts(message_id, chat_jid, exclude=None) -> List[str]`. `exclude=None` exclui a conta padrão. Para cada alias de `known_aliases()` diferente de `exclude`, abre `<dir>/store/messages.db` com `sqlite3.connect("file:...?mode=ro", uri=True)` e roda `SELECT 1 FROM messages WHERE id=? AND chat_jid=? LIMIT 1`. Banco ausente ou ilegível: pula a conta, sem erro. Sem contas configuradas: `[]`.

### 2. download_media indica a conta ou diz que não existe [tipo: implementar]
atende: D1, D3, D4
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/test_other_account.py`
depende de: 1
paralela: nao
mutacao:
  arquivo: `whatsapp-mcp-server/main.py`
  de: `if "failed to find message" in (status_message or ""):`
  para: `if "__nunca__" in (status_message or ""):`
  bateria: `cd whatsapp-mcp-server && uv run python -m pytest -q test_other_account.py`
  fixture: test_other_account.py::test_download_indica_a_outra_conta
pronto quando: com os dois bridges no ar e a mensagem `$MSG`/`$CHAT`, `main.download_media` sem `account` devolve `success=False` com `account="trabalho"` no `hint` e sem "downloads folder"; com id inexistente `XXXXNAOEXISTE` devolve hint com "not found in any account" e sem "downloads folder"; com `account="trabalho"` devolve `success=True` — provado por `uv run python -c "import main,sys;m,c=sys.argv[1:];print(main.download_media(m,c));print(main.download_media('XXXXNAOEXISTE',c));print(main.download_media(m,c,account='trabalho')['success'])" $MSG $CHAT` devolvendo as três saídas nessa ordem

Superfície humana: o agente decide o próximo passo lendo o `hint`. Ele precisa ver ali o alias exato para o parâmetro `account`. O teste falha se o alias sair do texto.

### 3. get_deleted_message indica a conta quando a mensagem não está na conta chamada [tipo: implementar]
atende: D1, D3, D4
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/test_other_account.py`
depende de: 2
paralela: nao
mutacao:
  arquivo: `whatsapp-mcp-server/main.py`
  de: `if "neither deleted nor edited" in (status_message or ""):`
  para: `if "__nunca__" in (status_message or ""):`
  bateria: `cd whatsapp-mcp-server && uv run python -m pytest -q test_other_account.py`
  fixture: test_other_account.py::test_deleted_indica_a_outra_conta
pronto quando: com os dois bridges no ar, `main.get_deleted_message(MSG, CHAT)` sem `account` devolve `success=False` com `account="trabalho"` no `hint`; com `account="trabalho"` devolve a mensagem original "neither deleted nor edited" sem `hint` de outra conta — provado por `uv run python -c "import main,sys;m,c=sys.argv[1:];print(main.get_deleted_message(m,c));print(main.get_deleted_message(m,c,account='trabalho'))" $MSG $CHAT` devolvendo as duas saídas nessa ordem

A 404 do bridge ("neither deleted nor edited") também sai quando a mensagem nem existe na conta chamada. A sugestão só entra quando o helper acha a mensagem em outra conta.
