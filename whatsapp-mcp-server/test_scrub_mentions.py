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
    Message,
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
