#!/usr/bin/env python3
"""Check saved Yandex Disk access without creating or changing files."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from credentials import read_token
from disk_api import DiskClient, DiskError


def main():
    try:
        token = read_token()
    except DiskError:
        print(json.dumps({"ready": False, "reason": "credentials_unavailable"}, ensure_ascii=False))
        return 3

    result = {"read": False, "write": False}
    client = DiskClient(token)
    try:
        client.check_connection()
        result["read"] = True
        # Requests an upload address without sending any bytes.
        client.check_connection(check_write=True)
        result["write"] = True
    except DiskError as error:
        result["reason"] = str(error)
    result["ready"] = result["read"] and result["write"]
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ready"] else 4


if __name__ == "__main__":
    sys.exit(main())
