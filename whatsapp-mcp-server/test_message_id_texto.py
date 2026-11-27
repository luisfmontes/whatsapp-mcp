"""Mensagem de texto na prosa de leitura também mostra o `Message ID`.

Antes, só a linha de mídia trazia o id. Quem lia uma conversa por
`list_messages` não tinha o id da mensagem de texto e, por isso, não conseguia
chamar `mark_chat_as_read`, `react_to_message`, `edit_message` nem responder
citando. O id é opaco (não é telefone nem JID), mesmo argumento da linha de
mídia em `LinhaDeMidiaSemEnderecoTest`.
"""

import re
import unittest
from datetime import datetime
from unittest import mock

import whatsapp
from whatsapp import Message


def _msg(**kw):
    base = dict(
        timestamp=datetime(2026, 11, 27, 10, 0, 0),
        sender="remetente@s.whatsapp.net",
        content="bom dia, preciso de ajuda",
        is_from_me=False,
        chat_jid="contato-teste@s.whatsapp.net",
        id="MSG-TEXTO-A",
        chat_name="Contato",
        media_type=None,
    )
    base.update(kw)
    return Message(**base)


class MessageIdNaLinhaDeTextoTest(unittest.TestCase):
    def _formatar(self, msg):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            return whatsapp.format_message(msg)

    def test_texto_recebido_traz_o_id(self):
        saida = self._formatar(_msg())
        self.assertIn("[Message ID: MSG-TEXTO-A] bom dia, preciso de ajuda", saida)

    def test_texto_enviado_por_mim_traz_o_id(self):
        # edit_message so serve para mensagem minha: o id tambem tem de vir.
        saida = self._formatar(_msg(is_from_me=True, id="MSG-TEXTO-B"))
        self.assertIn("From: Me: [Message ID: MSG-TEXTO-B]", saida)

    def test_linha_de_midia_continua_igual(self):
        saida = self._formatar(_msg(media_type="image", id="MSG-IMG-C"))
        self.assertIn("[image - Message ID: MSG-IMG-C] ", saida)
        self.assertEqual(saida.count("Message ID"), 1)

    def test_sem_id_nao_imprime_none(self):
        saida = self._formatar(_msg(id=None))
        self.assertNotIn("Message ID", saida)
        self.assertNotIn("None", saida)

    def test_linha_de_texto_nao_imprime_endereco(self):
        # O id entra, o chat_jid nao (D3).
        saida = self._formatar(_msg())
        self.assertNotIn("contato-teste@s.whatsapp.net", saida)
        self.assertEqual(re.findall(r"\d{8,}", saida), [])


if __name__ == "__main__":
    unittest.main()
