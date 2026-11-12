#!/usr/bin/env python3
"""Check Mail.ru login in read-only mode without reading messages."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import mail_ru_mcp as mail


def main():
    try:
        mail.load_credentials()
    except mail.SafeMailError:
        print(json.dumps({"ready": False, "reason": "credentials_unavailable"}))
        return 3

    try:
        client, _ = mail.connect(folder="INBOX")
        mail.close_client(client)
    except mail.SafeMailError:
        print(json.dumps({"ready": False, "imap": False}))
        return 4
    print(json.dumps({"ready": True, "imap": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
