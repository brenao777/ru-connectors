#!/usr/bin/env python3
"""Check connector dependencies without credentials or network requests."""
import importlib
import json
import sys

REQUIRED_MODULES = (
    'ssl', 'imaplib', 'smtplib', 'email', 'json', 'sqlite3', 'ctypes',
    'http.client', 'urllib.parse', 'urllib.request', 'xml.etree.ElementTree', 'tkinter',
    'venv', 'ensurepip', 'zipfile',
)


def check_runtime():
    checks = {'native_windows': sys.platform == 'win32', 'python_39_or_newer': sys.version_info >= (3, 9)}
    errors = {}
    modules = {}
    for name in REQUIRED_MODULES:
        try:
            modules[name] = importlib.import_module(name)
            checks[name] = True
        except Exception as exc:
            checks[name] = False
            errors[name] = type(exc).__name__
    if checks.get('ssl'):
        try:
            context = modules['ssl'].create_default_context()
            checks['tls_certificate_validation'] = context.check_hostname and context.verify_mode == modules['ssl'].CERT_REQUIRED
        except Exception as exc:
            checks['tls_certificate_validation'] = False
            errors['tls_certificate_validation'] = type(exc).__name__
    if checks.get('sqlite3'):
        try:
            database = modules['sqlite3'].connect(':memory:')
            try:
                database.execute('SELECT 1').fetchone()
            finally:
                database.close()
            checks['sqlite_runtime'] = True
        except Exception as exc:
            checks['sqlite_runtime'] = False
            errors['sqlite_runtime'] = type(exc).__name__
    if checks.get('tkinter'):
        root = None
        try:
            root = modules['tkinter'].Tk()
            root.withdraw()
            root.update_idletasks()
            checks['tk_window'] = True
        except Exception as exc:
            checks['tk_window'] = False
            errors['tk_window'] = type(exc).__name__
        finally:
            if root is not None:
                root.destroy()
    if checks['native_windows'] and checks.get('ctypes'):
        try:
            library = modules['ctypes'].WinDLL('Advapi32.dll', use_last_error=True)
            for name in ('CredReadW', 'CredWriteW', 'CredDeleteW', 'CredFree'):
                getattr(library, name)
            checks['windows_credential_api'] = True
        except Exception as exc:
            checks['windows_credential_api'] = False
            errors['windows_credential_api'] = type(exc).__name__
    return {
        'ready': all(checks.values()),
        'python_executable': sys.executable,
        'python_version': '.'.join(str(n) for n in sys.version_info[:3]),
        'checks': checks, 'errors': errors,
        'pip_packages_required': [],
    }


def require_runtime():
    result = check_runtime()
    if not result['ready']:
        missing = ', '.join(name for name, passed in result['checks'].items() if not passed)
        raise RuntimeError('Connector dependencies are not ready: ' + missing + '. Run scripts/bootstrap_windows.ps1 first.')
    return result


if __name__ == '__main__':
    result = check_runtime()
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(0 if result['ready'] else 10)
