import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit


CONNECTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONNECTOR))
import credentials
from disk_api import DiskClient, DiskError
import yandex_disk_mcp as mcp


PATH = "disk:/Учебные + файлы/Отчёт #1.txt"
RESOURCE = {"path": PATH, "name": "Отчёт #1.txt", "type": "file", "mime_type": "text/plain"}
TOKEN = "synthetic-test-token"
LINK = {"href": "https://downloader.dst.yandex.ru/disk/token?secret=1",
        "method": "GET", "templated": False}


class Response(io.BytesIO):
    status = 200

    def __init__(self, value, headers=None):
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False).encode("utf-8")
        super().__init__(value)
        self.headers = headers or {}


class DiskClientTests(unittest.TestCase):
    def setUp(self):
        self.client = DiskClient(TOKEN)

    def test_check_connection_uses_only_resource_access(self):
        with patch.object(self.client.opener, "open", return_value=Response({"type": "dir"})) as request:
            self.assertEqual(self.client.check_connection(), {"connected": True})
        sent = request.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(sent.full_url).query), {
            "path": ["disk:/"], "limit": ["1"], "fields": ["type"],
        })

    def test_metadata_encodes_cyrillic_and_excludes_private_urls(self):
        with patch.object(self.client.opener, "open", return_value=Response({**RESOURCE, "file": LINK["href"]})) as request:
            self.assertEqual(self.client.get_metadata(PATH), RESOURCE)
        sent = request.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(sent.full_url).query)["path"], [PATH])
        self.assertEqual(sent.get_method(), "GET")
        self.assertEqual(sent.get_header("Authorization"), f"OAuth {TOKEN}")

    def test_folder_pagination_and_empty_folder(self):
        folder = {"type": "dir", "path": "disk:/", "_embedded": {"items": [RESOURCE], "total": 3}}
        with patch.object(self.client, "get", side_effect=[folder, {"type": "dir"}]):
            self.assertEqual(self.client.list_folder("disk:/", 1, 1), {
                "path": "disk:/", "items": [RESOURCE], "total": 3, "next_offset": 2,
            })
            self.assertIsNone(self.client.list_folder("disk:/empty", 20, 0)["next_offset"])

    def test_search_can_resume_mid_page_without_skipping_a_match(self):
        other = {**RESOURCE, "name": "Презентация.pptx"}
        first = {"items": [other, RESOURCE, RESOURCE], "limit": 3}
        second = {"items": [RESOURCE], "limit": 3}
        with patch.object(self.client, "get", side_effect=[first, second]) as request:
            result = self.client.search_files("ОТЧЁТ", 1, 10)
            self.assertEqual(result, {"items": [RESOURCE], "scanned": 2, "next_offset": 12})
            result = self.client.search_files("ОТЧЁТ", 1, result["next_offset"])
            self.assertEqual(result["items"], [RESOURCE])
            self.assertIsNone(result["next_offset"])
        self.assertEqual([call.kwargs["offset"] for call in request.call_args_list], [10, 12])

    def test_empty_search_batch_can_resume(self):
        other = {**RESOURCE, "name": "Презентация.pptx"}
        with patch.object(self.client, "get", return_value={"items": [other] * 100, "limit": 100}) as request:
            self.assertEqual(self.client.search_files("итоги", 20, 5), {
                "items": [], "scanned": 300, "next_offset": 305,
            })
        self.assertEqual([call.kwargs["offset"] for call in request.call_args_list], [5, 105, 205])

    def test_text_download_uses_signed_redirect_without_forwarding_oauth(self):
        redirect = HTTPError(LINK["href"], 302, "Found",
                             {"Location": "https://example.storage.yandex.net/file?signed=1"}, None)
        response = Response("Привет".encode("cp1251"))
        with patch.object(self.client.opener, "open", side_effect=[
            Response(RESOURCE), Response(LINK), redirect, response,
        ]) as request:
            self.assertEqual(self.client.read_text(PATH, "cp1251")["text"], "Привет")
        sent = [call.args[0] for call in request.call_args_list]
        self.assertEqual(sent[2].get_header("Authorization"), f"OAuth {TOKEN}")
        self.assertIsNone(sent[3].get_header("Authorization"))

    def test_non_yandex_download_or_redirect_is_blocked(self):
        bad_url = "https://downloader.dst.yandex.ru.example.com/disk/token"
        redirect = HTTPError(LINK["href"], 302, "Found", {"Location": bad_url}, None)
        for replies in ([Response({**LINK, "href": bad_url})], [Response(LINK), redirect]):
            with self.subTest(requests=len(replies)):
                with patch.object(self.client.opener, "open", side_effect=[Response(RESOURCE), *replies]) as request:
                    with self.assertRaisesRegex(DiskError, "недопустимый адрес"):
                        self.client.read_text(PATH, "utf-8")
                    self.assertEqual(request.call_count, len(replies) + 1)

    def test_api_errors_do_not_expose_upstream_body_or_url(self):
        for status in (401, 403, 404, 429, 503):
            with self.subTest(status=status):
                error = HTTPError(LINK["href"], status, TOKEN, {}, io.BytesIO(TOKEN.encode()))
                with patch.object(self.client.opener, "open", side_effect=error):
                    with self.assertRaises(DiskError) as caught:
                        self.client.check_connection()
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn(LINK["href"], str(caught.exception))

    def test_binary_file_download_preserves_bytes_and_safe_extension(self):
        payload = b"PK\x03\x04" + bytes(range(256))
        resource = {**RESOURCE, "name": "Книга.xlsx"}
        with tempfile.TemporaryDirectory() as destination:
            with patch("tempfile.tempdir", destination), patch.object(
                self.client.opener, "open", side_effect=[Response(resource), Response(LINK), Response(payload)]
            ):
                result = self.client.download_file(PATH)
            saved = Path(result["local_path"])
            self.assertEqual(saved.suffix, ".xlsx")
            self.assertEqual(result["bytes"], len(payload))
            self.assertEqual(saved.read_bytes(), payload)
            self.assertEqual(saved.stat().st_mode & 0o077, 0)

    def test_oversize_or_incomplete_download_cleans_up_partial_file(self):
        examples = [
            (Response(b"too big"), "превышает"),
            (Response(b"ab", {"Content-Length": "3"}), "неполный"),
        ]
        with tempfile.TemporaryDirectory() as destination, patch("tempfile.tempdir", destination):
            for response, error in examples:
                with self.subTest(error=error), patch("disk_api.DOWNLOAD_LIMIT", 4), patch.object(
                    self.client.opener, "open", side_effect=[Response(RESOURCE), Response(LINK), response]
                ):
                    with self.assertRaisesRegex(DiskError, error):
                        self.client.download_file(PATH)
                    self.assertEqual(list(Path(destination).iterdir()), [])


class MCPSmokeTests(unittest.TestCase):
    def test_stdio_exposes_tools_and_recovers_from_bad_input(self):
        requests = [
            b"invalid\n",
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n',
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_folder","arguments":{"limit":true}}}\n',
        ]
        run = subprocess.run([sys.executable, str(CONNECTOR / "yandex_disk_mcp.py")],
                             input=b"".join(requests), capture_output=True, check=True, timeout=10)
        malformed, initialize, tools, bad_limit = map(json.loads, run.stdout.splitlines())
        self.assertEqual(malformed["error"]["code"], -32700)
        self.assertEqual(initialize["result"]["serverInfo"]["name"], "yandex-disk")
        self.assertEqual(initialize["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({item["name"] for item in tools["result"]["tools"]}, {
            "check_connection", "list_folder", "get_metadata", "search_files", "read_text", "download_file",
        })
        self.assertTrue(bad_limit["result"]["isError"])
        self.assertIn("пределы", bad_limit["result"]["content"][0]["text"])
        self.assertEqual(run.stderr, b"")

    def test_tool_call_returns_russian_document_data(self):
        with patch.object(mcp, "read_token", return_value=TOKEN), patch.object(
            DiskClient, "get", return_value=RESOURCE,
        ):
            result = mcp.call_tool({"name": "get_metadata", "arguments": {"path": PATH}})
        self.assertEqual(result["structuredContent"], RESOURCE)
        self.assertIn("Отчёт", result["content"][0]["text"])

    def test_keychain_write_passes_token_via_stdin_only(self):
        result = subprocess.CompletedProcess([], 0, stdout=TOKEN)
        with patch.object(credentials, "security", return_value=result) as security:
            credentials.write_token(TOKEN)
        self.assertEqual(security.call_args_list[0].args, ("-i",))
        self.assertIn(TOKEN, security.call_args_list[0].kwargs["command_input"])
        self.assertNotIn(TOKEN, security.call_args_list[1].args)


if __name__ == "__main__":
    unittest.main()
