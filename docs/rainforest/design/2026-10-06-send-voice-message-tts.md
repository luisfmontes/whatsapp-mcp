# send_voice_message: texto vira mensagem de voz (upstream)

## Objetivo
Uma tool MCP nova no `rodrigopg/whatsapp-mcp`, `send_voice_message(recipient, text, account)`, que transforma texto em fala e manda como mensagem de voz (PTT) do WhatsApp, reaproveitando o envio que já existe. Pedida pelo Rodrigo; entra como PR pequeno direto no upstream (base `main` em b76398a) e volta para o fork pela sincronização.

## Decisões fechadas
- **D1 — Público e momento: quem roda o MCP do upstream, quando o agente precisa responder por voz (ex.: contato que só manda áudio)** — porquê: é o pedido do Rodrigo; a tool é genérica, sem nada específico do Luís no código.
- **D2 — Tool nova `send_voice_message(recipient, text, account)`, ao lado de `send_audio_message`, que fica intocada** — porquê: a nova só gera o áudio e delega o envio ao caminho existente (`whatsapp_audio_voice_message`), sem mudar contrato de tool já publicada.
- **D3 — `TTS_ENGINE` aceita `local` ou `api`; sem configurar, a tool devolve "not configured" e não envia nada** — porquê: mesmo molde do `TRANSCRIPTION_ENGINE` (opt-in, `engine_ready()` com motivo). `local` = quem não quer API paga; `api` = quem já tem chave de um endpoint `/audio/speech` compatível com OpenAI.
- **D4 — Motor local é programa externo apontado por caminho (`TTS_CLI` + `TTS_MODEL`, ex.: binário do piper lendo o texto por stdin e gravando WAV); nenhuma dependência nova no `pyproject`** — porquê: repete o padrão do whisper.cpp (`WHISPER_CLI`/`WHISPER_MODEL`), e o upstream acabou de enxugar dependências (#27). Processo externo também isola licença: piper1-gpl (GPL-3) não contamina o código.
- **D5 — Modelo de voz nunca entra no repo; o README aponta onde baixar, sugere uma voz pt_BR do Piper e avisa da licença herdada** — porquê: as vozes pt_BR (faber, cadu, jeff, edresson) têm dataset CC0, mas são finetune da `lessac` (en_US), cujo dataset tem licença própria (Blizzard 2013); a escolha da voz é de quem instala.
- **D6 — Sem confirmação no código, igual ao `send_message`; texto acima de 4096 caracteres é recusado com erro claro, sem truncar** — porquê: confirmar é política de quem chama (a skill do Luís confirma); 4096 é o limite da API OpenAI e truncar em silêncio mandaria mensagem diferente da pedida.
- **D7 — A saída de qualquer motor passa pelo `convert_to_opus_ogg` existente (ffmpeg) antes do envio, em arquivo temporário apagado depois** — porquê: normaliza WAV do piper e opus/mp3 da API num formato só, que o bridge já sabe analisar (duração e waveform); ffmpeg ausente vira erro claro, como já acontece no `send_audio_message`.
- **D8 — Configuração do `api`: `TTS_API_KEY`, `TTS_API_BASE` (padrão `https://api.openai.com/v1`), `TTS_API_MODEL` (padrão `gpt-4o-mini-tts`), `TTS_API_VOICE` (padrão `alloy`); próprias, sem reaproveitar as variáveis da transcrição** — porquê: transcrição e fala podem ir para provedores diferentes (Groq transcreve, não fala).
- **D9 — Testes em arquivo novo `test_tts.py`, sem rede e sem motor real: CLI falso (script que grava um WAV) para o `local`, servidor HTTP falso para o `api`, e os caminhos de recusa (sem motor, texto longo, CLI que falha, HTTP 401/5xx)** — porquê: higiene de PRs paralelos do upstream (teste novo em arquivo novo) e CI dele sem piper nem chave.
- **D10 — Parâmetro opcional `notice` em `send_voice_message`: texto enviado como mensagem comum logo antes do áudio, mas só depois que a fala foi gerada; se a geração falhar, nada sai; se o envio do `notice` falhar, o áudio não é enviado e a tool devolve o motivo** — porquê: no teste real de 2026-10-06 o texto chegava ~7 s antes do áudio (tempo de gerar a fala), e com voz clonada seria mais; gerar primeiro e mandar os dois colados resolve, e ainda evita texto avisando de um áudio que nunca chegou. Decidido pelo Luís em 2026-10-06.

## Avaliado e descartado
- **Aviso em texto depois do áudio** (em vez do `notice`): não muda código, mas inverte a regra de texto antes do anexo da message-standards; descartado pelo Luís em 2026-10-06.
- **sherpa-onnx como dependência Python (o que o Sabia usa no `narrar.py`)**: funciona e foi medido no Sabia, mas acrescenta pacote nativo pesado ao `pyproject` de um projeto que acabou de remover dependências, e foge do padrão de motor local por caminho que o upstream já tem.
- **Importar o `narrar.py` do Sabia**: é dependência de outro projeto (privado), feito para narrar relatório `.md`, não recado curto.
- **VoiceStudio**: descartado já na colheita de 2026-09-08 (AGPL-3.0, sidecar pesado); o TTS local ficou com piper.
- **Parâmetro `text` dentro do `send_audio_message`**: mudaria o contrato de uma tool publicada; tool nova é aditiva.

## Fora de escopo
- O anúncio falado da regra 1b da message-standards ("Mensagem gerada pelo assistente do Luís." no início + a mesma linha em texto antes do áudio): política pessoal do Luís, montada pela skill dele antes de chamar a tool.
- Clonagem de voz: não entra (regra 1b: voz genérica por padrão).
- Migrar o Sabia para usar esta tool.
- Comando `/whatsapp:*` ou ajuste no `install.sh`/`install.ps1` para baixar piper e voz: só documentação neste PR.

## Varredura
docs/rainforest/varredura/2026-10-06-send-voice-message-tts.txt — nenhuma Issue, PR ou branch de TTS no fork; no upstream também nenhuma Issue (`gh issue list -R rodrigopg/whatsapp-mcp --search "tts OR voice OR speech"` vazio). Casaram ideias: `whatsapp-mensagem-de-voz-em-nome-do-luis` (este é o envio que ela pedia; o `ao_colher` mandava medir antes se vale — o pedido do Rodrigo responde isso) e `pilha-de-voz-local-voicestudio` (colhida; daí veio o descarte do VoiceStudio e a política 1b).

## Em aberto
- (vazio)
