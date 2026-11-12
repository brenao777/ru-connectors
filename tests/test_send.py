"""Offline contract tests. No credentials or real network access."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import smtplib
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1] / 'plugins/ru-connectors/yandex-mail/server'
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location('mail_under_test', SERVER / 'yandex_mail_mcp.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.object(m, "data_directory", return_value=Path(self.temp.name))
        self.env.start(); self.addCleanup(self.env.stop)
        self.creds = patch.object(m, 'load_credentials', return_value=('sender@yandex.ru', 'fake-test-password'))
        self.credentials_mock = self.creds.start(); self.addCleanup(self.creds.stop)
        self.username = patch.object(m, 'read_username', return_value='sender@yandex.ru')
        self.username.start(); self.addCleanup(self.username.stop)
        self.smtp_patch = patch.object(m.smtplib, 'SMTP_SSL')
        self.smtp = self.smtp_patch.start(); self.addCleanup(self.smtp_patch.stop)
        self.client = self.smtp.return_value
        self.client.send_message.return_value = {}
        self.args = dict(to='receiver@example.com', cc='copy@example.com', bcc='hidden@example.com',
                         subject='Проверка письма', body_text='Текст на русском.\nВторая строка.',
                         confirm=True, idempotency_key='send-test-0001')

    def test_send_bcc_and_receipt(self):
        result = m.send_message_impl(self.args)
        self.assertEqual(result['status'], 'accepted')
        self.assertTrue(result['sent'])
        self.smtp.assert_called_once()
        args, kwargs = self.smtp.call_args
        self.assertEqual(args, ('smtp.yandex.ru', 465))
        self.assertTrue(kwargs['context'].check_hostname)
        message = self.client.send_message.call_args.args[0]
        envelope = self.client.send_message.call_args.kwargs
        self.assertNotIn('Bcc', message)
        self.assertEqual(message['From'], 'sender@yandex.ru')
        self.assertIn('hidden@example.com', envelope['to_addrs'])
        self.assertEqual(str(message['Subject']), self.args['subject'])
        self.assertEqual(message.get_content().strip(), self.args['body_text'])
        self.assertEqual(m.get_send_status_impl(self.args)['message_id'], result['message_id'])
        db = m.send_database()
        try:
            row = db.execute('SELECT fingerprint, result FROM sends').fetchone()
        finally:
            db.close()
        self.assertNotIn(self.args['body_text'], str(row))
        self.assertNotIn('fake-test-password', str(row))

    def test_retries_survive_new_module(self):
        first = m.send_message_impl(self.args)
        # Re-executing the module simulates a fresh server with the same local DB.
        fresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(fresh)
        with patch.object(fresh, 'load_credentials', return_value=('sender@yandex.ru', 'fake')), \
                patch.object(fresh, 'data_directory', return_value=Path(self.temp.name)):
            second = fresh.send_message_impl(self.args)
        self.assertTrue(second['already_existed'])
        self.assertEqual(first['message_id'], second['message_id'])
        self.assertEqual(self.client.send_message.call_count, 1)

    def test_parallel_calls_send_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: m.send_message_impl(self.args), range(2)))
        self.assertEqual(self.client.send_message.call_count, 1)
        self.assertEqual(sum(bool(r.get('already_existed')) for r in results), 1)

    def test_changed_payload_rejected(self):
        m.send_message_impl(self.args)
        with self.assertRaises(m.SafeMailError):
            m.send_message_impl({**self.args, 'body_text': 'Changed'})
        self.assertEqual(self.client.send_message.call_count, 1)

    def test_confirm_and_injection_rejected_before_network(self):
        for change in [dict(confirm=False), dict(confirm='true'), dict(subject='Subject\r\nBcc: evil@example.com'),
                       dict(to='good@example.com\nBcc: evil@example.com'), dict(in_reply_to='\r\nInjected'),
                       dict(idempotency_key='short'), dict(body_text=' '), dict(to='bad')]:
            with self.subTest(change=change), self.assertRaises(m.SafeMailError):
                m.send_message_impl({**self.args, **change})
        self.smtp.assert_not_called()

    def test_unknown_outcome_never_resends(self):
        self.client.send_message.side_effect = smtplib.SMTPServerDisconnected('connection lost')
        result = m.send_message_impl(self.args)
        self.assertEqual(result['status'], 'pending_or_unknown')
        self.assertIsNone(result['sent'])
        again = m.send_message_impl(self.args)
        self.assertTrue(again['already_existed'])
        self.assertEqual(self.client.send_message.call_count, 1)

    def test_auth_failure_before_send(self):
        self.client.login.side_effect = smtplib.SMTPAuthenticationError(535, b'rejected')
        result = m.send_message_impl(self.args)
        self.assertEqual(result['status'], 'failed_before_send')
        self.assertFalse(result['sent'])
        self.client.send_message.assert_not_called()

    def test_all_recipients_rejected(self):
        self.client.send_message.side_effect = smtplib.SMTPRecipientsRefused({'receiver@example.com': (550, b'rejected')})
        result = m.send_message_impl(self.args)
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(result['sent'])

    def test_partial_acceptance_and_quit_failure(self):
        self.client.send_message.return_value = {'hidden@example.com': (550, b'rejected')}
        self.client.quit.side_effect = smtplib.SMTPServerDisconnected('quit failed')
        result = m.send_message_impl(self.args)
        self.assertEqual(result['status'], 'partially_accepted')
        self.assertEqual(result['refused_recipients'], ['hidden@example.com'])
        self.assertNotIn('hidden@example.com', result['accepted_recipients'])
        self.assertEqual(m.get_send_status_impl(self.args)['status'], 'partially_accepted')

    def test_rpc_schema_and_utf8(self):
        response = m.handle_request(dict(id=1, method='tools/call', params=dict(name='send_message', arguments={**self.args, 'attachments': ['secret.txt']})))
        self.assertTrue(response['result']['isError'])
        self.smtp.assert_not_called()
        tools = m.handle_request(dict(id=2, method='tools/list'))['result']['tools']
        self.assertIn('read_attachment', {tool['name'] for tool in tools})
        send_tool = next(t for t in tools if t['name'] == 'send_message')
        self.assertFalse(send_tool['annotations']['readOnlyHint'])
        self.assertTrue(send_tool['annotations']['openWorldHint'])
        self.assertIn('Проверка', m.encode_response({'text': 'Проверка'}).decode('utf-8'))

    def test_no_credentials_or_network_on_initialize(self):
        response = m.handle_request(dict(id=1, method='initialize'))
        self.assertEqual(response['result']['serverInfo']['version'], m.SERVER_VERSION)
        self.credentials_mock.assert_not_called()
        self.smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
