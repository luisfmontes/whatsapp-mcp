# Regression watch — ciclo 004

Comportamento **observado no smoke real** (bridge conectada, conta do dono, grupo descartável
`smoke 004`, criado e abandonado ao fim). Não é inferência a partir da doc da lib.

## 1. `found` do `user_info` não significa "tem WhatsApp" 🔴→🟢 resolvido em doc

| Chamada | Resultado |
|---------|-----------|
| `/api/user_info` com `5562999999999` (número inexistente) | `found: true`, `status: ""`, `devices: []` |
| `/api/is_on_whatsapp` com o mesmo número | `is_in: false` |

O WhatsApp devolve um nó `user` mesmo para número não registrado. `found` reflete "veio nó na
resposta", não "existe conta". Era a lacuna 🔴 da seção 8 do `requirements.md`.

**Mitigação:** docstring de `get_user_info` declara isso e manda usar `check_whatsapp` para
registro. **Não** foi alterado o significado de `found` — mudar para "found = tem devices" seria
inventar semântica que a lib não dá.

**Se regredir:** alguém "corrigir" `found` para depender de `devices`/`status` não-vazios quebra o
contrato de RN-10 (um resultado por entrada, correlacionado) sem avisar. O teste unitário não pega.

## 2. `GetGroupInfoFromLink` não traz locked/announce ⚠️ mudou o contrato

Setando `locked: true` e `announce: true` no grupo e relendo:

| Caminho de leitura | `is_locked` | `is_announce` |
|--------------------|-------------|---------------|
| `/api/group_invite_info` (invite query) | `false` | `false` |
| `/api/group_info` (`GetGroupInfo` completo) | **`true`** | **`true`** |

O nó `group` de uma resposta de convite não carrega os filhos `locked`/`announcement`, então
`parseGroupNode` deixa os dois em `false` independentemente do estado real. As escritas tinham
aplicado — quem estava errado era o caminho de leitura.

**Resolvido no ciclo:** os campos foram **removidos** de `GroupInviteInfoResponse` (campo sempre
falso é pior que campo ausente) e `/api/group_info` foi estendido com `topic`, `is_locked` e
`is_announce`, virando o jeito de reler o que `group_settings` escreveu.

**Se regredir:** alguém reintroduzir `is_locked`/`is_announce` no `invite_info` por simetria de API
reintroduz o campo mentiroso. O comentário no struct explica o porquê — não remover.

## 3. `set_group_photo` com `remove: true` devolve `picture_id: "remove"`

É o retorno literal de `SetGroupPhoto` do whatsmeow, não um bug nosso. Esquisito de ler, mas honesto.
Não foi mascarado.

## 4. Link revogado e link inválido são 500 com mensagem legível

| Entrada | Resposta |
|---------|----------|
| link antigo após `reset: true` | 500 — `"that group invite link has been revoked"` |
| código inventado | 500 — `"info query returned status 400: bad-request"` |
| grupo inexistente em `invite_link` | 500 — `"that group does not exist"` |

Segue a convenção 5 dos contratos (erro da lib → 500 com mensagem). Diferente do caso da foto, que
é 200 por ser resultado normal.

## 5. `group_settings` em grupo inexistente: 200 com falha por campo

`{"name": ..., "locked": true}` num JID de grupo bem formado mas inexistente devolveu **HTTP 200**,
`success: false`, `"0 of 2 setting(s) applied"`, e os **dois** campos com
`error: "info query returned status 404: item-not-found"`.

Confirma RN-04 em execução real: o erro no primeiro campo não interrompeu o segundo.

## Não coberto pelo smoke

- **Setter em grupo real onde o dono não é admin.** Deliberadamente não exercitado: rodar um setter
  contra grupo real arrisca alterá-lo de fato caso ele seja admin. O caminho de erro foi exercitado
  com grupo inexistente, que percorre o mesmo código (erro da lib → `results[].success:false`). A
  diferença fica na mensagem do WhatsApp (`item-not-found` vs. `forbidden`), não no tratamento.
- **`join_group_with_link` entrando num grupo de verdade.** Só foi testado com o link do próprio
  grupo do dono (já membro), que retornou 200 e o JID correto.
- **`SetGroupPhoto` com JPEG fora de proporção** (`ErrInvalidImageFormat`). Continua 🔴 da seção 8.
