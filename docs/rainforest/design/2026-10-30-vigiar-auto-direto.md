# vigiar --auto envia direto fora das exceções

## Objetivo
Com a vigia armada em `--auto`, o assistente parou duas vezes em 2026-10-30, em sessões diferentes, para pedir o ok do Luís antes de enviar. Nas duas ele corrigiu com "cara vc ta com resposta automatica" (Issue #52):
- uma mensagem de status simples, já confirmada na conversa, em resposta a quem a vigia acompanhava;
- um material já verificado que o mesmo pedido de arme mandava enviar.

A seção 6 de `commands/vigiar.md` diz "Skip the approval step", e mesmo assim perdeu por três motivos:
1. Ela só fala de reply, e não diz nada sobre a mensagem pedida junto com o arme.
2. Ela manda carregar o `message-standards`, cuja regra 3 exige rascunho e ok "a menos que ele peça explicitamente para enviar direto", sem dizer que o `--auto` é esse pedido.
3. A memória de sessão "autorização de envio não é permanente" repete a regra 3.

Este trabalho torna o `--auto` explícito no próprio `vigiar.md`. A regra 3 do `message-standards` é consertada no repositório dela (Issue #2 de lá), na mesma rodada.

Conduzido pela sessão em 2026-10-30. O Luís escolheu "consertar agora, um fluxo por repositório" e autorizou as alterações nos dois repositórios.

## Decisões fechadas
- **D1 — A seção 6 diz que armar com `--auto` é o pedido explícito de envio direto que a regra 3 do `message-standards` exige.** Isso vale enquanto a vigia daquele contato estiver armada, e prevalece sobre qualquer regra de "mostrar o rascunho antes" que a skill ou a memória da sessão tragam. O processo relação → intenção → rascunho → auto-checagem continua inteiro; só o "mostrar e esperar" sai. Porquê: o texto atual pede para pular a aprovação, mas não nomeia a regra que ele derruba, e a regra nomeada ganhou nas duas vezes.
- **D2 — O envio direto cobre também a mensagem que o próprio pedido de arme manda enviar.** Exemplo: "manda o X pra ele e vigia com resposta automática". Ela sai sem rascunho, com o rodapé de resposta automática. Porquê: o segundo caso de 2026-10-30 foi exatamente esse, e a seção 6 só falava de reply.
- **D3 — A lista "Always stop and ask" passa a ser declarada como a lista inteira de exceções.** Fora dela, o assistente envia. A lista e a regra de número novo do `message-standards` não mudam. Porquê: sem dizer que é exaustiva, qualquer dúvida vira motivo para parar, que foi o padrão das duas correções.
- **D4 — Fora do `--auto`, nada muda.** A seção 5 (modo padrão: rascunho e espera) e a lição colhida em 2026-09-18 continuam valendo: "envia X" autoriza o artefato, não o texto que o acompanha. O `--auto` é o sinal explícito de "manda direto" que aquela lição exige. Porquê: o conserto não pode reabrir o erro oposto, o envio sem rascunho num pedido que não armou resposta automática.

## Avaliado e descartado
- **Consertar só o `message-standards`.** O `vigiar.md` é quem define o modo e é lido primeiro. Deixar a exceção só na skill repete o problema de duas fontes, com uma delas omissa.
- **Trava mecânica (hook que libera `send_message` com vigia `--auto` armada).** Hoje não existe nenhum hook que barre o envio; o problema é de instrução, não de trava.

## Fora de escopo
- A memória `feedback_autorizacao_de_envio_nao_e_permanente`, que mora fora dos repositórios. Recebe à parte a ressalva do `--auto`, na mesma rodada.

## Varredura
docs/rainforest/varredura/2026-10-30-vigiar-auto-direto.txt — a Issue #52 é este trabalho. A #35 e o PR #39 trouxeram o `message-standards` para o vigiar, e o PR #32 criou o `--auto`; nenhum tratou da regra 3. Das ideias casadas, `whatsapp-enviado-sem-mostrar-rascunho` (colhida) é o limite do D4, e `whatsapp-auto-sem-message-standards` (colhida) já está coberta pela seção 6, que manda carregar a skill.

## Em aberto
nada
