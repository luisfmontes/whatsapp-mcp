"""request_history (issue #40): the MCP side posts the anchor to
/history_request and passes the bridge's asynchronous answer through."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import whatsapp


def _response(payload, status=202):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


class RequestHistoryTest(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(whatsapp, "_require_account"),
            mock.patch.object(whatsapp.accounts, "resolve_account", return_value="http://bridge/api"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_posta_ancora_e_count(self):
        with mock.patch.object(whatsapp, "_api_request",
                               return_value=_response({"success": True, "message": "History requested"})) as api:
            ok, msg = whatsapp.request_history("contato@s.whatsapp.net", "MSG-ANCORA", count=20)
        self.assertTrue(ok)
        self.assertEqual(msg, "History requested")
        api.assert_called_once_with("POST", "/history_request", "http://bridge/api",
                                    json={"chat_jid": "contato@s.whatsapp.net", "message_id": "MSG-ANCORA", "count": 20})

    def test_ancora_desconhecida_volta_falha(self):
        with mock.patch.object(whatsapp, "_api_request",
                               return_value=_response({"success": False, "message": "Anchor message not found"}, 404)):
            ok, msg = whatsapp.request_history("contato@s.whatsapp.net", "MSG-SUMIDA")
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_campos_obrigatorios_nao_chamam_a_ponte(self):
        with mock.patch.object(whatsapp, "_api_request") as api:
            self.assertFalse(whatsapp.request_history("", "MSG")[0])
            self.assertFalse(whatsapp.request_history("contato@s.whatsapp.net", " ")[0])
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
