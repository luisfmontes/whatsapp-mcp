# Plano: Responder citando mensagem, e mencionar pessoas

Design: docs/rainforest/design/2026-09-08-responder-citando-e-mencionar.md

## O que não pode quebrar

- A ponte tem que continuar subindo com o banco que já existe (128.377 mensagens
  na conta `pessoal`, 6.862 na `trabalho`). Coluna nova sem migração derruba a
  primeira consulta.
- `StoreMessage` preserva `content` existente via `COALESCE(NULLIF(...))` — é o
  que impede um re-sync de apagar transcrição de áudio. Nenhuma tarefa toca essa
  semântica.
- Envio sem citação e sem menção continua saindo como `Conversation`, byte a byte
  como hoje. A troca para `ExtendedTextMessage` só acontece quando há
  `ContextInfo`.
- Nenhum número de telefone e nenhum JID entra em teste, doc, mensagem de erro ou
  resposta de API. `python scripts/check-personal-data.py` sai 0 em toda tarefa.
- Nenhuma tarefa escreve nos stores reais (`whatsapp-bridge/store/`,
  `accounts/*/store/`). Leitura é sempre `file:...?mode=ro`; quando precisar
  escrever, é numa cópia no diretório de scratchpad da sessão, apagada ao fim.

## Tarefas

### 1. Colunas novas e migração idempotente do store [tipo: implementar]
atende: D8, D15
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: nenhuma
paralela: sim

Acrescenta à tabela `messages` as colunas `sender_jid`, `quoted_message_id`,
`quoted_sender`, `quoted_content` e `mentions` (todas `TEXT`, nullable), nas duas
frentes: no `CREATE TABLE IF NOT EXISTS` de `NewMessageStore` para banco novo, e
numa função `ensureMessagesSchema(db *sql.DB) error` que lê `PRAGMA table_info(messages)`
e emite `ALTER TABLE messages ADD COLUMN` só para as que faltam. `handleMessage`
passa a persistir o `senderJID` completo que já calcula em `main.go:1017-1018`.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: o corpo de `ensureMessagesSchema` que emite os `ALTER TABLE ... ADD COLUMN`
  para: `return nil` imediato, sem emitir nenhum ALTER
  bateria: `cd whatsapp-bridge && go test -run TestEnsureMessagesSchema ./...`
  fixture: `TestEnsureMessagesSchema/banco_legado_ganha_as_cinco_colunas` — cria SQLite com a tabela `messages` na forma antiga (13 colunas), roda a função, exige as 5 novas em `PRAGMA table_info`

pronto quando: com uma cópia do store real da conta `pessoal` no scratchpad da
sessão (`cp whatsapp-bridge/store/messages.db "$SCRATCH/m.db"`, banco de 128.377
linhas gravado antes desta mudança, portanto sem nenhuma das 5 colunas), abrir a
store contra ela acrescenta as 5 colunas e não perde nenhuma linha — provado por
`python -c "import sqlite3;c=sqlite3.connect(r'$SCRATCH/m.db');print(sorted(r[1] for r in c.execute('PRAGMA table_info(messages)')));print(c.execute('SELECT COUNT(*) FROM messages').fetchone()[0])"`
rodado **antes e depois** da migração, devolvendo uma lista que passa a conter
`mentions`, `quoted_content`, `quoted_message_id`, `quoted_sender` e `sender_jid`,
e **a mesma contagem** nas duas leituras. A contagem é comparada consigo mesma, e
nunca com um literal: a ponte está no ar e recebe mensagem o tempo todo, então
qualquer número fixo escrito aqui já nasce defasado — foi o que aconteceu com o
`128377` que esta linha trazia na primeira versão. A cópia é apagada ao fim da
tarefa (`rm "$SCRATCH/m.db"`), e o store real não é aberto para escrita em momento
nenhum.

### 2. Grupo de teste entre as contas `pessoal` e `trabalho` [tipo: configurar]
atende: D16
arquivos: `docs/rainforest/estado/2026-09-08-responder-citando-e-mencionar.json`
depende de: nenhuma
paralela: sim

Cria, pela API da ponte, um grupo contendo as duas contas do Luís, para que exista
um terceiro real em grupo sem envolver pessoas de fora. O JID do grupo fica
registrado no estado do fluxo (campo livre), **nunca** em arquivo versionado do
código nem em teste.

mutacao: n/a
  motivo: tarefa de configuração de ambiente — não introduz ramo de código a
  inverter. A falsificação dela é a existência do grupo com os dois participantes,
  medida abaixo.

pronto quando: com as duas pontes no ar (3005 e 3006), o grupo existe e as duas
contas o enxergam com 2 participantes — provado por `curl -s -X POST
localhost:3005/api/group_info -d '{"jid":"<jid do grupo>"}'` e a mesma chamada em
`localhost:3006` devolvendo, as duas, `"success": true` e uma lista de
participantes de tamanho 2. A saída é conferida sem colar JID nem número no
relatório: reporta-se só `success` e o tamanho da lista.

### 3. Extração do `ContextInfo` recebido e gravação [tipo: implementar]
atende: D1, D13
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

`extractContextInfo(msg *waProto.Message) *waProto.ContextInfo` cobrindo os tipos
que carregam contexto (`ExtendedTextMessage`, `ImageMessage`, `VideoMessage`,
`AudioMessage`, `DocumentMessage`, `StickerMessage`, `ContactMessage`,
`LocationMessage`, `PollCreationMessage`), com nil-safety. `handleMessage` passa a
gravar `quoted_message_id`, `quoted_sender`, `quoted_content` (via
`extractTextContent` sobre o `QuotedMessage`) e `mentions` (array JSON de
`GetMentionedJID()`), por caminho próprio — `StoreMessage` não muda de assinatura.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: o `case`/ramo de `extractContextInfo` que devolve o `ContextInfo` do `ExtendedTextMessage`
  para: `return nil` nesse ramo
  bateria: `cd whatsapp-bridge && go test -run TestExtractContextInfo ./...`
  fixture: `TestExtractContextInfo/extended_text_com_citacao` — `*waProto.Message` com `ExtendedTextMessage.ContextInfo` preenchido, exige `StanzaID` e `Participant` de volta

pronto quando: com uma resposta **de verdade** enviada da conta `trabalho` citando
uma mensagem da conta `pessoal` no grupo da tarefa 2, a linha gravada pela ponte
`pessoal` traz o id citado — provado por
`python -c "import sqlite3;c=sqlite3.connect('file:whatsapp-bridge/store/messages.db?mode=ro',uri=True);print(c.execute('SELECT quoted_message_id IS NOT NULL, quoted_sender IS NOT NULL, length(quoted_content)>0 FROM messages WHERE id=?',(ID,)).fetchone())"`
devolvendo `(1, 1, 1)`, onde `ID` é o id da mensagem recebida, lido do log da
ponte. Nenhum JID ou número é impresso: só os três booleanos.

### 4. Envio citando mensagem [tipo: implementar]
atende: D9, D10, D11, D12, D14
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

`SendMessageRequest` ganha `quoted_message_id`. A ponte busca a mensagem citada por
`(id, chat_jid)` e monta
`ContextInfo{StanzaID, Participant, QuotedMessage: &waProto.Message{Conversation: <conteúdo guardado>}}`.
Texto com contexto sai como `ExtendedTextMessage`; mídia recebe o `ContextInfo` no
struct do próprio tipo. Três recusas, todas HTTP 4xx **sem enviar nada**: id
inexistente no store (D12), autor desconhecido — `sender_jid` nulo e `sender`
igual ao usuário do `chat_jid` (D9), e chat de destino diferente do chat da
mensagem citada.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: o `return`/escrita de 4xx do ramo de "mensagem citada não encontrada no store"
  para: seguir o fluxo e enviar sem `ContextInfo`
  bateria: `cd whatsapp-bridge && go test -run TestSendQuotedRecusa ./...`
  fixture: `TestSendQuotedRecusa/id_inexistente_nao_envia` — exige status 4xx e que o cliente falso não tenha recebido nenhum `SendMessage`

pronto quando: com a ponte `pessoal` no ar e uma mensagem real da conta `trabalho`
no grupo da tarefa 2, `POST localhost:3005/api/send` com `quoted_message_id` dessa
mensagem devolve `"success": true`, e um `POST` com um id que não existe devolve
HTTP 4xx **e** nenhuma linha nova em `messages` — provado por comparar
`SELECT COUNT(*) FROM messages WHERE chat_jid = <grupo>` antes e depois da chamada
recusada, devolvendo o mesmo número.

### 5. Menção por nome, com desambiguação sem número [tipo: implementar]
atende: D3, D4, D5, D6, D7
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

`SendMessageRequest` ganha `mentions: []string` — **nomes**, nunca número. A ponte
resolve cada nome contra os participantes do chat de destino (tabela `senders`
cruzada com a lista de participantes do grupo), casando por `push_name`,
`full_name`, `first_name` e `business_name`. Um casamento: substitui `@Nome` no
texto por `@<número>` e acrescenta o JID em `MentionedJID`. Zero casamentos: 4xx
sem enviar. Dois ou mais: 4xx sem enviar, com a lista de candidatos
`{ref, nome, origem_do_nome}` — `ref` é opaco (`c1`, `c2`, …) e **a resposta não
contém número nem JID**. O reenvio aceita `mentions: ["ref:c2"]`. Só os nomes
listados são substituídos; `@` solto no texto passa intacto.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: o ramo que recusa com 4xx quando a resolução do nome devolve 2 ou mais candidatos
  para: seguir com o primeiro candidato da lista
  bateria: `cd whatsapp-bridge && go test -run TestResolveMentionAmbigua ./...`
  fixture: `TestResolveMentionAmbigua/dois_candidatos_recusa_e_devolve_refs` — dois participantes com o mesmo primeiro nome, exige 4xx, dois `ref` distintos e nenhum dígito de telefone no corpo

pronto quando: (a) com um prefixo de nome que casa com **2 ou mais** remetentes
reais do store — descoberto por
`python -c "import sqlite3;c=sqlite3.connect('file:whatsapp-bridge/store/messages.db?mode=ro',uri=True);print([n for n,q in c.execute('SELECT substr(coalesce(full_name,push_name),1,instr(coalesce(full_name,push_name)||\" \",\" \")-1) p, COUNT(*) q FROM senders GROUP BY p HAVING q>1')][:1])`
— a chamada devolve 4xx, dois ou mais `ref`, e o corpo da resposta **não casa**
`grep -E '[0-9]{8,}'`; (b) com o nome da conta `trabalho` no grupo da tarefa 2, a
mensagem é enviada e a ponte `trabalho` grava a menção — provado por
`SELECT mentions IS NOT NULL AND mentions <> '[]' FROM messages WHERE id=?`
contra `accounts/trabalho/store/messages.db` em modo `ro`, devolvendo `1`.

### 6. Reagir e apagar mensagem de terceiro em grupo [tipo: implementar]
atende: D2
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`
depende de: 1
paralela: nao

`handleReact` e `handleRevoke` deixam de recusar `from_me=false` em grupo
(`main.go:1268-1273` e `main.go:1351-1356`): o participante autor vem do
`sender_jid` gravado, e `actionSenderJID` passa a recebê-lo em vez de cair no
`chatJID`. Mensagem cujo autor é desconhecido continua recusada, com o mesmo erro
da D9.

mutacao:
  arquivo: `whatsapp-bridge/main.go`
  de: a passagem do `sender_jid` resolvido para `actionSenderJID` no caminho de `from_me=false` em grupo
  para: voltar a passar `chatJID` como remetente
  bateria: `cd whatsapp-bridge && go test -run TestActionSenderJIDGrupo ./...`
  fixture: `TestActionSenderJIDGrupo/terceiro_em_grupo_usa_participante` — exige que o JID passado a `BuildReaction` seja o do autor, não o do grupo

pronto quando: com uma mensagem real da conta `trabalho` no grupo da tarefa 2,
`POST localhost:3005/api/react` com `from_me: false` devolve `"success": true` e a
reação aparece no store da conta `trabalho` — provado por `SELECT COUNT(*)` da
tabela de mensagens da conta `trabalho` filtrando o id reagido, antes e depois,
mais confirmação visual no celular de que a reação está no balão certo.

### 7. Superfície de leitura: REST e servidor MCP [tipo: implementar]
atende: D13
arquivos: `whatsapp-bridge/main.go`, `whatsapp-bridge/main_test.go`, `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/test_reply_mentions.py`, `whatsapp-mcp-server/test_display_name.py`, `whatsapp-mcp-server/test_public_dict.py`
depende de: 3, 4, 5
paralela: nao

`APIMessage` ganha `quoted_message_id`, `quoted_sender`, `quoted_content` e
`mentions`; **todas** as consultas que alimentam `scanAPIMessageRow` ganham as
colunas na mesma ordem (`listMessages`, as três de `getMessageContext`,
`getLastInteraction`). No Python, `Message` e `_message_from_dict` ganham os
campos, `format_message` passa a mostrar a citação e as menções **por nome**
(`get_sender_name`), e `send_message`/`send_file` ganham `quoted_message_id` e
`mentions`.

mutacao:
  arquivo: `whatsapp-mcp-server/whatsapp.py`
  de: o trecho de `format_message` que acrescenta a linha da citação
  para: não acrescentar nada
  bateria: `cd whatsapp-mcp-server && python -m unittest test_reply_mentions -v`
  fixture: `test_format_message_mostra_citacao_por_nome` — mensagem com `quoted_message_id`, exige a linha de citação com o nome resolvido e **nenhum** dígito de telefone

pronto quando: com a resposta real recebida na tarefa 3 já no store,
`list_messages` limitado àquele chat imprime a linha de citação com nome e id, e
nenhuma sequência de 8+ dígitos — provado por rodar a tool contra a ponte no ar e
passar a saída por `grep -E '↳ reply to'` (casa) e `grep -E '[0-9]{8,}'` (não
casa).

### 8. Validação ponta a ponta no celular [tipo: teste]
atende: D2, D11, D16
arquivos: `docs/rainforest/estado/2026-09-08-responder-citando-e-mencionar.json`
depende de: 2, 3, 4, 5, 6, 7
paralela: nao

Exercita, no grupo da tarefa 2, os quatro gestos: citar mensagem de terceiro,
citar mensagem de **mídia**, mencionar por nome, e reagir a mensagem de terceiro.
A prova de que o contexto viajou pelo fio não é o que a ponte emissora acha ter
mandado — é o que a ponte **receptora** gravou.

mutacao: n/a
  motivo: tarefa de validação; não introduz código. A falsificação é a própria
  medição na ponta receptora, que fica vermelha se o `ContextInfo` não viajar.

pronto quando: para cada um dos quatro gestos disparados de `localhost:3005`, a
linha correspondente no store da conta `trabalho`
(`accounts/trabalho/store/messages.db`, modo `ro`) traz o campo esperado
preenchido — `quoted_message_id` não nulo para as duas citações, `mentions <> '[]'`
para a menção — **e** o Luís confirma, olhando o celular, que o balão de citação
mostra autor e trecho, que a menção aparece grifada, e que a reação está na
mensagem certa. Citação de mídia com prévia vazia ou feia é resultado registrado,
não falha: a D11 previu, e o que se decide aqui é se vira recusa.

### 9. Documentação da superfície nova [tipo: docs]
atende: D4, D6
arquivos: `README.md`, `AGENTS.md`
depende de: 7
paralela: nao

Documenta como se cita e como se menciona: que o texto leva `@Nome`, que a lista
`mentions` leva **nome** e nunca número, e o que acontece na ambiguidade (recusa
com `ref` opaco, e como reenviar com o `ref`).

mutacao: n/a
  motivo: documento não tem comportamento a inverter.

pronto quando: o que o README descreve casa com a interface real, conferido campo
a campo — cada parâmetro citado no README existe na assinatura da tool
correspondente em `whatsapp-mcp-server/main.py`, e cada parâmetro novo dessas
assinaturas aparece no README; provado por listar os dois conjuntos e exigir
diferença vazia nos dois sentidos. O texto da ambiguidade descreve o mesmo
protocolo que a tarefa 5 implementou: recusa, lista de `ref`, reenvio por `ref` —
e não uma escolha automática.

### 10. Branch e PR no fork [tipo: configurar]
atende: D18
arquivos: `docs/rainforest/estado/2026-09-08-responder-citando-e-mencionar.json`
depende de: 1, 3, 4, 5, 6, 7, 9
paralela: nao

Branch a partir de `origin/main`, PR contra `luisfmontes/whatsapp-mcp`. Nada de PR
novo para o upstream.

mutacao: n/a
  motivo: tarefa de entrega; não introduz comportamento.

pronto quando: o PR existe, aponta para a base certa e a CI fecha verde — provado
por `gh pr view <n> --repo luisfmontes/whatsapp-mcp --json baseRefName,state,statusCheckRollup`
devolvendo `baseRefName: "main"`, `state: "OPEN"` e nenhum check em `FAILURE` ou
`PENDING`.

### 11. Registro do bug de autoria perdida [tipo: docs]
atende: D17
arquivos: `docs/rainforest/design/2026-09-08-responder-citando-e-mencionar.md`
depende de: nenhuma
paralela: sim

Issue própria para o `sender` gravado como o JID do grupo, com as contagens
agregadas e nenhum identificador. Já executada em 2026-09-08.

mutacao: n/a
  motivo: registro externo; não há comportamento a inverter.

pronto quando: a Issue está aberta no fork e o corpo não contém identificador —
provado por `gh issue view 18 --repo luisfmontes/whatsapp-mcp --json state --jq .state`
devolvendo `OPEN`, e `gh issue view 18 --repo luisfmontes/whatsapp-mcp --json body --jq .body | grep -cE '[0-9]{10,}@|55[0-9]{9,}'`
devolvendo `0`.

## Emendas

Registradas aqui porque escopo que cresce sem rastro é escopo que ninguém
revisa. Cada uma nasceu de um defeito medido, não de conveniência.

- **2026-09-09 — tarefa 7 ganha dois arquivos de teste.**
  `whatsapp-mcp-server/test_display_name.py` e
  `whatsapp-mcp-server/test_public_dict.py` não existiam quando o plano foi
  escrito: o primeiro cobre o vazamento de JID na linha de leitura, achado ao
  rodar contra as pontes reais; o segundo cobre o vazamento por
  `get_message_context`, achado pela revisão independente. Sem a emenda os dois
  seriam creep — arquivo no diff sem tarefa que o reivindique.

- **2026-09-09 — tarefa 9 não toca `AGENTS.md`.** O plano listava os dois
  arquivos; só o `README.md` mudou. O `AGENTS.md` descreve como se trabalha
  neste repositório, e nada em como se trabalha mudou com esta entrega — o que
  mudou foi a interface, que é assunto do README. A lacuna fica registrada em
  vez de ficar por explicar.

- **2026-09-09 — o critério da tarefa 1 compara a contagem consigo mesma.**
  Trazia o literal `128377`, medido antes da execução. A ponte está no ar e
  recebe mensagem o tempo todo: na hora da execução eram 128.400. Número fixo
  de tabela viva transforma medição correta em falso reprovado.

- **2026-09-09 (rodada 2) — os artefatos do próprio fluxo passam a ser
  reivindicados.** `docs/rainforest/planos/2026-09-08-responder-citando-e-mencionar.md`
  (este arquivo) e `docs/rainforest/pedidos/2026-09-08-responder-citando-mensagem.md`
  estavam no diff sem `arquivos:` de tarefa nenhuma — creep pela letra da regra,
  ainda que sejam papelada do fluxo e não escopo de produto. Ficam sob a
  tarefa 11, que já é a tarefa de registro. A régua tem que ser a mesma que
  cobrou os dois arquivos de teste acima.

- **2026-09-09 (rodada 2) — tarefa 7 ganha `whatsapp-mcp-server/test_scrub_mentions.py`.**
  A revisão achou o vazamento um campo ao lado do que a rodada 1 fechou:
  `quoted_content` é o corpo da mensagem citada, e um corpo que menciona alguém
  carrega `@<número>` por protocolo — citar quem mencionou um terceiro punha o
  número do terceiro na leitura. O arquivo cobre isso e a limpeza equivalente
  no corpo de linhas antigas, sem a coluna `mentions`.

- **2026-09-09 (rodada 2) — nasce a tarefa 12, que o plano não tinha.**
  `arquivos:` `scripts/check-personal-data.py`,
  `docs/rainforest/estado/2026-09-08-responder-citando-e-mencionar.json`,
  `whatsapp-bridge/main_test.go`, `whatsapp-mcp-server/main.py`,
  `_reversa_forward/003-whatsmeow-gaps-7-8-9/interfaces/group-participants.md` —
  `atende:` D3.
  A tarefa 2 mandava o JID do grupo de teste ficar "no estado do fluxo, nunca em
  arquivo versionado", e neste repositório o estado do fluxo **é** versionado: o
  JID real do grupo entrou em `docs/` e ficou lá. Pior que o descuido é o
  motivo de ninguém ter visto: `scripts/check-personal-data.py` só conhecia
  `55<telefone>` e `@s.whatsapp.net`, então saiu verde por cima do arquivo que
  continha um JID real. A tarefa remove o JID e ensina a trava a enxergar
  `@g.us` e `@lid`. Os tres `@g.us` que ja existiam no repositorio (dois em
  docstring, um em doc de interface) sao sinteticos — conferido em leitura que
  nenhum deles casa com linha de `chats` em nenhum dos dois stores locais — e
  saem trocados por forma sem digitos, em vez de entrarem no baseline: JID de
  grupo literal no baseline dispara o proprio gate de publicacao, e ele tem
  razao. Baseline e para o que precisa ficar; isto nao precisava.
  `pronto quando:` com um JID de grupo em arquivo versionado fora do baseline,
  `python scripts/check-personal-data.py` sai 1 e nomeia o arquivo — provado por
  `conferir-mutacao.cjs` sobre `scripts/check-personal-data.py`, inserindo um
  literal `@g.us` fora do baseline e exigindo a bateria vermelha.

- **2026-09-09 (rodada 3) — `send_file` perde o `mentions` que a tarefa 7 lhe
  deu.** O parâmetro existia, era documentado no docstring e no README, e não
  podia dar certo em chamada nenhuma: a rota de mídia não manda `message`, e a
  ponte usa `message` tanto como legenda quanto como texto onde a menção é
  ancorada. Sem âncora o WhatsApp não grifa nada — e, depois da recusa por
  âncora ausente introduzida na rodada 2, a ponte passou a devolver 400 para
  100% das chamadas. Some da assinatura em vez de ficar prometendo. Quem quiser
  mencionar junto de um arquivo manda o texto por `send_message` primeiro e o
  arquivo depois, que é a ordem que a skill de mensagens já prescreve.
  `quoted_message_id` fica: esse funciona, porque o `ContextInfo` não depende de
  texto.

- **2026-09-09 (rodada 3) — tarefa 7 ganha a limpeza de `Chat.last_message`.**
  A alegação fechada na rodada 2 era "toda superfície de leitura passa pelo
  scrub", e era falsa: `list_chats`, `get_chat`, `get_contact_chats` e
  `get_direct_chat_by_contact` devolvem `last_message`, que é o corpo cru de
  `messages.content`. Basta a última mensagem do chat ser uma menção para o
  número do mencionado sair pela tool mais usada do servidor. Pré-existente ao
  trabalho, mas dentro da D3 e dentro da alegação que se fez.

- **2026-09-11 (rodada 3, bloqueante 1) — a tarefa 12 ganha a reescrita da
  branch.** Tirar o JID do arquivo num commit posterior não desfaz um push: os
  nove commits de `05316f7` a `af34dd5` já estavam em `origin`, que é fork de
  repositório público, e o próprio docstring da trava
  (`scripts/check-personal-data.py`) diz por quê — "in a fork network the object
  is still served by SHA". Pior, o job `personal data` da CI roda sobre a
  *working tree*, nunca sobre o histórico: ele era verde e sempre seria, por
  construção. A branch foi reescrita com `git filter-branch --tree-filter` sobre
  `origin/main..HEAD` e empurrada com `--force-with-lease`.
  `pronto quando:` nenhum commit de `origin/main..origin/feat/citacao-e-mencao`
  serve o campo com forma de JID de grupo — provado por laço de `git show` sobre
  `git rev-list` procurando `[0-9]{12,25}@g\.us` no arquivo de estado, sem
  nenhuma saída; e `git diff --stat backup/citacao-antes-da-reescrita HEAD`
  vazio, provando que só o histórico mudou, não a entrega.
  Limite que fica escrito em vez de ficar implícito: objetos órfãos podem seguir
  alcançáveis por SHA no GitHub até o GC da rede de forks. O dado é um JID de
  grupo (opaco, não deriva de telefone, não dá acesso sem convite), então não se
  abriu chamado no Support — a decisão foi essa, não um esquecimento.
