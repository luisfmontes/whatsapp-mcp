# Multiconta no bridge do WhatsApp

## Objetivo

Rodar N contas de WhatsApp na mesma instalação — na prática, pessoal e trabalho —
sem que a conta pareada hoje perceba a mudança, e sem que dado de uma caixa possa
aparecer na outra por esquecimento de um filtro.

## Decisões fechadas

- **D1 — a conta pareada hoje vira a conta default, com migração no lugar** — porquê: são 113 mil mensagens e um pareamento vivo nesta máquina; repareamento é risco de histórico sem contrapartida. Chamada que não diz a conta continua caindo nela.
- **D2 — `account` é opcional na leitura e cai na default; escrita sem `account` com mais de uma conta configurada é erro** — porquê: "conta ativa" com estado implícito entre chamadas é exatamente o mecanismo pelo qual uma mensagem pessoal sai no grupo do trabalho. O erro é do servidor MCP, que é quem tem o mapa e quem fala com o modelo.
- **D3 — a conta se identifica por apelido (`pessoal`, `trabalho`), mapeado ao JID na configuração** — porquê: o JID já mudou de forma uma vez com o LID, e número não se lê num log. O JID só é conhecido depois do pareamento, então ele é gravado no mapa quando o QR fecha.
- **D4 — parear conta nova acontece por endpoint REST + tool MCP que devolve o QR** — porquê: a interface real é o Claude; mandar o usuário ao terminal justamente no passo mais chato desperdiça o ganho.
- **D5 — um processo de bridge por conta, cada um no seu diretório e na sua porta** — porquê: medido no código, e é a decisão-raiz. Ver "Avaliado e descartado".
- **D6 — leitura é sempre dentro de uma conta; não existe consulta que agregue conversas de contas diferentes** — porquê: misturar as duas caixas na mesma lista é o começo de responder no chat errado. Se um dia precisar, o servidor MCP chama as duas contas e junta — sem exigir que os dados morem no mesmo arquivo.
- **D7 — o mapa de contas vive num `accounts.json` no diretório de dados, lido pelo instalador e pelo servidor MCP** — porquê: o bridge hoje não lê arquivo de configuração nenhum e vive de 5 variáveis de ambiente; com N contas isso vira env duplicada em dois lugares que divergem em silêncio. O arquivo guarda apelido, diretório, porta, JID e qual é a default.
- **D8 — uma tarefa agendada por conta, sem supervisor** — porquê: o auto-start por tarefa já existe e funciona, e cada bridge já tem watchdog interno; um supervisor é mais um processo para morrer sem ninguém ver. Consequência forçada: os dois arquivos de nome fixo em `%TEMP%` — o PNG do QR e o `wa_transcribe.lock` — passam a levar o apelido no nome, senão duas contas brigam pelo mesmo arquivo.
- **D9 — um único servidor MCP; as 36 tools ganham `account` opcional, resolvido para a URL da conta** — porquê: N servidores MCP custariam zero linha de código, mas dobrariam o catálogo para 72 tools (~7 mil tokens em todo contexto, para sempre). Efeito colateral bem-vindo: hoje `whatsapp.py` ignora `WHATSAPP_BRIDGE_PORT` e bate sempre em `:8080`; com a resolução pelo mapa, esse bug deixa de existir por construção.
- **D10 — a instalação atual fica onde está, apontada pelo `accounts.json`; conta nova nasce em `~/.whatsapp-mcp/accounts/<apelido>/`** — porquê: mover 82 MB de banco mais 19 diretórios de mídia com um bridge pareado em cima é risco puro, e a D1 já diz que ela continua sendo a default.
- **D11 — conta nova é criada pelo instalador (`install.ps1 -AddAccount <apelido>`), que aloca diretório, porta livre, entrada no mapa e tarefa; a tool MCP só pareia** — porquê: um servidor MCP que registra tarefa agendada e escreve configuração de sistema é elevação de privilégio na prática. O instalador já sabe fazer as quatro coisas, inclusive achar porta fora das faixas reservadas do Windows.
- **D12 — conta registrada com o processo fora do ar devolve erro claro, nomeando a conta e a tarefa; não sobe o processo sob demanda** — porquê: subir por uma porta de trás dentro de uma chamada de leitura reintroduz o processo órfão que a D8 evitou, só que escondido.
- **D13 — `get_bridge_status` sem `account` devolve o agregado das N contas** — porquê: é a única tool cuja pergunta real é "está tudo de pé?"; não há dado de conversa para vazar de uma caixa para a outra, então a D6 não se aplica.
- **D14 — nesta entrega o `-AddAccount` sai só no `install.ps1`** — porquê: nada do bridge muda, então a CI segue verde nas três plataformas; paridade sem usuário é código sem prova de que funciona.
- **D15 — `transcription.env` continua único e compartilhado por todas as contas** — porquê: a chave de transcrição é a mesma, e cópia de segredo é lugar a mais de onde vazar.

## Avaliado e descartado

- **N contas dentro de um processo — o desenho original da ideia plantada.** Descartado por medição no código de `main`: não existe abstração de conta em lugar nenhum. O cliente é variável local de `main()`; as 39 rotas REST vão para o `http.DefaultServeMux` global; 5 estruturas de pacote guardam sessão (`qrState`, `watchdogState`, `lastEventAtNanos`, `sweepOnce`, `mediaRetryCache`); os 3 abridores de banco têm DSN literal (`file:store/messages.db`), e todo caminho de dado é relativo ao CWD — a ponto de os próprios testes isolarem com `os.Chdir(t.TempDir())`. Do lado Python, `transcribe.py` e `recover_audios.py` derivam o diretório de mídia de `os.path.dirname(DB_PATH)`. E a PK de `messages` é `(id, chat_jid)`, sem coluna de conta: duas contas no mesmo arquivo colidem em qualquer chat compartilhado. O caminho exigiria parametrizar tudo isso, migrar 113 mil linhas e mudar a chave primária; um processo por conta não muda nada disso.
- **N servidores MCP, um por conta.** Custa zero linha de código e tornaria a D2 impossível de violar, porque o nome da tool carregaria a conta. Descartado pelo custo permanente de contexto: 72 definições de tool em vez de 36.
- **Supervisor próprio para lançar e vigiar os filhos.** Descartado porque duplica um watchdog que já existe dentro de cada bridge e acrescenta um processo cuja morte ninguém observa.
- **Subir o processo da conta sob demanda, na primeira chamada.** Descartado: é o supervisor de novo, escondido dentro de uma chamada de leitura.
- **Mover a instalação atual para o layout novo.** Descartado pelo tamanho: 82 MB de bancos e 19 diretórios de mídia, com um bridge pareado em cima.

## Fora de escopo

- Multiconta no `install.sh` (macOS/Linux) — D14.
- Consulta que agregue conversas de contas diferentes — D6.
- Motor de transcrição diferente por conta — D15.
- Limite ou quota de contas.
- Autenticação por conta: `API_AUTH_TOKEN` e o bind em loopback seguem como hoje.

## Em aberto

- Nada.
