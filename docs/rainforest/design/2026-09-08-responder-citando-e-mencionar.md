# Responder citando mensagem, e mencionar pessoas

## Objetivo

Dar à ponte duas capacidades que ela não tem: **responder citando uma mensagem**
— inclusive mensagem de terceiro em grupo — e **mencionar pessoas**. Nos dois
sentidos: montar o `ContextInfo` no envio, e ler o `ContextInfo` que chega, para
que se saiba qual mensagem foi citada quando alguém responde.

Pedido de origem: `docs/rainforest/pedidos/2026-09-08-responder-citando-mensagem.md`
(sessão do Tech Challenge FIAP, 2026-09-08). Menção não vem de lá — vem do Luís,
no mesmo dia.

Estado do código antes desta entrega: `grep -n 'ContextInfo\|QuotedMessage\|StanzaID\|mention'`
em `whatsapp-bridge/main.go` e `whatsapp-mcp-server/whatsapp.py` = zero ocorrências.
Nada a migrar de comportamento anterior; é capacidade nova nas duas pontas.

## Decisões fechadas

- **D1 — as duas capacidades no mesmo lote** — porquê: citação e menção viajam no
  mesmo `ContextInfo`, montado no mesmo ponto do envio e lido no mesmo ponto da
  recepção. Separar pagaria duas vezes o mesmo encanamento, e a segunda metade
  chegaria com a primeira já congelada.

- **D2 — `react` e `delete` de mensagem de terceiro em grupo entram, como tarefa
  própria e destacável do plano** — porquê: hoje os dois recusam explicitamente
  (`main.go:1268-1273` e `main.go:1351-1356`, HTTP 400 "participant JID
  unavailable"), e o dado que falta é o mesmo que a citação passa a ter. Medido na
  biblioteca: `BuildReaction(chat, sender, id, reaction)` (`send.go:534`) e
  `BuildRevoke(chat, sender, id)` (`send.go:519`) pedem o JID do autor e **nada
  mais** — a doc de `BuildRevoke` diz que admin revoga de terceiro passando o JID
  do autor como segundo parâmetro (`send.go:516-518`). Não há campo, chave ou
  permissão adicional exigida pela assinatura. Tarefa destacável porque o risco
  novo é humano, não técnico: passa a ser possível apagar mensagem de terceiro em
  grupo por engano, o que hoje é impossível por acidente.

- **D3 — menção se faz por NOME na superfície; número não aparece em lugar nenhum
  do caminho** — porquê: palavra do Luís, 2026-09-08. Nem na chamada, nem na
  resposta de erro, nem na desambiguação. Consequência aceita: a ponte precisa
  descobrir o número por conta própria e, quando não conseguir decidir sozinha,
  precisa de um jeito de perguntar sem falar o número (D6).

- **D4 — o texto carrega `@Nome`, e a ponte troca por `@<número>` no envio** —
  porquê: o WhatsApp só grifa a menção se o corpo do texto contiver `@<número>`
  casando com o JID em `MentionedJID`. Como a D3 tira o número das mãos de quem
  chama, quem tem que inserir é a ponte. Escrever `@Nome` no texto mantém a
  posição da menção na frase sendo escolha de quem escreve — a alternativa
  (anexar as menções no começo da mensagem) sai com cara de robô.
  Isso inverte uma decisão anterior minha ("a ponte não reescreve o texto"), que
  não sobrevive à D3: quem não vê o número não pode digitá-lo.

- **D5 — a ponte só troca os nomes que vierem na lista `mentions`, nunca varre o
  texto atrás de `@`** — porquê: `@` aparece em texto normal (e-mail, handle,
  preço). Varrer significaria a ponte adivinhar o que é menção; com a lista
  explícita, `@Nome` é substituído se e só se `Nome` foi pedido. Texto com `@`
  que ninguém listou passa intacto.

- **D6 — a busca do nome é restrita aos participantes do chat de destino, e nome
  ambíguo recusa o envio devolvendo os candidatos com identificador opaco** —
  porquê: mencionar quem não está no grupo não notifica ninguém, e buscar na
  agenda inteira arriscaria marcar um estranho de nome parecido. Dois "Rodrigo" no
  grupo e a ponte escolhendo sozinha manda menção para a pessoa errada, num grupo,
  sem volta. A resposta traz `ref: c1 — "Rodrigo" (nome na agenda)`,
  `ref: c2 — "Rodrigo PG" (nome no WhatsApp)`, e o reenvio vai com o `ref`. O
  `ref` existe porque devolver "o nome exato" quebra de novo quando os dois nomes
  são idênticos — e porque o `ref`, ao contrário do número, pode circular pela
  conversa e pelo log sem ser dado pessoal. Neste repositório em particular, cuja
  história recente é sobre número real vazado, essa é a metade que importa.

- **D7 — nome sem correspondência no chat: HTTP 4xx, nada enviado** — porquê:
  mesmo critério que o pedido já fixou para `quoted_message_id` inexistente. O
  contrário é a mensagem sair com um `@Fulano` literal que não marca ninguém, e
  o autor só descobrir olhando o celular.

- **D8 — persistir o JID completo do autor em coluna nova, em vez de reconstruir
  `<sender>@s.whatsapp.net`** — porquê: medido no store real da conta pessoal
  (`whatsapp-bridge/store/messages.db`, 128.377 mensagens, somente leitura,
  agregados): em **9.433 mensagens, de 12 grupos**, o `sender` gravado é o JID do
  **próprio grupo**, não da pessoa — `chat_jid LIKE sender||'@%'` casa exatamente
  9.433 vezes, todas em `@g.us`, zero em 1:1, zero com `is_from_me=1`. São 23,5%
  das mensagens de grupo dessa conta. Reconstruir montaria o JID de uma pessoa
  que não existe. `handleMessage` já calcula `senderJID` completo
  (`main.go:1017-1018`) e simplesmente não o persiste — o dado passa pela ponte
  hoje, só não é guardado.

- **D9 — citar mensagem cujo autor é desconhecido recusa, com erro específico** —
  porquê: para as 9.433 da D8 nenhum backfill resolve, porque o dado nunca foi
  gravado. Enviar sem `Participant` faria o balão sair sem autor ou com autor
  errado num grupo. O erro diz que o autor é desconhecido naquele trecho do
  histórico e que a partir de agora fica guardado.

- **D10 — preencher sempre `ContextInfo.QuotedMessage`** — porquê: a biblioteca
  não documenta se o app do destinatário precisa do conteúdo citado ou se
  `StanzaID` + `Participant` bastam. Procurado em código, comentário, teste e
  proto (`proto/waE2E/WAWebProtobufsE2E.proto:1470-1472` traz os três campos sem
  nenhum doc-comment) — não há resposta na lib. Preencher não custa e não aposta
  em comportamento não documentado.

- **D11 — citar mensagem de mídia é permitido, com prévia degradada** — porquê: o
  pedido punha fora de escopo, mas recusar tiraria uma capacidade que pode já
  funcionar: a citação viaja por `StanzaID`, e a prévia é enfeite. O que a ponte
  consegue montar hoje é a legenda guardada (ou vazio, quando não há). Se o balão
  sair feio no celular, aí sim se recusa — com prova, não por precaução.

- **D12 — `quoted_message_id` inexistente no store: HTTP 4xx, nada enviado** —
  porquê: critério do pedido de origem, mantido.

- **D13 — a leitura expõe a citação e as menções** — porquê: metade do pedido é
  *identificar* qual mensagem foi citada. `handleMessage` passa a extrair o
  `ContextInfo` de qualquer tipo que o carregue (`ExtendedTextMessage` e os tipos
  de mídia; texto puro chega como `Conversation` e não tem `ContextInfo`), a REST
  passa a devolver os campos, e a formatação do `list_messages` mostra a quem a
  mensagem responde e quem ela menciona — por nome, nunca por número, coerente
  com a D3.

- **D14 — texto com `ContextInfo` sai como `ExtendedTextMessage`, não como
  `Conversation`** — não é escolha, é a forma do protocolo: `Conversation` é uma
  string e não carrega `ContextInfo`. A doc da lib diz isso explicitamente
  ("Things like replies, mentioning users and the 'forwarded' flag are stored in
  ContextInfo, which can be put in ExtendedTextMessage and any of the media
  message types", `send.go:176-177`). Fica registrado porque muda o caminho de
  envio mais usado do projeto, e porque não existe helper na lib — os `Build*`
  cobrem reação, revoke, edição, enquete e history sync, e **não** citação.

- **D15 — migração idempotente por `PRAGMA table_info`, além das colunas no
  `CREATE TABLE`** — porquê: `CREATE TABLE IF NOT EXISTS` não acrescenta coluna
  em banco que já existe, e o banco em uso tem 128.377 mensagens. Sem a migração
  a ponte sobe e quebra na primeira consulta.

- **D16 — a validação real é o balão no celular, num grupo de teste entre as duas
  contas do Luís (`pessoal` e `trabalho`)** — porquê: o critério do pedido é
  visual e não tem teste automatizado que o substitua. Da ótica da conta pessoal,
  a conta de trabalho é um terceiro em grupo — que é exatamente o caso crítico —
  sem mandar mensagem de teste para pessoas de verdade em grupo alheio.

- **D17 — o `sender` gravado como grupo vira Issue própria, não conserto deste
  lote** — porquê: é bug anterior e independente (autoria perdida em 12
  conversas); investigar a causa aqui dobraria o escopo, e deixar sem registro
  perde o achado. A Issue leva só contagens agregadas, nenhum identificador.

- **D18 — entrega em branch + PR no fork (`luisfmontes/whatsapp-mcp`)** — porquê:
  é o que o `AGENTS.md` pede, e é o que deixa a CI rodar antes de entrar. Nada de
  PR novo para o upstream (D7 do design de 2026-08-23).

## Avaliado e descartado

- **Reconstruir `<sender>@s.whatsapp.net` na hora do envio, sem coluna nova** —
  erra em 9.433 mensagens medidas, onde o `sender` guardado é o grupo. Era a
  decisão que eu tinha tomado sozinho antes do fluxo; o dado a derrubou.
- **Mencionar passando número ou JID** — mais simples e foi minha recomendação
  inicial; recusada pelo Luís, e a recusa tem mérito próprio neste repo: número
  que não circula não vaza.
- **A ponte escolher sozinha entre nomes ambíguos** — menção errada em grupo não
  tem desfazer.
- **Devolver os nomes candidatos e reenviar pelo nome exato** — quebra quando os
  dois nomes são idênticos, que é justamente o caso que a desambiguação existe
  para resolver.
- **Buscar o nome na agenda inteira** — mencionar quem não está no grupo não
  notifica ninguém e amplia o alvo de erro.
- **Varrer o texto atrás de `@` para descobrir menções** — `@` aparece em texto
  normal; a ponte adivinharia.
- **Usar helper da biblioteca para montar a citação** — não existe. Verificado:
  os `Build*` do client são `BuildMessageKey`, `BuildRevoke`, `BuildReaction`,
  `BuildUnavailableMessageRequest`, `BuildHistorySyncRequest`, `BuildEdit`,
  `BuildPollVote`, `BuildPollCreation`. Nenhum de citação.
- **Enviar só `StanzaID` + `Participant`, sem `QuotedMessage`** — a lib não
  documenta que baste; seria apostar em comportamento não observado.
- **Recusar citação de mídia** — ver D11.
- **`StoreMessage` ganhar mais parâmetros** — já tem 13 e semântica delicada de
  `COALESCE(NULLIF(...))` que existe para não apagar transcrição em re-sync
  (`main.go:433-487`). O contexto entra por caminho próprio.

## Fora de escopo

- **Consertar a autoria perdida nos 12 grupos** — Issue própria (D17).
- **Menção automática do autor da mensagem citada** — o pedido de origem já
  colocava fora, e continua fora: citar e mencionar são gestos diferentes.
- **Citar mensagem por busca de conteúdo** ("responde aquela do link") — a
  citação se faz por id, que `list_messages` já imprime.
- **Backfill do JID do autor no histórico** — impossível para as 9.433 (o dado
  nunca existiu) e desnecessário para o resto (o número está lá).

## Em aberto

- (nada)
