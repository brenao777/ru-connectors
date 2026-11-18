"""Store the OAuth token using the current user's native credential store."""

import subprocess
import sys

from disk_api import DiskError, validate_token
import windows_credentials


SERVICE = "ru-connectors.yandex-disk"
ACCOUNT = "default"


def security(*args, command_input=None):
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            input=command_input, capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DiskError("Связка ключей недоступна. Разблокируйте её и повторите.") from None


def read_token():
    if sys.platform == "win32":
        token = windows_credentials.read_token()
    elif sys.platform == "darwin":
        result = security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w")
        if result.returncode:
            raise DiskError("Токен Яндекс Диска недоступен. Запустите setup.py connect в Терминале.")
        token = result.stdout.strip()
    else:
        raise DiskError("Для подключения нужна macOS или Windows.")
    validate_token(token)
    return token


def write_token(token):
    validate_token(token)
    if sys.platform == "win32":
        windows_credentials.write_token(token)
    elif sys.platform == "darwin":
        # security -i reads the command over stdin, keeping the secret out of argv.
        command = f'add-generic-password -U -s "{SERVICE}" -a "{ACCOUNT}" -w "{token}"\n'
        if security("-i", command_input=command).returncode:
            raise DiskError("Связка ключей не сохранила токен.")
    else:
        raise DiskError("Для подключения нужна macOS или Windows.")
    if read_token() != token:
        raise DiskError("Системное хранилище не подтвердило сохранение токена.")


def delete_token():
    if sys.platform == "win32":
        windows_credentials.delete_token()
        return
    if sys.platform != "darwin":
        raise DiskError("Для отключения нужна macOS или Windows.")
    result = security("delete-generic-password", "-s", SERVICE, "-a", ACCOUNT)
    if result.returncode:
        raise DiskError("Связка ключей не подтвердила удаление токена.")
