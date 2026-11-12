"""Collect credentials in a protected macOS dialog."""
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from macos_keychain import CredentialManagerError, write_credential


DIALOG = '''
activate
set a to display dialog "Введите полный адрес Яндекс Почты" default answer "" with title "Яндекс Почта" giving up after 900
if gave up of a then error number -128
set p to display dialog "Введите пароль приложения Яндекса для типа «Почта»" default answer "" with hidden answer with title "Яндекс Почта" giving up after 900
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
        raise SystemExit("Не удалось открыть системное окно Яндекс Почты.")
    address, _, password = result.stdout.rstrip("\r\n").partition("\n")
    address, password = address.strip(), password.strip()
    if not re.fullmatch(r"[!-~]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", address) or not password:
        raise SystemExit("Проверьте адрес почты и пароль приложения.")
    try:
        write_credential(address, password)
    except CredentialManagerError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
