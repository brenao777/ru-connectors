"""Exercise MIME selection, read-only IMAP and the local-file handoff."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

SERVER = Path(__file__).resolve().parents[1] / "plugins/ru-connectors/yandex-mail/server"
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location("mail_attachment_test", SERVER / "yandex_mail_mcp.py")
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        message = EmailMessage()
        message.set_content("Тело письма")
        message.add_attachment("первый", filename="отчёт.txt")
        message.add_attachment("второй", filename="отчёт.txt")
        message.add_attachment(b"%PDF-test", maintype="application", subtype="pdf", filename="отчёт.pdf")
        self.raw = message.as_bytes()
        self.client = MagicMock()
        self.client.uid.return_value = ("OK", [(b"1 (BODY[] {" + str(len(self.raw)).encode() + b"}", self.raw),
                                                b" RFC822.SIZE " + str(len(self.raw)).encode() + b")"])

    def test_rpc_reads_second_same_named_attachment_without_seen_flag(self):
        with patch.object(mail, "connect", return_value=(self.client, "test")) as connect:
            message = mail.read_message_impl({"uid": "11", "folder": "Inbox"})
            first, second, _ = message["attachments"]
            self.assertEqual(first["filename"], second["filename"])
            self.assertNotEqual(first["attachment_id"], second["attachment_id"])
            response = mail.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "read_attachment", "arguments": {
                        "uid": "11", "folder": "Inbox", "attachment_id": second["attachment_id"],
                    },
                },
            })
        result = response["result"]["structuredContent"]
        self.assertEqual(result["text"].strip(), "второй")
        self.assertFalse(result["marked_read"])
        self.assertNotIn("local_path", result)
        connect.assert_called_with(readonly=True, folder="Inbox")
        for call in self.client.uid.call_args_list:
            self.assertEqual(call.args[:2], ("fetch", "11"))
            self.assertIn("BODY.PEEK[]", call.args[2])

    def test_document_handoff_saves_only_selected_file_under_local_profile(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(mail, "data_directory", return_value=Path(directory)):
            with patch.object(mail, "connect", return_value=(self.client, "test")):
                message = mail.read_message_impl({"uid": "11"})
                result = mail.read_attachment_impl({
                    "uid": "11", "attachment_id": message["attachments"][2]["attachment_id"],
                })
            path = Path(result["local_path"])
            self.assertEqual(path.read_bytes(), b"%PDF-test")
            self.assertEqual(result["document_format"], "pdf")
            self.assertEqual(path.parent, Path(directory).resolve() / "attachments")
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_incomplete_message_is_rejected_before_saving(self):
        attachment_id = mail.extract_body_and_attachments(
            mail.BytesParser(policy=mail.email.policy.default).parsebytes(self.raw)
        )[1][2]["attachment_id"]
        self.client.uid.return_value = ("OK", [(b"1 (BODY[]", self.raw[:-20]),
                                               b" RFC822.SIZE " + str(len(self.raw)).encode() + b")"])
        with patch.object(mail, "connect", return_value=(self.client, "test")), patch.object(mail, "save_attachment_file") as save:
            with self.assertRaisesRegex(mail.SafeMailError, "неполное письмо"):
                mail.read_attachment_impl({"uid": "11", "attachment_id": attachment_id})
            save.assert_not_called()

    def test_attached_message_is_not_in_parent_body(self):
        inner = EmailMessage()
        inner.set_content("Текст пересланного письма")
        outer = EmailMessage()
        outer.set_content("Текст текущего письма")
        outer.add_attachment(inner, filename="пересылка.eml")
        body, attachments = mail.extract_body_and_attachments(outer)
        self.assertEqual(body, "Текст текущего письма")
        self.assertEqual(attachments[0]["content_type"], "message/rfc822")
