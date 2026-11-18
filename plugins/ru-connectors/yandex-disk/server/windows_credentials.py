"""Store the OAuth token as a generic Windows credential."""

import ctypes
from ctypes import wintypes

from disk_api import DiskError


TARGET_NAME = "ru-connectors.yandex-disk"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


def credential_api():
    try:
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    except OSError:
        raise DiskError("Диспетчер учётных данных Windows недоступен.") from None
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
    api.CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredReadW.restype = api.CredWriteW.restype = api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.restype = None
    return api


def read_token():
    api = credential_api()
    pointer = PCREDENTIALW()
    if not api.CredReadW(TARGET_NAME, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        raise DiskError("Токен Яндекс Диска недоступен. Подключите Диск через скилл connect-yandex-disk.")
    try:
        stored = pointer.contents
        return ctypes.string_at(stored.CredentialBlob, stored.CredentialBlobSize).decode("ascii")
    except UnicodeError:
        raise DiskError("Запись Яндекс Диска в Диспетчере учётных данных повреждена.") from None
    finally:
        api.CredFree(pointer)


def write_token(token):
    api = credential_api()
    secret = bytearray(token, "ascii")
    blob = (ctypes.c_ubyte * len(secret)).from_buffer(secret)
    credential = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=TARGET_NAME,
                             CredentialBlobSize=len(secret), CredentialBlob=blob,
                             Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="default")
    try:
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise DiskError("Диспетчер учётных данных Windows не сохранил токен.")
    finally:
        ctypes.memset(blob, 0, len(secret))


def delete_token():
    if not credential_api().CredDeleteW(TARGET_NAME, CRED_TYPE_GENERIC, 0):
        raise DiskError("Диспетчер учётных данных Windows не подтвердил удаление токена.")
