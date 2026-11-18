"""Prepare project-local stdio MCP configuration for both clients."""

import json
from pathlib import Path
import sys

from disk_api import DiskError


def configure(project):
    server = project / "yandex-disk" / "yandex_disk_mcp.py"
    command = sys.executable
    args = ["-E", "-s", "-B", str(server)]
    codex = (
        "[mcp_servers.yandex-disk]\n"
        f"command = {json.dumps(command, ensure_ascii=False)}\n"
        f"args = {json.dumps(args, ensure_ascii=False)}\n"
        "startup_timeout_sec = 15\n"
        "tool_timeout_sec = 120\n"
    )
    claude = json.dumps({"mcpServers": {
        "yandex-disk": {"type": "stdio", "command": command, "args": args},
    }}, ensure_ascii=False, indent=2) + "\n"
    configs = {project / ".codex" / "config.toml": codex, project / ".mcp.json": claude}
    try:
        missing = []
        for path, content in configs.items():
            if not path.exists():
                missing.append((path, content))
            elif path.read_text(encoding="utf-8") != content:
                raise DiskError(f"Файл {path} уже содержит другие настройки. Конфигурация сохранена без изменений.")
        for path, content in missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
    except (OSError, UnicodeError):
        raise DiskError("Не удалось подготовить настройки Codex и Claude Code.") from None
