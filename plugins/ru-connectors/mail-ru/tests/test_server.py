import importlib.util
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

p = Path(__file__).resolve().parents[1] / 'server' / 'mail_ru_mcp.py'
spec = importlib.util.spec_from_file_location('mail_ru_mcp', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class MailConnectorTests(unittest.TestCase):
    def test_send_tool_uses_mail_smtp_and_deduplicates(self):
        arguments = {
            "to": "recipient@example.com",
            "subject": "Проверка",
            "body_text": "Тестовое письмо",
            "idempotency_key": "mail-test-123",
            "confirm": True,
        }
        request = {"id": 1, "method": "tools/call",
                   "params": {"name": "send_message", "arguments": arguments}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(m.smtp, "data_directory", return_value=Path(directory)) as storage, \
                patch.object(m, "load_credentials", return_value=("sender@mail.ru", "test-only")), \
                patch.object(smtplib, "SMTP_SSL") as tls:
            tls.return_value.send_message.return_value = {}
            first = m.handle_request(request)["result"]["structuredContent"]
            second = m.handle_request(request)["result"]["structuredContent"]
            self.assertEqual(first["status"], "accepted")
            self.assertTrue(second["already_existed"])
            self.assertEqual(m.get_send_status_impl(arguments)["message_id"], first["message_id"])
        self.assertEqual(tls.call_args.args, ("smtp.mail.ru", 465))
        self.assertTrue(tls.call_args.kwargs["context"].check_hostname)
        storage.assert_called_with("mail-ru")
        tls.return_value.login.assert_called_once_with("sender@mail.ru", "test-only")
        tls.return_value.send_message.assert_called_once()

    def test_message_is_fetched_without_marking_read(self):
        message = EmailMessage()
        message['Subject'] = 'Тест'
        message.set_content('Текст письма')
        message.add_attachment(b'data', maintype='application', subtype='octet-stream', filename='a.bin')
        client = MagicMock()
        client.uid.return_value = ('OK', [(b'1 (FLAGS ()', message.as_bytes())])
        with patch.object(m, 'connect', return_value=(client, 'example@mail.ru')) as connect:
            result = m.read_message_impl({'uid': '123'})
        connect.assert_called_once_with(folder='INBOX')
        client.uid.assert_called_once_with('fetch', '123', '(BODY.PEEK[] FLAGS RFC822.SIZE)')
        self.assertIn('Текст письма', result['body_text'])
        self.assertEqual(result['attachments'][0]['filename'], 'a.bin')
        self.assertFalse(result['attachments_downloaded'])
        self.assertFalse(result['marked_read'])

    def test_connection_selects_readonly_and_tls(self):
        client = MagicMock()
        client.login.return_value = ('OK', [b''])
        client.select.return_value = ('OK', [b'0'])
        with patch.object(m, 'load_credentials', return_value=('example@mail.ru','secret')), patch.object(m.imaplib, 'IMAP4_SSL', return_value=client) as tls:
            m.connect(folder='INBOX')
        self.assertEqual(tls.call_args.args[:2], ('imap.mail.ru', 993))
        self.assertTrue(client.select.call_args.kwargs['readonly'])
        self.assertTrue(m.HEADER_FETCH.startswith('(BODY.PEEK['))

    def test_send_requires_explicit_confirmation_before_loading_credentials(self):
        arguments = {"to": "recipient@example.com", "subject": "Тест", "body_text": "Тест",
                     "idempotency_key": "mail-test-123", "confirm": False}
        with patch.object(m, "load_credentials") as credentials:
            response = m.handle_request({"id": 1, "method": "tools/call",
                                         "params": {"name": "send_message", "arguments": arguments}})
        self.assertTrue(response["result"]["isError"])
        credentials.assert_not_called()

if __name__ == '__main__':
    unittest.main()
