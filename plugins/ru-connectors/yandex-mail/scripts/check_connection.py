"""Check IMAP and SMTP login without reading or sending messages."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import yandex_mail_mcp as mail


def main():
    try:
        username, password = mail.read_credential()
    except mail.CredentialManagerError:
        print(json.dumps({"ready": False, "reason": "credentials_unavailable"}))
        return 3

    result = {"imap": False, "smtp": False}
    try:
        client, _ = mail.connect(readonly=True, folder="INBOX")
        mail.close_client(client)
        result["imap"] = True
    except (mail.SafeMailError, mail.imaplib.IMAP4.error, OSError):
        pass

    result["smtp"] = mail.smtp.check_smtp(mail.SMTP_HOST, mail.SMTP_PORT, username, password)
    result["ready"] = result["imap"] and result["smtp"]
    print(json.dumps(result))
    return 0 if result["ready"] else 4


if __name__ == "__main__":
    sys.exit(main())
