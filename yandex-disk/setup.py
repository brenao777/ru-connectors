"""Validate an OAuth token before saving it, or check/delete the saved token."""

import argparse
import getpass
import sys
import warnings

from credentials import delete_token, read_token, write_token
from disk_api import DiskClient, DiskError


def main():
    parser = argparse.ArgumentParser(description="Подключение Яндекс Диска на macOS")
    parser.add_argument("action", choices=("connect", "check", "disconnect"))
    action = parser.parse_args().action
    try:
        if sys.platform != "darwin":
            raise DiskError("Для пробной настройки нужна macOS.")
        if action == "disconnect":
            delete_token()
            print("Токен удалён из Связки ключей.")
        elif action == "check":
            DiskClient(read_token()).check_connection()
            print("Подключение к Яндекс Диску работает.")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("Вставьте OAuth-токен Яндекс Диска: ").strip()
            DiskClient(token).check_connection()
            write_token(token)
            print("Яндекс Диск подключён. Токен сохранён в Связке ключей.")
    except (EOFError, getpass.GetPassWarning):
        print("Откройте Терминал и повторите команду для скрытого ввода токена.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except DiskError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
