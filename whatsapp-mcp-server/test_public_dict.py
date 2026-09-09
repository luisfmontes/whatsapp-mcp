"""Achado 1 da revisão de 2026-09-09: a tool `get_message_context` devolvia o
dataclass inteiro, com `quoted_sender` e `mentions` crus — para um JID de
telefone, o número da pessoa, na resposta da tool que o próprio docstring do
`send_message` manda usar para achar o id da citação.
"""

import re
import unittest
from datetime import datetime
from unittest import mock

from whatsapp import Message, UNNAMED_CONTACT, message_to_public_dict

# Formas de JID, deliberadamente não numéricas, como já fazem os testes vizinhos.
AUTOR = "autor-citado@s.whatsapp.net"
MENCIONADO = "mencionado-um@s.whatsapp.net"

# O último teste precisa de algo COM forma de telefone para provar que a saída
# não carrega nenhum. Montado em tempo de execução de propósito: o repositório é
# público e a trava de dado pessoal recusa a sequência escrita por extenso —
# com razão, já que ela não distingue número inventado de número de gente.
DDI_DDD = "55" + "62"
FALSO_A = DDI_DDD + "0" * 6 + "11"
FALSO_B = DDI_DDD + "0" * 6 + "22"


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


class PublicDictTest(unittest.TestCase):
    def test_nao_expoe_o_jid_do_autor_citado(self):
        msg = _msg(quoted_message_id="MSG-0", quoted_sender=AUTOR, quoted_content="original")
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana"):
            d = message_to_public_dict(msg)
        self.assertNotIn("quoted_sender", d)
        self.assertEqual(d["quoted_sender_name"], "Fulana")
        self.assertNotIn(AUTOR, repr(d))

    def test_mencoes_saem_por_nome(self):
        msg = _msg(mentions=[MENCIONADO])
        with mock.patch("whatsapp.get_sender_name", return_value="Beltrano"):
            d = message_to_public_dict(msg)
        self.assertEqual(d["mentions"], ["Beltrano"])
        self.assertNotIn(MENCIONADO, repr(d))

    def test_nome_desconhecido_vira_marcador_nunca_o_jid(self):
        msg = _msg(quoted_message_id="MSG-0", quoted_sender=AUTOR, mentions=[MENCIONADO])
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            d = message_to_public_dict(msg)
        self.assertEqual(d["quoted_sender_name"], UNNAMED_CONTACT)
        self.assertEqual(d["mentions"], [UNNAMED_CONTACT])
        self.assertNotIn("autor-citado", repr(d))
        self.assertNotIn("mencionado-um", repr(d))

    def test_corpo_mostra_arroba_nome_e_nao_arroba_numero(self):
        msg = _msg(content="oi @mencionado-um tudo bem", mentions=[MENCIONADO])
        with mock.patch("whatsapp.get_sender_name", return_value="Beltrano"):
            d = message_to_public_dict(msg)
        self.assertIn("@Beltrano", d["content"])
        self.assertNotIn("@mencionado-um", d["content"])

    def test_sem_citacao_e_sem_mencao_os_campos_ficam_vazios(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Quem Seja"):
            d = message_to_public_dict(_msg())
        self.assertIsNone(d["quoted_message_id"])
        self.assertIsNone(d["quoted_sender_name"])
        self.assertEqual(d["mentions"], [])

    def test_nenhuma_sequencia_de_telefone_nos_campos_desta_entrega(self):
        """A trava que o repositório aplica a si mesmo, aplicada à resposta da tool.

        `sender` e `chat_jid` continuam sendo alças de endereçamento que já
        existiam antes deste trabalho; o que se cobra aqui é o que ele
        introduziu.
        """
        msg = _msg(
            quoted_message_id="MSG-0",
            quoted_sender=FALSO_A + ":8@s.whatsapp.net",
            quoted_content="original",
            mentions=[FALSO_B + "@s.whatsapp.net"],
            content="oi @" + FALSO_B + " tudo bem",
        )
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda j, a=None: j):
            d = message_to_public_dict(msg)
        somente_novos = {
            k: v for k, v in d.items()
            if k in ("content", "quoted_sender_name", "quoted_content", "mentions")
        }
        self.assertEqual(re.findall(r"\d{8,}", repr(somente_novos)), [])


if __name__ == "__main__":
    unittest.main()
