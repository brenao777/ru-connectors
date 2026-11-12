"""SMTP submission and local receipts shared by the mail connectors."""

import datetime as dt
import email.utils
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import sys
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Tuple


class SafeMailError(Exception):
    pass


def check_smtp(host: str, port: int, username: str, password: str) -> bool:
    client = None
    try:
        client = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15)
        client.login(username, password)
        return True
    except (OSError, smtplib.SMTPException):
        return False
    finally:
        if client is not None:
            client.close()


def validate_addresses(raw: Any, field_name: str) -> List[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    if any(not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value) for value in values):
        raise SafeMailError("Адреса должны быть строками без управляющих символов.")
    addresses: List[str] = []
    for _, address in email.utils.getaddresses([str(value) for value in values]):
        address = address.strip()
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", address):
            raise SafeMailError("Некорректный адрес в поле " + field_name + ": " + address)
        addresses.append(address)
    if not addresses and values:
        raise SafeMailError("Не удалось распознать адреса в поле " + field_name + ".")
    return addresses


def send_key(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value):
        raise SafeMailError("Укажите idempotency_key длиной 8–128 безопасных символов.")
    return value


def data_directory(server_name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / server_name
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return root / "Codex" / server_name


def send_database(directory: Path) -> sqlite3.Connection:
    # Outside the plugin directory: upgrading the plugin must not erase receipts.
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(directory / "send-receipts.sqlite3"), timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS sends (operation TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
    db.commit()
    return db


def send_operation(username: str, key: str) -> str:
    return hashlib.sha256((username.casefold() + "\n" + key).encode("utf-8")).hexdigest()


def get_send_status(args: Dict[str, Any], username: str, directory: Path) -> Dict[str, Any]:
    key = send_key(args.get("idempotency_key"))
    db = send_database(directory)
    try:
        row = db.execute("SELECT result FROM sends WHERE operation=?", (send_operation(username, key),)).fetchone()
        return json.loads(row[0]) if row else {"status": "not_found", "sent": False}
    finally:
        db.close()


def send_message(
    args: Dict[str, Any],
    load_credentials: Callable[[], Tuple[str, str]],
    directory: Path,
    host: str,
    port: int,
) -> Dict[str, Any]:
    if args.get("confirm") is not True:
        raise SafeMailError("Отправка требует явного запроса пользователя и confirm: true.")
    to = validate_addresses(args.get("to"), "to")
    cc = validate_addresses(args.get("cc"), "cc")
    bcc = validate_addresses(args.get("bcc"), "bcc")
    recipients = list(dict.fromkeys(to + cc + bcc))
    if not to or len(recipients) > 50:
        raise SafeMailError("Нужен получатель to; всего допускается не более 50 адресов.")
    subject = args.get("subject")
    body = args.get("body_text")
    if (not isinstance(subject, str) or not subject.strip() or len(subject) > 998
            or any(ord(c) < 32 or ord(c) == 127 for c in subject)
            or not isinstance(body, str) or not body.strip() or len(body) > 200000):
        raise SafeMailError("Нужны непустые тема (до 998 символов, без управляющих символов) и текст (до 200000).")
    reply_headers = {}
    for field in ("in_reply_to", "references"):
        value = args.get(field, "")
        if not isinstance(value, str) or len(value) > 998 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise SafeMailError("Некорректный заголовок " + field)
        reply_headers[field] = value
    key = send_key(args.get("idempotency_key"))
    username, password = load_credentials()
    # ASCII envelope avoids silently sending to a changed address without SMTPUTF8.
    try:
        (username + "".join(recipients)).encode("ascii")
    except UnicodeEncodeError as exc:
        raise SafeMailError("SMTP-адреса должны быть в ASCII; используйте punycode для домена.") from exc
    payload = {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body_text": body, **reply_headers}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    operation = send_operation(username, key)
    message_id = "<codex-" + operation + "@" + username.split("@")[-1] + ">"
    message = EmailMessage()
    message["From"] = username
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    # No Bcc header: hidden recipients exist only in the SMTP envelope.
    message["Subject"] = subject
    message["Date"] = email.utils.format_datetime(dt.datetime.now().astimezone())
    message["Message-ID"] = message_id
    for field, header in (("in_reply_to", "In-Reply-To"), ("references", "References")):
        if reply_headers[field]:
            message[header] = reply_headers[field]
    message.set_content(body)
    pending = {"status": "pending_or_unknown", "sent": None, "message_id": message_id,
               "notice": "Исход отправки пока неизвестен. Не повторяйте с новым ключом; проверьте Отправленные и получателя."}
    db = send_database(directory)
    try:
        # A unique row serializes duplicate calls, including separate MCP processes.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT fingerprint, result FROM sends WHERE operation=?", (operation,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise SafeMailError("Этот idempotency_key уже использован для другого письма.")
            result = json.loads(row[1])
            result["already_existed"] = True
            return result
        db.execute("INSERT INTO sends VALUES (?, ?, ?)", (operation, fingerprint, json.dumps(pending)))
        db.commit()
        client = None
        sending = False
        try:
            client = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
            client.login(username, password)
            sending = True
            refused = client.send_message(message, from_addr=username, to_addrs=recipients)
            result = {"status": "partially_accepted" if refused else "accepted", "sent": True,
                      "message_id": message_id, "accepted_recipients": [r for r in recipients if r not in refused],
                      "refused_recipients": list(refused), "already_existed": False,
                      "notice": "SMTP-сервер принял письмо для указанных получателей. Доставка адресатам этим не подтверждена."}
        except smtplib.SMTPRecipientsRefused:
            result = {"status": "rejected", "sent": False, "message_id": message_id,
                      "notice": "SMTP отклонил всех получателей; письмо не принято."}
        except (OSError, smtplib.SMTPException, ValueError):
            result = pending if sending else {"status": "failed_before_send", "sent": False, "message_id": message_id,
                       "notice": "Подключение или вход SMTP не удались. Проверьте сеть, пароль приложения и доступ SMTP."}
        finally:
            if client is not None:
                # QUIT failure after DATA must not turn an accepted message into a retry.
                try:
                    client.quit()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass
        db.execute("UPDATE sends SET result=? WHERE operation=?", (json.dumps(result, ensure_ascii=False), operation))
        db.commit()
        return result
    finally:
        db.close()


SEND_TOOLS = [
    {
        "name": "send_message",
        "description": "Send a plain-text email through SMTP only on the user's explicit send request. confirm must be true. Keep one idempotency_key for retries; pending_or_unknown must never be retried with a new key. SMTP acceptance does not guarantee delivery.",
        "inputSchema": {
            "type": "object", "required": ["to", "subject", "body_text", "idempotency_key", "confirm"],
            "properties": {
                **{field: {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]} for field in ("to", "cc", "bcc")},
                "subject": {"type": "string", "minLength": 1, "maxLength": 998},
                "body_text": {"type": "string", "minLength": 1, "maxLength": 200000},
                "in_reply_to": {"type": "string"}, "references": {"type": "string"},
                "idempotency_key": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{8,128}$"},
                "confirm": {"type": "boolean", "const": True},
            }, "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "get_send_status",
        "description": "Read the local receipt for a send idempotency_key without sending anything. pending_or_unknown is not evidence of delivery or failure.",
        "inputSchema": {"type": "object", "required": ["idempotency_key"], "properties": {
            "idempotency_key": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{8,128}$"}}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]
