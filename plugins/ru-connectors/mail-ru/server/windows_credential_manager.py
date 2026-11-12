"""Minimal Windows Credential Manager wrapper using only ctypes."""

import ctypes
from ctypes import wintypes
import re
import sys
from typing import Any, Optional, Tuple


TARGET_NAME = "Codex_MailRuReadonly"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512
ERROR_NOT_FOUND = 1168


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class CREDENTIALW(ctypes.Structure):
    pass


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

CREDENTIALW._fields_ = [
    ("Flags", wintypes.DWORD),
    ("Type", wintypes.DWORD),
    ("TargetName", wintypes.LPWSTR),
    ("Comment", wintypes.LPWSTR),
    ("LastWritten", FILETIME),
    ("CredentialBlobSize", wintypes.DWORD),
    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
    ("Persist", wintypes.DWORD),
    ("AttributeCount", wintypes.DWORD),
    ("Attributes", ctypes.c_void_p),
    ("TargetAlias", wintypes.LPWSTR),
    ("UserName", wintypes.LPWSTR),
]


class CredentialManagerError(RuntimeError):
    pass


def _get_advapi() -> Any:
    if sys.platform != "win32":
        raise CredentialManagerError("Windows Credential Manager requires native Windows.")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(PCREDENTIALW),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _last_error(api: Any) -> int:
    custom = getattr(api, "last_error", None)
    return int(custom if custom is not None else ctypes.get_last_error())


def validate_mail_address(value: str) -> str:
    address = value.strip()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", address):
        raise CredentialManagerError("Enter a full valid mailbox address.")
    if "\x00" in address:
        raise CredentialManagerError("Mailbox address contains an invalid character.")
    return address


def write_credential(username: str, password: str, api: Optional[Any] = None) -> None:
    username = validate_mail_address(username)
    if not password or "\x00" in password:
        raise CredentialManagerError("The app password is empty or invalid.")
    secret_bytes = bytearray(password.encode("utf-16-le"))
    if len(secret_bytes) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
        raise CredentialManagerError("The app password exceeds the Windows credential limit.")
    blob = (ctypes.c_ubyte * len(secret_bytes)).from_buffer(secret_bytes)
    credential = CREDENTIALW()
    credential.Flags = 0
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = TARGET_NAME
    credential.Comment = "Mail.ru app password for the Codex safe connector"
    credential.CredentialBlobSize = len(secret_bytes)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.AttributeCount = 0
    credential.Attributes = None
    credential.TargetAlias = None
    credential.UserName = username
    target_api = api or _get_advapi()
    try:
        if not target_api.CredWriteW(ctypes.byref(credential), 0):
            code = _last_error(target_api)
            raise CredentialManagerError(
                "Windows Credential Manager rejected the credential (error %d)." % code
            )
    finally:
        for index in range(len(secret_bytes)):
            secret_bytes[index] = 0


def read_credential(api: Optional[Any] = None) -> Tuple[str, str]:
    target_api = api or _get_advapi()
    credential_pointer = PCREDENTIALW()
    if not target_api.CredReadW(
        TARGET_NAME,
        CRED_TYPE_GENERIC,
        0,
        ctypes.byref(credential_pointer),
    ):
        code = _last_error(target_api)
        if code == ERROR_NOT_FOUND:
            raise CredentialManagerError(
                "Mail.ru credentials are not configured in Windows Credential Manager."
            )
        raise CredentialManagerError(
            "Windows Credential Manager could not read the credential (error %d)." % code
        )
    try:
        credential = credential_pointer.contents
        username = credential.UserName or ""
        if credential.CredentialBlobSize:
            raw = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            password = raw.decode("utf-16-le")
        else:
            password = ""
        if not username or not password:
            raise CredentialManagerError("The stored Mail.ru credential is incomplete.")
        return username, password
    finally:
        target_api.CredFree(credential_pointer)


def read_username(api: Optional[Any] = None) -> str:
    """Read only the stored username, without decoding the credential secret."""
    target_api = api or _get_advapi()
    credential_pointer = PCREDENTIALW()
    if not target_api.CredReadW(
        TARGET_NAME,
        CRED_TYPE_GENERIC,
        0,
        ctypes.byref(credential_pointer),
    ):
        code = _last_error(target_api)
        if code == ERROR_NOT_FOUND:
            raise CredentialManagerError(
                "Mail.ru credentials are not configured in Windows Credential Manager."
            )
        raise CredentialManagerError(
            "Windows Credential Manager could not read the credential (error %d)." % code
        )
    try:
        username = credential_pointer.contents.UserName or ""
        if not username:
            raise CredentialManagerError("The stored Mail.ru username is missing.")
        return username
    finally:
        target_api.CredFree(credential_pointer)


def delete_credential(expected_username: str, api: Optional[Any] = None) -> None:
    """Delete the fixed connector credential only when its username matches."""
    expected = validate_mail_address(expected_username)
    target_api = api or _get_advapi()
    stored = read_username(target_api)
    if stored.casefold() != expected.casefold():
        raise CredentialManagerError(
            "The stored connector mailbox does not match the requested address. Nothing was deleted."
        )
    if not target_api.CredDeleteW(TARGET_NAME, CRED_TYPE_GENERIC, 0):
        code = _last_error(target_api)
        if code == ERROR_NOT_FOUND:
            raise CredentialManagerError(
                "Mail.ru credentials are not configured in Windows Credential Manager."
            )
        raise CredentialManagerError(
            "Windows Credential Manager could not delete the credential (error %d)." % code
        )
