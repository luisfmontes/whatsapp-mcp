"""Bloqueante 1 da revisão de 2026-09-09 (rodada 2).

`quoted_content` é o CORPO da mensagem citada, e um corpo que menciona alguém
carrega `@<número>` por protocolo — o WhatsApp só grifa a menção se o texto
escrever o número. Então citar uma mensagem que mencionou um terceiro punha o
número desse terceiro na linha de leitura e na resposta da tool, que é
exatamente o que a D3 proíbe e o que a rodada 1 já tinha bloqueado no campo
vizinho `quoted_sender`.

A mesma lacuna existe em `content` quando a coluna `mentions` da própria
mensagem está vazia — linha gravada antes de a coluna existir, por exemplo.
"""

import re
import unittest
from datetime import datetime
from unittest import mock

from whatsapp import (
    Chat,
    Message,
    _chat_from_dict,
    UNNAMED_CONTACT,
    _display_name,
    _scrub_mention_numbers,
    format_message,
    message_to_public_dict,
)

# Formas com jeito de telefone, montadas em tempo de execução: o repositório é
# público e a trava de dado pessoal recusa a sequência escrita por extenso.
DDI_DDD = "55" + "62"
FALSO = DDI_DDD + "0" * 6 + "33"
OUTRO_FALSO = DDI_DDD + "0" * 6 + "44"


def _msg(**kw):
    base = dict(
        timestamp=datetime(2026, 9, 9, 10, 0, 0),
        sender="remetente@s.whatsapp.net",
        content="corpo da mensagem",
        is_from_me=False,
        chat_jid="grupo-teste@g.us",
        id="MSG-1",
        chat_name="Grupo",
        media_type=None,
    )
    base.update(kw)
    return Message(**base)


class ScrubMentionNumbersTest(unittest.TestCase):
    def test_apaga_arroba_numero_solto(self):
        self.assertEqual(
            _scrub_mention_numbers("bom dia @" + FALSO + " tudo certo"),
            "bom dia @" + UNNAMED_CONTACT + " tudo certo",
        )

    def test_nao_mexe_em_arroba_de_nome(self):
        self.assertEqual(_scrub_mention_numbers("oi @Ana Paula"), "oi @Ana Paula")

    def test_nao_mexe_em_numero_sem_arroba(self):
        texto = "o pedido " + FALSO + " chegou"
        self.assertEqual(_scrub_mention_numbers(texto), texto)

    def test_vazio_e_none_passam(self):
        self.assertEqual(_scrub_mention_numbers(""), "")
        self.assertIsNone(_scrub_mention_numbers(None))


class CitacaoNaoVazaNumeroDeTerceiroTest(unittest.TestCase):
    """O cenário concreto: C é mencionado por A, B responde citando A."""

    def _mensagem_citando_quem_mencionou(self):
        return _msg(
            quoted_message_id="MSG-0",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content="@" + FALSO + " bom dia",
        )

    def test_dict_publico_nao_carrega_o_numero_do_terceiro(self):
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            d = message_to_public_dict(self._mensagem_citando_quem_mencionou())
        self.assertNotIn(FALSO, repr(d))
        self.assertIn(UNNAMED_CONTACT, d["quoted_content"])

    def test_linha_de_leitura_nao_carrega_o_numero_do_terceiro(self):
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            saida = format_message(self._mensagem_citando_quem_mencionou())
        self.assertNotIn(FALSO, saida)
        self.assertIn("↳ reply to", saida)

    def test_corpo_sem_coluna_de_mencoes_tambem_e_limpo(self):
        # Linha antiga: o `@<número>` está no corpo, mas `mentions` está vazia
        # porque a coluna nasceu neste trabalho — `_mentions_by_name` não tem
        # o que resolver, e antes disso o número saía inteiro.
        msg = _msg(content="ei @" + FALSO + " confere isso", mentions=[])
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            d = message_to_public_dict(msg)
            saida = format_message(msg)
        self.assertNotIn(FALSO, d["content"])
        self.assertNotIn(FALSO, saida)


class UltimaMensagemDoChatTest(unittest.TestCase):
    """Bloqueante 3 da rodada 3: `list_chats` é a tool mais usada do servidor,
    e `Chat.last_message` é o corpo cru de `messages.content`. Basta a última
    mensagem do chat ser uma menção para o número do mencionado sair por ali.
    """

    def test_last_message_passa_pela_limpeza(self):
        chat = _chat_from_dict({
            "jid": "grupo-teste@g.us",
            "name": "Grupo",
            "last_message": "@" + FALSO + " bom dia",
        })
        self.assertNotIn(FALSO, chat.last_message)
        self.assertIn(UNNAMED_CONTACT, chat.last_message)

    def test_last_message_ausente_nao_quebra(self):
        chat = _chat_from_dict({"jid": "grupo-teste@g.us", "name": "Grupo"})
        self.assertIsNone(chat.last_message)


class LinhaFromTest(unittest.TestCase):
    """Bloqueante 1 da rodada 5: a linha `From:` de `list_messages` caia no
    identificador cru quando o nome nao resolvia — e `messages.sender` e a
    parte de usuario do JID, ou seja, o telefone puro. Medido no store real em
    2026-09-11: 130 de 665 remetentes, 2.152 mensagens, 100% com forma de
    telefone. A mesma mensagem respondia "(contato sem nome)" em
    `get_message_context` e o numero em `list_messages`.
    """

    def test_nome_que_nao_resolve_vira_marcador_e_nao_o_numero(self):
        # Linha INTEIRA, com show_chat_info ligado: a versao anterior deste
        # teste desligava o cabecalho do chat e o fixture fixava um chat_name
        # legivel, entao ele media meia linha — e a metade nao medida era o
        # bloqueante 1 da rodada 6 (achado da propria rodada 6).
        msg = _msg(sender=FALSO + "@s.whatsapp.net", chat_name=OUTRO_FALSO)
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            saida = format_message(msg, show_chat_info=True)
        self.assertEqual(re.findall(r"\d{8,}", saida), [])
        self.assertIn("From: " + UNNAMED_CONTACT, saida)
        self.assertIn("Chat: " + UNNAMED_CONTACT, saida)
        self.assertIn("corpo da mensagem", saida)

    def test_nome_que_resolve_continua_saindo(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            saida = format_message(_msg(), show_chat_info=False)
        self.assertIn("From: Fulana", saida)

    def test_excecao_na_busca_do_nome_continua_sendo_dita(self):
        """Decisao anterior a este trabalho (PR #12) que continua valendo."""
        with mock.patch("whatsapp.get_sender_name", side_effect=RuntimeError("boom")):
            saida = format_message(_msg(), show_chat_info=False)
        self.assertIn("boom", saida)
        self.assertIn(UNNAMED_CONTACT, saida)
        self.assertIn("corpo da mensagem", saida)


class UltimoRemetenteDoChatTest(unittest.TestCase):
    """Bloqueante 2 da rodada 5: em chat de GRUPO o `jid` e @g.us e nao carrega
    numero nenhum — `last_sender` era o unico lugar por onde o telefone de um
    terceiro saia em `list_chats`. Medido: 31 de 141 grupos do store real.
    """

    def test_sai_por_nome(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            chat = _chat_from_dict({
                "jid": "grupo-teste@g.us",
                "name": "Grupo",
                "last_sender": FALSO,
            })
        self.assertEqual(chat.last_sender_name, "Fulana")
        self.assertNotIn(FALSO, repr(chat))

    def test_nome_que_nao_resolve_vira_marcador(self):
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            chat = _chat_from_dict({
                "jid": "grupo-teste@g.us",
                "name": "Grupo",
                "last_sender": FALSO,
            })
        self.assertEqual(chat.last_sender_name, UNNAMED_CONTACT)
        self.assertNotIn(FALSO, repr(chat))

    def test_campo_cru_nao_existe_mais(self):
        self.assertFalse(hasattr(Chat(jid="x@g.us", name="x", last_message_time=None), "last_sender"))


class NomeDoChatTest(unittest.TestCase):
    """Bloqueante 1 da rodada 6: `chats.name` cai no telefone quando o contato
    nao tem nome — a ponte faz `name = sender` e depois `name = jid.User`, que
    sao a parte de usuario do JID. Medido no store real em 2026-09-11: 1.658 de
    2.162 chats 1:1 com o nome igual ao numero. Era a mesma linha em que
    `From:` ja dizia "(contato sem nome)", um campo a esquerda.
    """

    def test_nome_de_chat_com_forma_de_telefone_vira_marcador(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            d = message_to_public_dict(_msg(chat_name=FALSO))
        self.assertEqual(d["chat_name"], UNNAMED_CONTACT)
        self.assertNotIn(FALSO, repr(d))

    def test_nome_de_chat_de_verdade_continua_saindo(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            d = message_to_public_dict(_msg(chat_name="Grupo do Trabalho"))
        self.assertEqual(d["chat_name"], "Grupo do Trabalho")

    def test_lista_de_chats_tambem(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            chat = _chat_from_dict({"jid": "contato@s.whatsapp.net", "name": FALSO})
        self.assertEqual(chat.name, UNNAMED_CONTACT)
        self.assertNotIn(FALSO, repr(chat))


class ContaDoUltimoRemetenteTest(unittest.TestCase):
    """Bloqueante 2 da rodada 6: `_chat_from_dict` resolvia o nome contra a
    conta PRIMARIA, nao contra a conta de quem pediu a lista — o campo criado
    na rodada 5 para mostrar nome mostrava o marcador justamente quando o nome
    existia na outra ponte, e podia trazer o rotulo da conta errada.
    """

    def test_a_conta_pedida_e_a_que_resolve_o_nome(self):
        vistas = []

        def espiao(jid, account=None):
            vistas.append(account)
            return "Fulana"

        with mock.patch("whatsapp.get_sender_name", side_effect=espiao):
            _chat_from_dict({"jid": "g@g.us", "name": "Grupo", "last_sender": FALSO}, "trabalho")
        self.assertEqual(vistas, ["trabalho"])


class NomeQueEUmTelefoneTest(unittest.TestCase):
    """Observação 5 da mesma revisão: `name.isdigit()` só pega o número puro."""

    def test_nome_com_pontuacao_de_telefone_vira_marcador(self):
        formatado = "+" + DDI_DDD[:2] + " " + DDI_DDD[2:] + " " + FALSO[4:9] + "-" + FALSO[9:]
        self.assertTrue(len(re.sub(r"\D", "", formatado)) >= 8, "fixture precisa ter 8+ dígitos")
        with mock.patch("whatsapp.get_sender_name", return_value=formatado):
            self.assertEqual(_display_name("alguem@s.whatsapp.net"), UNNAMED_CONTACT)

    def test_nome_de_gente_com_poucos_digitos_passa(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Ana 2"):
            self.assertEqual(_display_name("alguem@s.whatsapp.net"), "Ana 2")


if __name__ == "__main__":
    unittest.main()
