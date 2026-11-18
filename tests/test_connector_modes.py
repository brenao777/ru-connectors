"""Drive the macOS launcher's setup and check modes with stub provider scripts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "ru-connectors"
STUB = '''import sys
print({report!r})
sys.exit({code})
'''
DIALOG = '''from pathlib import Path
(Path(__file__).resolve().parent / "dialog-opened").write_text("1")
'''
# The probe the launcher runs against each Python candidate. In setup mode a
# launcher that finds no usable Python installs one with Homebrew, so the test
# hands it an interpreter it accepts and skips rather than start an install.
PROBE = ("import imaplib, smtplib, sqlite3, ssl, sys, urllib.request; "
         "assert sys.version_info >= (3, 9); ssl.create_default_context()")


@unittest.skipUnless(sys.platform == "darwin", "the zsh launcher is used on macOS")
class LauncherModeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="коннектор "))
        self.addCleanup(shutil.rmtree, self.root, True)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copy2(PLUGIN / "scripts" / "connector.sh", scripts / "connector.sh")
        self.provider = self.root / "yandex-disk" / "scripts"
        self.provider.mkdir(parents=True)
        (self.root / "yandex-disk" / "server").mkdir()
        (self.provider / "setup_macos.py").write_text(DIALOG, encoding="utf-8")
        if subprocess.run([sys.executable, "-I", "-c", PROBE], capture_output=True).returncode:
            self.skipTest("this interpreter does not satisfy the launcher's Python probe")
        local_bin = self.root / "bin"
        local_bin.mkdir()
        (local_bin / "python3").symlink_to(sys.executable)
        self.environment = {**os.environ, "PATH": f"{local_bin}:{os.environ.get('PATH', '')}"}

    def write_check(self, payload, code):
        (self.provider / "check_connection.py").write_text(
            STUB.format(report=json.dumps(payload), code=code), encoding="utf-8",
        )

    def run_mode(self, *arguments):
        return subprocess.run(
            ["/bin/zsh", str(self.root / "scripts" / "connector.sh"), "yandex-disk", *arguments],
            capture_output=True, text=True, timeout=120, env=self.environment,
        )

    def test_setup_opens_the_dialog_only_when_no_token_is_stored(self):
        self.write_check({"ready": False, "reason": "credentials_unavailable"}, 3)
        result = self.run_mode("setup")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertTrue((self.provider / "dialog-opened").exists())
        # The pre-check report stays hidden: only the verdict after setup is printed.
        self.assertEqual(json.loads(result.stdout), {"ready": False, "reason": "credentials_unavailable"})

        (self.provider / "dialog-opened").unlink()
        self.write_check({"read": True, "write": True, "ready": True}, 0)
        result = self.run_mode("setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.provider / "dialog-opened").exists())
        self.assertEqual(json.loads(result.stdout)["ready"], True)

    def test_reconnect_asks_for_a_new_token_and_check_reports_the_verdict(self):
        self.write_check({"read": True, "write": False, "ready": False}, 4)
        result = self.run_mode("setup", "--reconnect")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertTrue((self.provider / "dialog-opened").exists())

        (self.provider / "dialog-opened").unlink()
        result = self.run_mode("check")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"read": True, "write": False, "ready": False})
        self.assertFalse((self.provider / "dialog-opened").exists())

    def test_unknown_provider_and_mode_are_refused(self):
        self.write_check({"ready": True}, 0)
        self.assertEqual(self.run_mode("teleport").returncode, 64)
        unknown = subprocess.run(
            ["/bin/zsh", str(self.root / "scripts" / "connector.sh"), "dropbox", "check"],
            capture_output=True, text=True, timeout=60, env=self.environment,
        )
        self.assertEqual(unknown.returncode, 64)


if __name__ == "__main__":
    unittest.main()
