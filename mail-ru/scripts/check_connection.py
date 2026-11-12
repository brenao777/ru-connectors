#!/usr/bin/env python3
"""Проверка реального IMAP без вывода содержимого писем и секретов."""
import importlib.util
import json
from pathlib import Path
import sys

server_dir = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(server_dir))
spec = importlib.util.spec_from_file_location('mail_connector', server_dir / 'mail_ru_mcp.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
try:
    folders = m.list_folders_impl()
    client, _ = m.connect(folder='INBOX')
    try:
        ids = m.uid_search(client, True, None)
        if not ids:
            ids = m.uid_search(client, False, None)
        if not ids:
            print(json.dumps({'connected': True, 'folders': len(folders['folders']), 'message_test': 'empty inbox'}, ensure_ascii=False))
        else:
            uid = ids[-1]
            before = m.fetch_headers(client, [uid])[0]['unread']
            message = m.read_message_impl({'uid': uid, 'max_body_chars': 1000})
            after = m.fetch_headers(client, [uid])[0]['unread']
            print(json.dumps({'connected': True, 'folders': len(folders['folders']), 'message_test': True, 'body_extracted': bool(message['body_text']), 'unread_status_preserved': before == after, 'attachment_metadata_count': len(message['attachments'])}, ensure_ascii=False))
            if before != after:
                sys.exit(2)
    finally:
        m.close_client(client)
except Exception as exc:
    print(json.dumps({'connected': False, 'error_type': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
    sys.exit(1)
