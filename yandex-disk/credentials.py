"""Store the test connector's OAuth token in a dedicated macOS Keychain item."""

import subprocess
import sys

from disk_api import DiskError, validate_token


SERVICE = "ru-connectors.yandex-disk"
ACCOUNT = "default"


def security(*args, command_input=None):
    if sys.platform != "darwin":
        raise DiskError("Эта версия коннектора хранит токен в Связке ключей macOS.")
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            input=command_input, capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DiskError("Связка ключей недоступна. Разблокируйте её и повторите.") from None


def read_token():
    result = security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w")
    if result.returncode:
        raise DiskError("Токен Яндекс Диска недоступен. Запустите setup.py connect в Терминале.")
    token = result.stdout.strip()
    validate_token(token)
    return token


def write_token(token):
    validate_token(token)
    # security -i reads the command over stdin, so the secret stays out of argv and config.
    command = f'add-generic-password -U -s "{SERVICE}" -a "{ACCOUNT}" -w "{token}"\n'
    result = security("-i", command_input=command)
    if result.returncode or read_token() != token:
        raise DiskError("Связка ключей не подтвердила сохранение токена.")


def delete_token():
    result = security("delete-generic-password", "-s", SERVICE, "-a", ACCOUNT)
    if result.returncode:
        raise DiskError("Связка ключей не подтвердила удаление токена.")
