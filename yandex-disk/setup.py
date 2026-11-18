"""Validate an OAuth token before saving it, or check/delete the saved token."""

import argparse
import getpass
from pathlib import Path
import sys
import warnings

from client_config import configure
from credentials import delete_token, read_token, write_token
from disk_api import DiskClient, DiskError


def main():
    parser = argparse.ArgumentParser(description="Подключение Яндекс Диска на macOS и Windows")
    parser.add_argument("action", choices=("configure", "connect", "check", "disconnect"))
    action = parser.parse_args().action
    try:
        if sys.platform not in ("darwin", "win32") or sys.version_info < (3, 9):
            raise DiskError("Для настройки нужна macOS или Windows с Python 3.9+.")
        if action == "configure":
            configure(Path(__file__).resolve().parents[1])
            print("Подготовлены настройки тестового проекта для Codex и Claude Code.")
        elif action == "disconnect":
            delete_token()
            print("Токен удалён из системного хранилища.")
        elif action == "check":
            DiskClient(read_token()).check_connection(check_write=True)
            print("Доступ к чтению и загрузке на Яндекс Диск подтверждён.")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("Вставьте OAuth-токен Яндекс Диска: ").strip()
            DiskClient(token).check_connection(check_write=True)
            write_token(token)
            print("Яндекс Диск подключён. Токен сохранён в системном хранилище.")
    except (EOFError, getpass.GetPassWarning):
        print("Откройте Терминал или PowerShell для скрытого ввода токена.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except DiskError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
