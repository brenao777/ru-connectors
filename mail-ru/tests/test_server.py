import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

p = Path(__file__).resolve().parents[1] / 'server' / 'mail_ru_mcp.py'
spec = importlib.util.spec_from_file_location('mail_ru_mcp', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class MailConnectorTests(unittest.TestCase):
    def test_only_read_tools_are_exposed(self):
        self.assertEqual(set(m.HANDLERS), {'list_folders', 'list_messages', 'search_messages', 'read_message'})
        self.assertEqual({x['name'] for x in m.TOOLS}, set(m.HANDLERS))
        self.assertTrue(all(x['annotations']['readOnlyHint'] for x in m.TOOLS))

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

    def test_unknown_write_tool_is_rejected(self):
        response = m.handle_request({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'send_message','arguments':{}}})
        self.assertIn('error', response)

if __name__ == '__main__':
    unittest.main()
