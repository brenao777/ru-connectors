"""Copy shared sources into the self-contained macOS plugin package."""
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "plugins"
SHARED = (
    "server/yandex_mail_mcp.py",
    "scripts/check_connection.py",
    "scripts/read_attachment_file.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    for relative in SHARED:
        original = (ROOT / "yandex-mail-windows" / relative).read_bytes()
        output = ROOT / "yandex-mail-macos" / relative
        if check:
            if output.read_bytes() != original:
                raise SystemExit(f"Outdated macOS copy: {relative}")
        else:
            output.write_bytes(original)


if __name__ == "__main__":
    main()
