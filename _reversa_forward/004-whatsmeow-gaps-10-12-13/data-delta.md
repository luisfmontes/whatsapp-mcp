# Data delta — ciclo 004

**Nenhuma mudança de dados.**

| Aspecto | Situação |
|---------|----------|
| Tabelas novas | nenhuma |
| Colunas novas | nenhuma |
| Índices novos | nenhum |
| Migração | não se aplica |
| Escrita em `messages.db` | nenhuma (RN-13) |
| Estado persistido | nenhum — os 7 endpoints são request/response puros contra o WhatsApp |

Efeitos colaterais fora do banco, que existem e são intencionais:

| Endpoint | Efeito externo real |
|----------|---------------------|
| `/api/group_invite_link` com `reset=true` | **Revoga** o link de convite anterior no WhatsApp (RN-02) |
| `/api/join_group_with_link` | Entra no grupo (ou cria pedido de entrada) — visível para os participantes |
| `/api/group_settings` | Altera nome/tópico/announce/locked do grupo para todos os membros |
| `/api/group_photo` | Altera ou remove a foto do grupo para todos os membros |
| `/api/group_photo` (leitura) | Lê um arquivo **do host da bridge** — o caminho não vem do cliente MCP por acaso, é o mesmo modelo do `send_file` |
