# Plano: send_voice_message: texto vira mensagem de voz (upstream)

Design: docs/rainforest/design/2026-10-06-send-voice-message-tts.md

Base do trabalho: branch `up/send-voice-message` a partir de `upstream/main` (`79a88ff85cc380df85e5e3f860aca6ace011a184`, que já traz o modo somente leitura do #25). O upstream é conta única: as funções dele não têm `account`, então a tool nasce lá como `send_voice_message(recipient, text)`; o `account` da D2 entra só no port para o fork, depois do merge.

## O que não pode quebrar
- `send_audio_message` (tool e `whatsapp.send_audio_message`) com o mesmo contrato e comportamento.
- Com `MCP_READONLY=true` a tool nova não é exposta: ela entra com `@write_tool`, como toda tool que envia.
- Sem `TTS_ENGINE` configurado, nada é gerado nem enviado, e a tool diz por quê ("not configured").
- `pyproject.toml` e `uv.lock` sem dependência nova.
- Suíte do upstream (`uv run python -m unittest discover -p 'test_*.py'` em `whatsapp-mcp-server`) verde; nenhum teste toca rede, piper ou chave real.
- Nenhum teste lê o texto do fonte: os alvos de mutação abaixo ditam a linha, mas os testes exercitam comportamento.

## Tarefas

### 1. Módulo `tts.py`: motores local e api, limite de texto e conversão [tipo: implementar]
atende: D3, D4, D6, D7, D8, D9
arquivos: `whatsapp-mcp-server/tts.py`, `whatsapp-mcp-server/test_tts.py`
depende de: nenhuma
paralela: sim
mutacao:
  arquivo: `whatsapp-mcp-server/tts.py`
  de: `if len(text) > MAX_TTS_CHARS:`
  para: `if False:`
  bateria: `cd whatsapp-mcp-server && python -m unittest test_tts -v`
  fixture: test_tts.py, caso `test_rejects_text_over_limit`
prova: `cd whatsapp-mcp-server && python -m unittest test_tts -v`
pronto quando: com `TTS_ENGINE=local` e `TTS_CLI` apontando para um CLI falso que lê o texto por stdin e grava um WAV válido em `--output_file`, `tts.synthesize("Olá, teste")` devolve o caminho de um `.ogg` que o `ffprobe` identifica como `codec_name=opus`; com `TTS_ENGINE=api` contra um servidor HTTP falso em `127.0.0.1`, o POST chega em `/audio/speech` com `model`, `voice` e `input` no corpo e o cabeçalho de autorização carregando o valor de `TTS_API_KEY`, e o resultado também vira `.ogg` opus; sem `TTS_ENGINE`, `engine_ready()` devolve `(False, <motivo com "not configured">)`; texto com 4097 caracteres é recusado com erro que cita o limite 4096, sem chamar o motor; CLI que sai com código diferente de zero e HTTP 401/500 viram erro com o motivo, sem arquivo de saída sobrando — provado por `cd whatsapp-mcp-server && python -m unittest test_tts -v` com todos os casos `ok` e zero `skipped` (o teste monta o CLI falso e o servidor; os casos que convertem exigem ffmpeg, presente nesta máquina; se o CI do upstream não tiver ffmpeg, a tarefa reporta isso em vez de pular em silêncio)

### 2. Tool `send_voice_message` e helper no `whatsapp.py` [tipo: implementar]
atende: D1, D2, D6, D7, D9
arquivos: `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/test_tts.py`, `tests/e2e/e2e.py`
depende de: 1
paralela: nao
mutacao:
  arquivo: `whatsapp-mcp-server/whatsapp.py`
  de: `    if not ready:`
  para: `    if False:`
  bateria: `cd whatsapp-mcp-server && python -m unittest test_tts -v`
  fixture: test_tts.py, caso `test_send_voice_not_configured_sends_nothing`
prova: `cd whatsapp-mcp-server && python -m unittest test_tts.TestSendVoiceMessage -v`
pronto quando: com o MCP chamando `send_voice_message(recipient=<numero sintetico>, text="Olá")` e o bridge substituído por um servidor HTTP falso, (a) sem `TTS_ENGINE` a tool devolve `success` falso com mensagem contendo "not configured", e o servidor falso recebe zero requisições; (b) com o motor local falso, o servidor falso recebe exatamente um POST em `/api/send` cujo `media_path` aponta para um `.ogg` existente no momento do envio, e o temporário é apagado depois; (c) texto de 4097 caracteres devolve `success` falso sem requisição ao bridge — provado por `cd whatsapp-mcp-server && python -m unittest test_tts -v` com os três casos `ok`; e `send_voice_message` aparece em `MCP_TOOLS` do `tests/e2e/e2e.py` (uma linha, ordem alfabética, vírgula no fim, pela higiene do upstream)

### 3. README: configuração do TTS, voz sugerida e licença [tipo: docs]
atende: D5, D8
arquivos: `README.md`
depende de: 2
paralela: nao
mutacao: n/a
  motivo: documentação, sem comportamento a inverter; a falsificação é casar nomes e padrões com o código entregue
pronto quando: com o `tts.py` entregue na tarefa 1, cada variável que o README documenta (`TTS_ENGINE`, `TTS_CLI`, `TTS_MODEL`, `TTS_API_KEY`, `TTS_API_BASE`, `TTS_API_MODEL`, `TTS_API_VOICE`) existe em `tts.py` com o mesmo padrão declarado no README (`gpt-4o-mini-tts`, `alloy`, `https://api.openai.com/v1`), o limite citado é 4096 (o mesmo `MAX_TTS_CHARS`), a voz pt_BR sugerida é uma das quatro do `rhasspy/piper-voices` (faber, cadu, jeff, edresson) com o aviso de que são finetune da `lessac` (licença Blizzard 2013) e de que o modelo não vem no repo, e a tool entra como linha única na tabela de tools — provado por `cd whatsapp-mcp-server && python -c "import tts,io;r=io.open('../README.md',encoding='utf-8').read();src=io.open('tts.py',encoding='utf-8').read();vs=['TTS_ENGINE','TTS_CLI','TTS_MODEL','TTS_API_KEY','TTS_API_BASE','TTS_API_MODEL','TTS_API_VOICE'];assert all(v in r and v in src for v in vs);assert tts.MAX_TTS_CHARS==4096 and '4096' in r;assert 'lessac' in r;print('ok')"` devolvendo `ok`, e lendo a seção para conferir que o texto descreve o que o código faz (motor ausente = "not configured", nada é enviado)

## Emenda 1 (2026-10-06, após revisão reprovada com 3 achados)

A revisão 1 achou: (1) os testes de conversão quebram no CI do upstream, porque o `ubuntu-latest` não traz ffmpeg; (2) surrogate solto no texto estoura `UnicodeEncodeError` fora do contrato `(False, motivo)`; (3) o corpo de erro HTTP (`r.text[:200]`) pode devolver a chave mascarada. Decisão do Luís para (1): instalar ffmpeg no CI do upstream **e** pular com motivo explícito quando ffmpeg/ffprobe faltarem localmente.

### 4. CI do upstream instala ffmpeg; testes de conversão pulam com motivo quando ele falta [tipo: configurar]
atende: D9
arquivos: `.github/workflows/ci.yml`, `whatsapp-mcp-server/test_tts.py`
depende de: 3
paralela: nao
mutacao: n/a
  motivo: configuração de CI; a falsificação é o job python do PR rodar os testes de conversão (não pulados) e o mesmo teste pular, com motivo, num PATH sem ffmpeg
pronto quando: com o PR aberto no `rodrigopg/whatsapp-mcp`, o job `python` do CI fica verde e o log do `unittest -v` mostra os casos de conversão de `test_tts` como `ok` (não `skipped`) — provado por `gh run view <run> -R rodrigopg/whatsapp-mcp --log` filtrado por `test_tts`; e localmente, com o PATH sem ffmpeg, `cd whatsapp-mcp-server && python -m unittest test_tts -v` sai 0 e lista esses casos como `skipped` com motivo citando ffmpeg

### 5. Erro de codificação e erro de autenticação voltam como `(False, motivo)` sem eco do corpo [tipo: implementar]
atende: D6, D8
arquivos: `whatsapp-mcp-server/tts.py`, `whatsapp-mcp-server/test_tts.py`
depende de: 4
paralela: nao
mutacao:
  arquivo: `whatsapp-mcp-server/tts.py`
  de: `if r.status_code in (401, 403):`
  para: `if False:`
  bateria: `cd whatsapp-mcp-server && python -m unittest test_tts -v`
  fixture: test_tts.py, caso `test_api_auth_error_does_not_echo_body`
prova: `cd whatsapp-mcp-server && python -m unittest test_tts.TestRobustness -v`
pronto quando: com o servidor HTTP falso respondendo 401 com corpo `Incorrect API key provided: sk-...abcd`, `whatsapp.send_voice_message` devolve `success` falso com mensagem que cita 401 e `TTS_API_KEY` e **não** contém `sk-`; com o texto `"oi \ud83d"` (surrogate solto), a tool devolve `success` falso com motivo, sem exceção e sem requisição ao bridge, e sem temporário sobrando — provado por `cd whatsapp-mcp-server && python -m unittest test_tts.TestRobustness -v` com os dois casos `ok`

## Emenda 2 (2026-10-06, após teste real: texto chegava antes do áudio)

### 6. Parâmetro `notice`: texto colado ao áudio, enviado só depois da fala gerada [tipo: implementar]
atende: D10
arquivos: `whatsapp-mcp-server/whatsapp.py`, `whatsapp-mcp-server/main.py`, `whatsapp-mcp-server/test_tts.py`, `README.md`
depende de: 5
paralela: nao
mutacao:
  arquivo: `whatsapp-mcp-server/whatsapp.py`
  de: `    if notice:`
  para: `    if False:`
  bateria: `cd whatsapp-mcp-server && python -m unittest test_tts -v`
  fixture: test_tts.py, caso `test_notice_is_sent_after_synthesis_right_before_audio`
prova: `cd whatsapp-mcp-server && python -m unittest test_tts.TestNotice -v`
pronto quando: com o bridge falso gravando a ordem das requisições e o motor local falso, (a) `send_voice_message(recipient, "Olá", notice="aviso")` gera exatamente duas requisições ao bridge, na ordem `/api/send` com `message == "aviso"` e sem `media_path`, depois `/api/send` com `media_path` `.ogg`, e o CLI falso já tinha terminado quando a primeira chegou; (b) com o motor falhando e `notice` preenchido, o bridge recebe zero requisições; (c) com o bridge recusando o texto do `notice`, o áudio não é enviado e a tool devolve `success` falso com o motivo; (d) sem `notice` o comportamento das tarefas 1-5 não muda — provado por `cd whatsapp-mcp-server && python -m unittest test_tts -v` com todos os casos `ok`; e o README documenta `notice` na seção da tool
