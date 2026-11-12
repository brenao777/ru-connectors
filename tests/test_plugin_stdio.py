"""Exercise both MCP servers using the packaged launchers and no mailbox."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform in ("win32", "darwin"), "requires Windows or macOS")
class PluginStdioTests(unittest.TestCase):
    def test_published_launchers(self):
        source = Path(__file__).resolve().parents[1] / "plugins" / "ru-connectors"
        for config_name in ("mcp.codex.json", ".mcp.json"):
            config = json.loads((source / config_name).read_text(encoding="utf-8"))
            self.assertEqual(set(config["mcpServers"]), {"yandex-mail-safe", "mail-ru-readonly"})
            for name, mcp in config["mcpServers"].items():
                with self.subTest(config=config_name, server=name):
                    self.check_launcher(source, name, mcp)

    def check_launcher(self, source, name, mcp):
        command = [mcp["command"], *mcp["args"]]
        command = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(source)) for arg in command]
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        payload = "".join(json.dumps(request) + "\n" for request in requests).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="mail test ") as temporary:
            environment = os.environ.copy()
            if sys.platform == "win32":
                environment["LOCALAPPDATA"] = temporary
                marker = Path(temporary) / "Codex/yandex-mail-safe/runtime-python.txt"
                marker.parent.mkdir(parents=True)
                marker.write_text(sys.executable, encoding="utf-8")
            result = subprocess.run(
                command, cwd=source if mcp.get("cwd") == "." else temporary, env=environment,
                input=payload, capture_output=True, timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        hello, tool_list = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(hello["result"]["serverInfo"]["name"], name)
        names = {tool["name"] for tool in tool_list["result"]["tools"]}
        self.assertIn("read_message", names)
        if name == "yandex-mail-safe":
            self.assertTrue({"send_message", "get_send_status"} <= names)
        else:
            self.assertEqual(names, {"list_folders", "list_messages", "search_messages", "read_message"})
