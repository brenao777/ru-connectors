"""Collect a Mail.ru app password in a macOS dialog, then verify and save it."""
import imaplib
import json
from pathlib import Path
import re
import ssl
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from mail_ru_mcp import IMAP_HOST, IMAP_PORT, KEYCHAIN_SLOT_ACCOUNT, KEYCHAIN_SLOT_SERVICE, close_client


DIALOG = '''
activate
set a to display dialog "Введите полный адрес Почты Mail" default answer "" with title "Почта Mail" giving up after 900
if gave up of a then error number -128
set p to display dialog "Введите пароль внешнего приложения Почты Mail" default answer "" with hidden answer with title "Почта Mail" giving up after 900
if gave up of p then error number -128
return (text returned of a) & linefeed & (text returned of p)
'''


def main():
    result = subprocess.run(
        ["/usr/bin/osascript", "-"], input=DIALOG, capture_output=True, text=True,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            raise SystemExit(2)
        raise SystemExit("Не удалось открыть системное окно Почты Mail.")
    address, _, secret = result.stdout.rstrip("\r\n").partition("\n")
    address = address.strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+", address) or not secret:
        raise SystemExit("Проверьте адрес и пароль приложения.")

    client = None
    try:
        client = imaplib.IMAP4_SSL(
            IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=20,
        )
        status, _ = client.login(address, secret)
        if status != "OK":
            raise SystemExit("Почта Mail отклонила вход по IMAP.")
    except (imaplib.IMAP4.error, OSError):
        raise SystemExit("Не удалось войти в Почту Mail. Проверьте IMAP и пароль приложения.") from None
    finally:
        if client is not None:
            close_client(client)

    # security -i reads the hex-encoded secret from stdin, not from argv.
    record = json.dumps([address, secret])
    try:
        subprocess.run(
            ["/usr/bin/security", "-i"],
            input=(f'add-generic-password -U -s "{KEYCHAIN_SLOT_SERVICE}" '
                   f'-a "{KEYCHAIN_SLOT_ACCOUNT}" -X {record.encode().hex()}\n'),
            capture_output=True, text=True, check=True, timeout=120,
        )
        verified = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SLOT_SERVICE, "-a", KEYCHAIN_SLOT_ACCOUNT, "-w"],
            capture_output=True, text=True, check=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        raise SystemExit("Не удалось сохранить пароль Почты Mail в Связке ключей.") from None
    if verified.stdout.rstrip("\r\n") != record:
        raise SystemExit("Не удалось проверить запись в Связке ключей.")


if __name__ == "__main__":
    main()
