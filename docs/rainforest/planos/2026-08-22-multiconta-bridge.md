# Plano: Multiconta no bridge do WhatsApp

Design: docs/rainforest/design/2026-08-22-multiconta-bridge.md

## O que não pode quebrar

- A instalação pareada de hoje continua funcionando **sem `accounts.json` nenhum**: as 36 tools batem no bridge atual e o banco de 113 mil mensagens não é tocado.
- Nenhum dos 39 handlers REST, nenhum dos 3 abridores de banco e nenhuma tabela mudam. Se o diff tocar `NewMessageStore`, `openMessagesDB`, `openUnaccentMessagesDB` ou `openStoreDBReadOnly`, o plano saiu do trilho.
- Escrita nunca escolhe conta sozinha: com mais de uma conta configurada e sem `account`, é erro — nunca a default.
- `install.ps1` continua ASCII puro (PowerShell 5.1 quebra no parse com qualquer byte > 127) e o JSON que ele escreve continua sem BOM.
- A CI das três plataformas continua verde.

## Tarefas

### 1. Mapa de contas: formato do `accounts.json` e resolução no servidor MCP [tipo: implementar]
atende: D1, D3, D7, D10
arquivos: `whatsapp-mcp-server/accounts.py`, `whatsapp-mcp-server/test_accounts.py`
depende de: nenhuma
paralela: sim
pronto quando: com um `accounts.json` de duas contas (`pessoal` marcada default, apontando para o diretório e a porta da instalação atual; `trabalho` em `~/.whatsapp-mcp/accounts/trabalho`, outra porta), `resolve_account(None)` devolve a base URL da default, `resolve_account("trabalho")` a da outra e `resolve_account("inexistente")` levanta erro nomeando as contas conhecidas; e **na ausência do arquivo** — que é o estado da máquina hoje — `resolve_account(None)` devolve a URL derivada de `WHATSAPP_API_BASE_URL`/`WHATSAPP_BRIDGE_PORT` sem erro e `accounts_configured()` devolve `False` — provado por `python -m pytest whatsapp-mcp-server/test_accounts.py -q` devolvendo `passed` sem `failed`, e por `python -c "import accounts; print(accounts.resolve_account(None), accounts.resolve_account('trabalho'))"` imprimindo as duas URLs distintas.

### 2. `account` nas 36 tools e a guarda de escrita [tipo: implementar]
atende: D2, D6, D9
arquivos: `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/test_account_routing.py`
depende de: 1
paralela: nao
pronto quando: com **dois bridges de verdade no ar** — o binário `whatsapp-bridge` subido duas vezes, em diretórios e portas distintos, ainda sem parear — e um `accounts.json` com as duas contas, `get_bridge_status(account="trabalho")` responde com dados da porta do trabalho e `get_bridge_status(account="pessoal")` com os da outra porta; e `send_message(recipient=..., message=...)` **sem** `account` devolve erro citando os dois apelidos, sem chegar a fazer requisição HTTP — provado por `python -m pytest whatsapp-mcp-server/test_account_routing.py -q` devolvendo `passed` sem `failed`, com o teste subindo os dois processos reais e derrubando-os no fim.

### 3. `install.ps1 -AddAccount` cria a conta inteira [tipo: implementar]
atende: D5, D8, D10, D11, D14, D15
arquivos: `install.ps1`
depende de: 1
paralela: nao
pronto quando: `pwsh -File install.ps1 -AddAccount trabalho -InstallDir <dir descartável> -Service:$false` cria `<dir>/accounts/trabalho/store/`, escolhe porta que **não** cai em nenhuma faixa de `netsh interface ipv4 show excludedportrange protocol=tcp`, grava a entrada no `accounts.json` (sem BOM), gera o launcher da conta carregando o `transcription.env` **compartilhado** da raiz e imprime — sem registrar — o comando de tarefa `WhatsAppMCPBridge-trabalho`; o arquivo gerado é lido por `accounts.resolve_account("trabalho")` da tarefa 1 sem ajuste — provado por rodar o comando em diretório temporário e por `python -c "import accounts; print(accounts.resolve_account('trabalho'))"` imprimindo a URL da porta escolhida, mais `python -c "d=open('install.ps1','rb').read(); print(max(d))"` devolvendo valor <= 127.

### 4. Apelido no nome dos arquivos fixos em `%TEMP%` [tipo: implementar]
atende: D8
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim
pronto quando: com `WHATSAPP_ACCOUNT=trabalho` no ambiente do processo, o bridge usa `whatsapp-qr-trabalho.png` e `wa_transcribe-trabalho.lock` em `%TEMP%`; sem a variável, usa exatamente os nomes de hoje (`whatsapp-qr.png`, `wa_transcribe.lock`), porque a conta default não pode perceber a mudança — provado por `go test ./... -run TestAccountScopedTempPaths -v` devolvendo `PASS`, e por subir o binário com `WHATSAPP_ACCOUNT=trabalho` num diretório descartável e listar `%TEMP%` mostrando o PNG com sufixo.

### 5. Status agregado e o erro de conta fora do ar [tipo: implementar]
atende: D12, D13
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/test_account_routing.py`
depende de: 2
paralela: nao
pronto quando: com duas contas no `accounts.json` e **só a default com processo no ar**, `get_bridge_status()` sem `account` devolve as duas entradas — a default com o status real vindo de `/api/status`, a outra marcada como fora do ar citando o nome da tarefa `WhatsAppMCPBridge-trabalho` — e `list_chats(account="trabalho")` devolve uma mensagem de erro que contém o apelido e o nome da tarefa, sem stacktrace e sem tentar subir processo nenhum — provado por `python -m pytest whatsapp-mcp-server/test_account_routing.py -q -k "status_agregado or fora_do_ar"` devolvendo `passed` sem `failed`.

### 6. Tool de pareamento da conta registrada [tipo: implementar]
atende: D4
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/test_account_routing.py`
depende de: 2
paralela: nao
pronto quando: com o bridge da conta `trabalho` no ar e ainda **não pareado**, `pair_account("trabalho")` devolve os bytes que `/qr.png` daquela porta serve, começando com a assinatura PNG `\x89PNG`; e com a conta já pareada (`/qr.png` respondendo 404/409), devolve mensagem dizendo que a conta já está pareada, não erro cru — provado por `python -m pytest whatsapp-mcp-server/test_account_routing.py -q -k pair` devolvendo `passed` sem `failed`.

### 7. Regressão: a instalação de hoje não percebe nada [tipo: teste]
atende: D1
arquivos: `whatsapp-mcp-server/test_accounts.py`, `whatsapp-mcp-server/test_account_routing.py`
depende de: 2, 5
paralela: nao
pronto quando: sem `accounts.json` no disco e com o ambiente da instalação atual, uma chamada real a `get_bridge_status()` contra o bridge que já está rodando nesta máquina (`http://localhost:3005/api`) devolve o status dele, e nenhuma tool exige `account` — provado por `python -c "import whatsapp; print(whatsapp.get_bridge_status())"` imprimindo o JSON de status do bridge vivo, e pela suíte inteira `python -m pytest whatsapp-mcp-server -q` devolvendo `passed` sem `failed`.

### 8. README e contexto de deploy [tipo: docs]
atende: D7, D11, D14
arquivos: `README.md`, `.claude/context/deploy.md`
depende de: 3, 5
paralela: nao
pronto quando: o README descreve o `accounts.json` com um exemplo que **casa byte a byte** com o que o `-AddAccount` gera, diz que multiconta é só Windows nesta versão e mostra o fluxo criar → parear; `deploy.md` documenta uma tarefa agendada por conta e o `transcription.env` compartilhado — provado por `python -c "import json,re; ex=re.search(r'```json\n(.*?)```', open('README.md',encoding='utf-8').read(), re.S).group(1); json.loads(ex)"` sem exceção, e pelo exemplo do README ser aceito por `accounts.resolve_account`.
