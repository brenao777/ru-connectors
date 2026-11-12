"""Dependency verification with no real GUI, credentials, or network access."""
import importlib.util
from pathlib import Path
import sys
import ssl
import types
import unittest
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/ru-connectors/scripts'
spec = importlib.util.spec_from_file_location('probe_under_test', SCRIPTS / 'runtime_probe.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.ssl = types.SimpleNamespace(create_default_context=MagicMock(return_value=ssl.create_default_context()), CERT_REQUIRED=ssl.CERT_REQUIRED)
        self.real_import = p.importlib.import_module
        self.ensurepip = self.real_import("ensurepip")
        self.tk = MagicMock()
        self.api = MagicMock()
        self.ctypes = types.SimpleNamespace(WinDLL=MagicMock(return_value=self.api))

    def fake_import(self, name):
        if name == 'ssl':
            return self.ssl
        if name == 'tkinter':
            return self.tk
        if name == 'ctypes':
            return self.ctypes
        if name == 'ensurepip':
            return self.ensurepip
        return self.real_import(name)

    def test_complete_windows_runtime(self):
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.importlib, 'import_module', side_effect=self.fake_import):
            result = p.check_runtime()
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['pip_packages_required'], [])
        self.tk.Tk.return_value.withdraw.assert_called_once()
        self.tk.Tk.return_value.destroy.assert_called_once()
        self.ctypes.WinDLL.assert_called_once_with('Advapi32.dll', use_last_error=True)
        self.api.CredReadW.assert_not_called()
        self.api.CredWriteW.assert_not_called()
        self.api.CredDeleteW.assert_not_called()

    def test_missing_tkinter_is_not_ready(self):
        def import_without_tk(name):
            if name == 'tkinter':
                raise ModuleNotFoundError('tkinter missing')
            return self.fake_import(name)
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.importlib, 'import_module', side_effect=import_without_tk):
            result = p.check_runtime()
        self.assertFalse(result['ready'])
        self.assertFalse(result['checks']['tkinter'])
        self.assertEqual(result['errors']['tkinter'], 'ModuleNotFoundError')

    def test_importable_tk_with_broken_tcl_is_not_ready(self):
        self.tk.Tk.side_effect = RuntimeError('Tcl runtime missing')
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.importlib, 'import_module', side_effect=self.fake_import):
            result = p.check_runtime()
        self.assertFalse(result['ready'])
        self.assertTrue(result['checks']['tkinter'])
        self.assertFalse(result['checks']['tk_window'])

    def test_native_windows_required(self):
        with patch.object(p.sys, 'platform', 'linux'), patch.object(p.importlib, 'import_module', side_effect=self.fake_import):
            result = p.check_runtime()
        self.assertFalse(result['ready'])
        self.ctypes.WinDLL.assert_not_called()

    def test_old_python_is_not_ready(self):
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.sys, 'version_info', (3, 8, 10)), patch.object(p.importlib, 'import_module', side_effect=self.fake_import):
            result = p.check_runtime()
        self.assertFalse(result['checks']['python_39_or_newer'])
        self.assertFalse(result['ready'])

    def test_missing_sqlite_is_not_ready(self):
        def import_without_sqlite(name):
            if name == 'sqlite3':
                raise ModuleNotFoundError('sqlite missing')
            return self.fake_import(name)
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.importlib, 'import_module', side_effect=import_without_sqlite):
            result = p.check_runtime()
        self.assertFalse(result['ready'])
        self.assertFalse(result['checks']['sqlite3'])

    def test_disabled_tls_validation_is_not_ready(self):
        ssl = MagicMock()
        ssl.create_default_context.return_value.check_hostname = False
        def unsafe_import(name):
            return ssl if name == 'ssl' else self.fake_import(name)
        with patch.object(p.sys, 'platform', 'win32'), patch.object(p.importlib, 'import_module', side_effect=unsafe_import):
            result = p.check_runtime()
        self.assertFalse(result['ready'])
        self.assertFalse(result['checks']['tls_certificate_validation'])

    def test_installer_guard_explains_missing_component(self):
        with patch.object(p, 'check_runtime', return_value={'ready':False,'checks':{'tk_window':False}}):
            with self.assertRaisesRegex(RuntimeError, 'bootstrap_windows.ps1'):
                p.require_runtime()


if __name__ == '__main__':
    unittest.main()
