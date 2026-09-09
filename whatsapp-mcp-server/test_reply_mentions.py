"""Stdlib unittest for task 7 of
docs/rainforest/planos/2026-09-08-responder-citando-e-mencionar.md: the read
surface (format_message, _message_from_dict) and the send-side payload
(send_message/send_file) for citation ("reply to") and mentions.

Style follows test_transcribe.py: stdlib unittest, no network, no bridge
process. Run: python -m unittest test_reply_mentions -v

Identifiers below are deliberately not phone-shaped (mirrors the convention
in whatsapp-bridge/main_test.go's TestSendQuotedRecusa) — this repo's recent
history is about a leaked real number, and personal-data-baseline.txt is the
only source of real numbers allowed in a test.
"""

import re
import unittest
from datetime import datetime
from unittest import mock

import whatsapp
from whatsapp import Message, format_message, _message_from_dict


def _msg(**overrides):
    base = dict(
        timestamp=datetime(2026, 9, 8, 10, 0, 0),
        sender="remetente-teste@s.whatsapp.net",
        content="respondendo",
        is_from_me=False,
        chat_jid="chat-teste@s.whatsapp.net",
        id="MSG-2",
    )
    base.update(overrides)
    return Message(**base)


class FormatMessageCitacaoTest(unittest.TestCase):
    def test_format_message_mostra_citacao_por_nome(self):
        msg = _msg(
            quoted_message_id="MSG-CITADA-TESTE",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content="Esse eh o texto original citado para o teste",
        )
        names = {
            "remetente-teste@s.whatsapp.net": "Fulano",
            "autor-citado@s.whatsapp.net": "Beltrano",
        }
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda jid, account=None: names.get(jid, jid)):
            output = format_message(msg, show_chat_info=False)

        self.assertIn(
            '↳ reply to Beltrano [MSG-CITADA-TESTE]: "Esse eh o texto original citado para o teste"',
            output,
        )
        self.assertIsNone(
            re.search(r"\d{8,}", output),
            f"output has a run of 8+ digits (looks like a phone number): {output!r}",
        )

    def test_format_message_mostra_mencao_por_nome(self):
        msg = _msg(mentions=["mencionado-a@s.whatsapp.net", "mencionado-b@s.whatsapp.net"])
        names = {
            "remetente-teste@s.whatsapp.net": "Fulano",
            "mencionado-a@s.whatsapp.net": "Ana",
            "mencionado-b@s.whatsapp.net": "Bia",
        }
        with mock.patch("whatsapp.get_sender_name", side_effect=lambda jid, account=None: names.get(jid, jid)):
            output = format_message(msg, show_chat_info=False)

        self.assertIn("    @ mentions: Ana, Bia", output)
        self.assertIsNone(re.search(r"\d{8,}", output))

    def test_sem_citacao_e_sem_mencao_saida_identica_a_de_hoje(self):
        msg = _msg()
        with mock.patch("whatsapp.get_sender_name", return_value="Fulano"):
            output = format_message(msg, show_chat_info=False)

        self.assertEqual(output, "[2026-09-08 10:00:00] From: Fulano: respondendo\n")
        self.assertNotIn("↳", output)
        self.assertNotIn("@ mentions", output)

    def test_truncamento_em_80_chars_com_reticencias(self):
        long_content = "x" * 100
        msg = _msg(
            quoted_message_id="MSG-LONGA",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content=long_content,
        )
        with mock.patch("whatsapp.get_sender_name", return_value="Beltrano"):
            output = format_message(msg, show_chat_info=False)

        expected_preview = "x" * 80 + "…"
        self.assertIn(f'"{expected_preview}"', output)

    def test_sem_truncamento_quando_cabe_em_80(self):
        content = "y" * 80
        msg = _msg(
            quoted_message_id="MSG-CURTA",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content=content,
        )
        with mock.patch("whatsapp.get_sender_name", return_value="Beltrano"):
            output = format_message(msg, show_chat_info=False)

        self.assertIn(f'"{content}"', output)
        self.assertNotIn("…", output)

    def test_get_sender_name_excecao_nao_impede_a_formatacao(self):
        msg = _msg(
            quoted_message_id="MSG-X",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content="texto",
        )
        with mock.patch("whatsapp.get_sender_name", side_effect=RuntimeError("boom")):
            output = format_message(msg, show_chat_info=False)

        # The main line's own try/except already handled this failure mode
        # before task 7 (get_sender_name raising for message.sender).
        self.assertIn("[Error formatting message: boom]", output)
        # The quoted-sender resolution has its own independent fallback: the
        # rest of the line (id, preview) survives, falling back to the raw
        # JID for the name that couldn't be resolved.
        self.assertIn('↳ reply to autor-citado@s.whatsapp.net [MSG-X]: "texto"', output)


class MessageFromDictTest(unittest.TestCase):
    def _base_dict(self, **overrides):
        d = {
            "timestamp": "2026-09-08T10:00:00",
            "sender": "remetente-teste@s.whatsapp.net",
            "content": "oi",
            "is_from_me": False,
            "chat_jid": "chat-teste@s.whatsapp.net",
            "id": "MSG-1",
        }
        d.update(overrides)
        return d

    def test_campos_presentes(self):
        d = self._base_dict(
            quoted_message_id="MSG-CITADA",
            quoted_sender="autor-citado@s.whatsapp.net",
            quoted_content="texto citado",
            mentions=["mencionado@s.whatsapp.net"],
        )
        msg = _message_from_dict(d)
        self.assertEqual(msg.quoted_message_id, "MSG-CITADA")
        self.assertEqual(msg.quoted_sender, "autor-citado@s.whatsapp.net")
        self.assertEqual(msg.quoted_content, "texto citado")
        self.assertEqual(msg.mentions, ["mencionado@s.whatsapp.net"])

    def test_campos_ausentes(self):
        msg = _message_from_dict(self._base_dict())
        self.assertIsNone(msg.quoted_message_id)
        self.assertIsNone(msg.quoted_sender)
        self.assertIsNone(msg.quoted_content)
        self.assertEqual(msg.mentions, [])

    def test_mentions_null_vira_lista_vazia(self):
        msg = _message_from_dict(self._base_dict(mentions=None))
        self.assertEqual(msg.mentions, [])


class SendPayloadTest(unittest.TestCase):
    """send_message/send_file must include quoted_message_id/mentions in the
    POST payload only when given, and never touch the real accounts.json on
    this machine — account is always passed explicitly so _require_account
    never calls accounts.known_aliases(), and accounts.resolve_account is
    mocked so no real bridge config is read.
    """

    def setUp(self):
        patcher = mock.patch("whatsapp.accounts.resolve_account", return_value="http://localhost:9999/api")
        self.addCleanup(patcher.stop)
        patcher.start()

    @staticmethod
    def _fake_response(json_body, status=200):
        resp = mock.Mock()
        resp.status_code = status
        resp.json.return_value = json_body
        resp.text = ""
        return resp

    def test_send_message_payload_sem_campos_novos(self):
        with mock.patch("whatsapp.requests.post") as mock_post:
            mock_post.return_value = self._fake_response({"success": True, "message": "ok"})
            ok, msg = whatsapp.send_message("destino-teste@s.whatsapp.net", "oi", account="conta-teste")

        self.assertTrue(ok)
        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("quoted_message_id", payload)
        self.assertNotIn("mentions", payload)

    def test_send_message_payload_com_campos_novos(self):
        with mock.patch("whatsapp.requests.post") as mock_post:
            mock_post.return_value = self._fake_response({"success": True, "message": "ok"})
            ok, msg = whatsapp.send_message(
                "destino-teste@s.whatsapp.net",
                "oi @Fulano",
                account="conta-teste",
                quoted_message_id="MSG-CITADA-TESTE",
                mentions=["Fulano"],
            )

        self.assertTrue(ok)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["quoted_message_id"], "MSG-CITADA-TESTE")
        self.assertEqual(payload["mentions"], ["Fulano"])

    def test_send_file_payload_sem_campos_novos(self):
        with mock.patch("whatsapp.requests.post") as mock_post, \
             mock.patch("whatsapp.os.path.isfile", return_value=True):
            mock_post.return_value = self._fake_response({"success": True, "message": "ok"})
            ok, msg = whatsapp.send_file("destino-teste@s.whatsapp.net", "arquivo.jpg", account="conta-teste")

        self.assertTrue(ok)
        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("quoted_message_id", payload)
        self.assertNotIn("mentions", payload)

    def test_send_file_payload_com_campos_novos(self):
        with mock.patch("whatsapp.requests.post") as mock_post, \
             mock.patch("whatsapp.os.path.isfile", return_value=True):
            mock_post.return_value = self._fake_response({"success": True, "message": "ok"})
            ok, msg = whatsapp.send_file(
                "destino-teste@s.whatsapp.net",
                "arquivo.jpg",
                account="conta-teste",
                quoted_message_id="MSG-CITADA-TESTE",
                mentions=["Fulano"],
            )

        self.assertTrue(ok)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["quoted_message_id"], "MSG-CITADA-TESTE")
        self.assertEqual(payload["mentions"], ["Fulano"])


if __name__ == "__main__":
    unittest.main()
