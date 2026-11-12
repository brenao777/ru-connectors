"""Store one mailbox in the macOS login Keychain."""
import base64
import json
import subprocess


SERVICE = "com.yandex-mail-safe.mailbox"
ACCOUNT = "default"


class CredentialManagerError(RuntimeError):
    pass


def security(*args, command_input=None):
    return subprocess.run(
        ["/usr/bin/security", *args], input=command_input,
        capture_output=True, text=True, timeout=120,
    )


def read_credential():
    result = security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w")
    if result.returncode:
        raise CredentialManagerError("Связка ключей не вернула запись Яндекс Почты.")
    try:
        address, password = json.loads(base64.b64decode(result.stdout.strip(), validate=True))
        if not isinstance(address, str) or not isinstance(password, str) or not password:
            raise ValueError
    except (ValueError, TypeError):
        raise CredentialManagerError("Запись Яндекс Почты повреждена.") from None
    return address, password


def read_username():
    return read_credential()[0]


def write_credential(address, password):
    # The secret is passed over stdin and is absent from argv and environment.
    encoded = base64.b64encode(json.dumps([address, password]).encode()).decode("ascii")
    command = f'add-generic-password -U -s "{SERVICE}" -a "{ACCOUNT}" -w "{encoded}"\n'
    result = security("-i", command_input=command)
    if result.returncode or read_credential() != (address, password):
        raise CredentialManagerError("Связка ключей не сохранила данные Яндекс Почты.")


def delete_credential(address):
    if read_username().casefold() != address.casefold():
        raise CredentialManagerError("Подключённый ящик изменился. Повторите запрос.")
    result = security("delete-generic-password", "-s", SERVICE, "-a", ACCOUNT)
    if result.returncode:
        raise CredentialManagerError("Связка ключей не удалила запись Яндекс Почты.")
