# Data delta — ciclo 005

**Primeiro ciclo da colheita com mudança de dados.** Os ciclos 002, 003 e 004 não tocaram o banco;
este toca, e é por isso que o #11 não entrou de carona em nenhum deles.

| Aspecto | Situação |
|---------|----------|
| Tabelas novas | `polls`, `poll_votes` |
| Índices novos | `idx_poll_votes_poll` |
| Tabelas alteradas | **nenhuma** — `messages`, `chats` e `senders` intocadas |
| Migração | não se aplica |
| Backfill | impossível, e é uma limitação real (ver abaixo) |

## Por que não precisa de framework de migração

O schema de `messages.db` é criado num único `CREATE TABLE IF NOT EXISTS` que roda em todo start da
bridge (main.go ~82). Tabela nova entra nesse bloco e aparece sozinha no próximo start, em banco
novo ou existente. Nada a versionar, nada a reverter — em banco antigo as tabelas simplesmente
passam a existir, vazias.

Rollback é seguro por consequência: voltar para um binário anterior deixa as duas tabelas órfãs no
banco, sem nenhum código lendo ou escrevendo nelas. Não quebra leitura de mensagem.

## Por que não dá para fazer backfill

`polls` só pode ser preenchida a partir do evento de criação da enquete, que a bridge precisa ter
visto. Enquete criada antes desta feature **não tem como ser recuperada** — o WhatsApp não oferece
consulta, e `messages.db` nunca guardou as opções porque `handleMessage` descartava a mensagem
inteira por não ter texto nem mídia.

Consequência prática: votos de enquetes antigas chegam com `resolved = 0`. É a razão de existir
dessa coluna — o voto fica gravado e contado como não resolvido, em vez de sumir (RN-05).

## Crescimento

Desprezível. Uma linha por enquete e uma por votante por enquete, contra as ~113k linhas de
`messages` já existentes. Sem BLOB, sem mídia.
