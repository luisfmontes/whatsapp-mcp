# O 500 do include_last_message, e o erro de API que lê como lista vazia

## Objetivo

Consertar dois endpoints REST do bridge que devolvem HTTP 500 quando
`include_last_message` é `false`, e fechar o mecanismo que fez esse defeito
ficar três semanas invisível: o servidor MCP traduz qualquer erro de API em
lista vazia, então "o endpoint está quebrado" chega ao modelo como "não há
chats".

Como o defeito foi encontrado: uma chamada de `list_chats` com
`include_last_message: false` devolveu "no output", e só bater no bridge com
`curl` revelou o 500 por trás.

## Decisões fechadas

- **D1 — a projeção mantém 6 colunas, com `NULL as last_message`, `NULL as last_sender` e `NULL as last_is_from_me` quando o `JOIN` não entra** — porquê: `scanAPIChatRow` (`whatsapp-bridge/main.go:3317`) escaneia 6 colunas fixas e é compartilhado por 4 chamadores (`listChats`, `getContactChats`, `getChat`, `getDirectChatByContact`). Projetar só 3 colunas quebraria o scan nos quatro. Com os placeholders o `JOIN` continua não sendo pago — que é o motivo de a flag existir —, nenhum chamador muda, e os campos chegam como `null`, que é exatamente o que a struct `APIChat` já modela (todos ponteiros, sem `omitempty`). Não há mudança de contrato de wire.

- **D2 — `_api_post` levanta `ValueError` em 5xx, e mantém `None` em 4xx, timeout e erro de conexão** — porquê: 5xx é bug do servidor e precisa ser alto; 4xx e timeout são o caminho degradado que a D12 da entrega de multiconta já trata levantando por conta própria, e mexer neles alteraria de uma vez as ~20 leituras que passam por essa função. O tipo é `ValueError` por precedente vivo e não por gosto: a D12 já faz `Account 'x' not found...` subir legível no cliente MCP como `Error executing tool ...`. Exceção própria (`BridgeAPIError`) só se pagaria se alguém capturasse por tipo, e nesta entrega ninguém captura. Risco a confirmar no `executar`: algum dos 94 testes Python pode assumir lista vazia em 500.

- **D3 — o teste prova nas duas camadas: Go chamando `listChats` e `getChat` direto contra um SQLite temporário, e Python no `_api_post`** — porquê: hoje **nenhum** teste toca esses caminhos — 36 funções `Test*` no `main_test.go` e nenhuma menciona `listChats`, `getChat` ou `include_last_message`. É isso, e não sorte, que explica a suíte verde com dois endpoints quebrados. O teste Go segue o padrão `os.Chdir(t.TempDir())` que o repo já usa, então falha sem depender de conta pareada; o teste Python cobre a mudança de comportamento da D2, que é a que toca mais código.

- **D4 — a entrega vai em branch + PR no fork (`luisfmontes/whatsapp-mcp`), não em commit direto na `main`** — porquê: é o que o `AGENTS.md` pede no checklist de PR, e é o que deixa a CI rodar antes de entrar.

- **D5 — PR para o upstream (`rodrigopg/whatsapp-mcp`) depois que o PR do fork estiver verde, cobrindo os dois sítios** — porquê: o defeito é herdado, não regressão local — nasceu em `0b6f3f4` (2026-07-30) e o upstream, cujo HEAD é posterior (`62ab090`, 2026-08-03), carrega as duas funções com a mesma forma (linhas 2004 e 2499 lá). É agnóstico de SO, que é a condição que o `AGENTS.md` exige para PR upstream, e tem repro de uma linha de `curl` — o tipo de PR que não depende de o autor topar o roadmap deste fork.

## Avaliado e descartado

- **Projetar só 3 colunas e escrever um segundo scan para essa forma** — `scanAPIChatRow` é compartilhado por 4 chamadores; o segundo caminho de scan custaria manutenção permanente sem entregar nada que os placeholders `NULL` não entreguem.
- **Fazer o `JOIN` sempre e usar a flag só para decidir o que volta na resposta** — apaga o sentido da flag, que existe para não pagar o `JOIN`. Medido no store da conta `pessoal`: 2.476 chats e 121.050 mensagens.
- **Levantar em qualquer não-200 no `_api_post`** — mudaria o comportamento de ~20 leituras de uma vez, inclusive o caminho degradado (bridge fora, timeout) que a D12 já resolve de outra forma.
- **Exceção própria `BridgeAPIError`** — ninguém captura por tipo nesta entrega; o precedente da D12 (`ValueError`) já sobe legível no cliente.
- **Provar só por teste de ponta a ponta contra o bridge no ar** — exige conta pareada e não roda na CI. Serve como conferência manual, não como rede.

## Fora de escopo

- **Os outros quatro pontos do arquivo que montam SQL por partes** — `getDirectChatByContact`, `listMessages`, `getContactChats` e `getMessageContext`. Foram auditados um a um: o `JOIN` deles é incondicional ou não existe flag controlando, então não são o mesmo defeito. Não é omissão, é medição.
- **Transcrição por conta** — `whatsapp-mcp-server/db_path.py` e `transcribe.py` não têm nenhuma noção de conta (`WHATSAPP_MESSAGES_DB` está fixo na `pessoal`), então o backfill manual só alcança a conta default. É outro assunto e merece slug próprio; o sweep dentro do bridge está correto, porque o launcher faz `Set-Location` na pasta da conta.
- **Multiconta em si** — validada contra o sistema no ar antes desta entrega (duas contas logadas, dados isolados, guard de escrita da D2 levantando, apelido desconhecido com erro claro, `go test` e 94 testes Python verdes). Nada a mudar.

## Em aberto

- (nada)
