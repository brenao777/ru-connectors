"""Collect a Yandex Disk OAuth token in a macOS dialog, verify it, then save it."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from credentials import write_token
from disk_api import DiskClient, DiskError, validate_token


DIALOG = '''
activate
set t to display dialog "Вставьте OAuth-токен Яндекс Диска" default answer "" with hidden answer with title "Яндекс Диск" giving up after 900
if gave up of t then error number -128
return text returned of t
'''


def main():
    result = subprocess.run(
        ["/usr/bin/osascript", "-"], input=DIALOG, capture_output=True, text=True,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            raise SystemExit(2)
        raise SystemExit("Не удалось открыть системное окно Яндекс Диска.")
    token = result.stdout.rstrip("\r\n").strip()
    if not token:
        raise SystemExit("Токен не введён. Получите токен на странице Яндекс OAuth и повторите подключение.")
    try:
        validate_token(token)
        # Reading the root confirms the token before it reaches the credential store.
        DiskClient(token).check_connection()
        write_token(token)
    except DiskError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
