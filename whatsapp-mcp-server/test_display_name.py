"""Nome na superfície de leitura: nunca o número (D3).

Achado em produção em 2026-09-08: o `quoted_sender` chega do fio endereçado a
um dispositivo (`<user>:8@s.whatsapp.net`), a tabela `senders` é indexada sem
esse sufixo, a busca falhava e o código antigo caía no JID cru — imprimindo o
telefone na saída que a D3 diz que nunca carrega um.
"""

import unittest
from unittest import mock

from whatsapp import UNNAMED_CONTACT, _display_name, _strip_device_suffix


class StripDeviceSuffixTest(unittest.TestCase):
    def test_remove_o_sufixo_de_dispositivo(self):
        self.assertEqual(
            _strip_device_suffix("usuario-teste:8@s.whatsapp.net"),
            "usuario-teste@s.whatsapp.net",
        )

    def test_jid_sem_dispositivo_passa_intacto(self):
        self.assertEqual(
            _strip_device_suffix("usuario-teste@s.whatsapp.net"),
            "usuario-teste@s.whatsapp.net",
        )

    def test_lid_tambem(self):
        self.assertEqual(_strip_device_suffix("abc:26@lid"), "abc@lid")

    def test_none_e_vazio_sobrevivem(self):
        self.assertIsNone(_strip_device_suffix(None))
        self.assertEqual(_strip_device_suffix(""), "")

    def test_string_sem_arroba_passa_intacta(self):
        self.assertEqual(_strip_device_suffix("sem-arroba"), "sem-arroba")


class DisplayNameTest(unittest.TestCase):
    def test_busca_pelo_jid_normalizado(self):
        with mock.patch("whatsapp.get_sender_name", return_value="Fulana") as g:
            self.assertEqual(_display_name("usuario-teste:8@s.whatsapp.net"), "Fulana")
        # É o JID SEM dispositivo que vai para a busca — era isso que faltava.
        g.assert_called_once_with("usuario-teste@s.whatsapp.net", None)

    def test_nome_nao_resolvido_vira_marcador_e_nunca_o_jid(self):
        # get_sender_name devolve o próprio JID quando não acha nome.
        with mock.patch(
            "whatsapp.get_sender_name",
            side_effect=lambda jid, account=None: jid,
        ):
            saida = _display_name("usuario-teste:8@s.whatsapp.net")
        self.assertEqual(saida, UNNAMED_CONTACT)
        self.assertNotIn("usuario-teste", saida)

    def test_excecao_vira_marcador(self):
        with mock.patch("whatsapp.get_sender_name", side_effect=RuntimeError("boom")):
            self.assertEqual(_display_name("usuario-teste@s.whatsapp.net"), UNNAMED_CONTACT)

    def test_jid_vazio_vira_marcador(self):
        self.assertEqual(_display_name(None), UNNAMED_CONTACT)
        self.assertEqual(_display_name(""), UNNAMED_CONTACT)


if __name__ == "__main__":
    unittest.main()
