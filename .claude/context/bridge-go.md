# Contexto: Bridge Go

Bridge Go (`whatsapp-bridge/main.go`) — conecta ao WhatsApp via whatsmeow, expõe REST API local, persiste em SQLite (`store/messages.db`).

## Pontos críticos

- **whatsmeow exige `context.Context`** em todas as chamadas de API (quebra que congelou o upstream). Versão pinada: `v0.0.0-20260529101937-a7ea56383ec4` (pseudo-version de commit — a lib não tem release estável). Go **1.25+** obrigatório (`go.mod`).

## Camada SQLite por plataforma (desde o port Windows)

- `sqlite_driver_cgo.go` (`//go:build !windows`) mantém `mattn/go-sqlite3` (CGO) — mac/Linux
  inalterados. `sqlite_driver_windows.go` (`//go:build windows`) usa `modernc.org/sqlite`, Go puro,
  para o Windows compilar com `CGO_ENABLED=0` sem MinGW. `main.go` chama `openMessagesDB()`,
  `openUnaccentMessagesDB()`, `openStoreDBReadOnly()` e `storeDSN()` em vez de `sql.Open` direto.
- **O driver tem que se chamar `"sqlite3"`**: `sqlstore.New(ctx, dialect, dsn)` usa o `dialect` como
  nome do driver **e** como chave da sintaxe de UPSERT. E no Windows o alias precisa apontar para a
  instância que o modernc registrou (obtida via `(*sql.DB).Driver()`): um `&sqlite.Driver{}` de
  zero-value conecta mas não carrega funções registradas, e toda query com `unaccent()` falha em
  runtime com `no such function: unaccent`.
- DSN não é portável entre os dois: `_foreign_keys=on`/`_busy_timeout=5000`/`mode=ro` do mattn
  correspondem a `_fk=on`/`_pragma=busy_timeout(5000)`/`_pragma=query_only(true)` no modernc.
- **Divergência conhecida em NULL:** no lado CGO o mattn recusa argumento que não seja TEXT/BLOB
  (`callbackArgString`), então `unaccent(NULL)` derruba a query; no Windows NULL propaga como NULL.
  Não dispara hoje porque o bridge grava string vazia, não NULL.
- `go build` no Windows **não compila** o arquivo `!windows`. Type-check cruzado antes de publicar:
  `GOOS=darwin go build ./...` e `GOOS=linux go build ./...` (não precisa de toolchain C).
- `busy_timeout` nas duas conexões de `messages.db` não é opcional: sem ele, buscas durante history
  sync falham com `database is locked (5) (SQLITE_BUSY)` como 500.
- **REST API liga em `127.0.0.1` por padrão.** Upstream ligava `0.0.0.0` (qualquer um na LAN mandava mensagem como você). `BIND_ADDR=<ip>` (ex. `0.0.0.0`, ou um IP específico como um endereço Tailscale) reabre.
- **Porta** via `WHATSAPP_BRIDGE_PORT` (default 8080; este setup usa 8081).
- **`API_AUTH_TOKEN`**: se `BIND_ADDR` não é `127.0.0.1`/`localhost`, o processo **recusa subir**
  sem essa env var setada (fail-closed, não fail-open). Quando setada, todo `/api/*` exige
  header `Authorization: Bearer <token>` (middleware `requireBearerToken`, aplicado só em
  `/api/*` — `/qr` e `/qr.png` ficam sempre abertos, é o próprio fluxo de pareamento).

## Endpoints REST de leitura (`/api/chats`, `/api/messages`, etc.)

- 9 endpoints somam-se aos de escrita já existentes: `/api/chats`, `/api/messages`,
  `/api/message_context`, `/api/contacts/search`, `/api/contacts/chats`,
  `/api/contacts/last_interaction`, `/api/chat`, `/api/chat/by_contact`, `/api/sender_name`.
  Contrato completo em `.reversa/_reversa_forward/mcp-remote-hardening/interfaces.md`.
- Motivo de existirem: o MCP Python lia esses dados via SQLite local direto; isso só funciona
  quando MCP e bridge rodam na mesma máquina. Bridge remota (VPS) exige que essas leituras
  também sejam REST — MCP server hoje é 100% stateless (ver `mcp-python.md`).
- `whatsapp.db` (store do whatsmeow) não expõe `*sql.DB` cru via `sqlstore.Container` — esses
  handlers abrem uma **segunda conexão SQLite read-only** própria só pra leituras de
  `whatsmeow_contacts`/`whatsmeow_lid_map`, separada da conexão de escrita gerenciada pela lib.
- Busca accent-insensitive (`unaccent`) migrou de Python pra um driver SQLite derivado
  (`sqlite3_unaccent`, registrado via `sql.Register`), usado só na conexão de leitura — não
  afeta a conexão de escrita principal.
- Timestamps de resposta usam offset explícito (`-03:00`), não `Z`/UTC — deliberado, pra
  compatibilizar com `datetime.fromisoformat` do Python <3.11.

## Captura de conteúdo

- `extractTextContent` retorna captions de image/video/document (`GetCaption()`). Bug original: caption era dropado na escrita → busca não achava texto que existia. Caminho live e caminho de history-sync compartilham a função (antes o sync tinha extractor inline duplicado com o mesmo bug).
- Mensagens enviadas são persistidas localmente na hora (echo multi-device não dispara em conta single-device).

## History sync

- `store.DeviceProps.RequireFullSync = proto.Bool(true)` + `HistorySyncConfig{FullSyncDaysLimit:365,...}` **antes de `NewClient`**. Sem isso só vem histórico recente. Recupera ~1 ano.
- **Armadilha:** `INSERT OR REPLACE INTO messages` (main.go:221) sobrescreve a linha inteira no sync. Re-parear/re-sync **apaga transcrições** (content volta a ''). COALESCE em messages ainda não existe (só em senders/chats). Backup `messages.db` antes de re-parear.

## LID→PN

- `resolveToPN` normaliza LID→PN na escrita (mesmo contato não racha em dois `chat_jid`).
- `migrateLIDChats` roda no startup, mescla rows `@lid` legados nos `@s.whatsapp.net` (transacional, idempotente).

## Mídia + segurança

- **`safeMediaPath`** (main.go ~889): rejeita componentes crus com `/`, `\`, `..`, `.`, vazio (não sanitiza — surface o ataque). IDs reais do WhatsApp são hex/base64 sem separadores.
- Path de mídia prefixado com message ID: filename derivado do sync-time colidia (571 áudios → 87 paths únicos), cache devolvia bytes errados.
- **Media retry** (recuperação de mídia expirada): `SendMediaRetryReceipt` → `events.MediaRetry` → `DecryptMediaRetryNotification` → `DownloadMediaWithPath`. History sync NÃO recupera (re-entrega as mesmas URLs mortas do CDN). Endpoint `/api/mediaretry`. Log contract estável: `MEDIA RETRY <id>: SUCCESS|NOTONPHONE|ERROR`.

## Testes

- `main_test.go` — `TestSafeMediaPath` (traversal + colisão de filename). `go test ./...`.
