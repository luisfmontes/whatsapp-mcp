"""Issue #21: quando a ponte recusa o download porque o remetente apagou a
mensagem para todos, a tool não pode sugerir procurar o arquivo na pasta de
downloads — seria mandar o agente atrás do que o remetente retirou.
"""

import unittest
from unittest import mock

import main

RECUSA_REVOKE = ('HTTP 500 - {"success":false,"message":"Failed to download media: '
                 'message was deleted by the sender"}\n')


class TestDownloadRevoked(unittest.TestCase):
    def test_apagada_nao_sugere_procurar_o_arquivo(self):
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, RECUSA_REVOKE)):
            out = main.download_media("MSG-1", "grupo-teste@g.us")
        self.assertFalse(out["success"])
        self.assertNotIn("downloads folder", out["hint"])
        self.assertIn("deleted this message for everyone", out["hint"])

    def test_outra_falha_mantem_a_dica_da_pasta(self):
        with mock.patch.object(main, "whatsapp_download_media", return_value=(None, "HTTP 500 - timeout")):
            out = main.download_media("MSG-1", "grupo-teste@g.us")
        self.assertFalse(out["success"])
        self.assertIn("downloads folder", out["hint"])


if __name__ == "__main__":
    unittest.main()
