#!/usr/bin/env python3
"""Delete the saved Yandex Disk token from the native credential store."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from credentials import delete_token
from disk_api import DiskError


def main():
    try:
        delete_token()
    except DiskError as error:
        print(json.dumps({"disconnected": False, "reason": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"disconnected": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
