import ctypes
from contextlib import redirect_stdout, suppress
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


CONNECTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONNECTOR / "server"))
import credentials
from disk_api import DiskClient, DiskError
import windows_credentials
import yandex_disk_mcp as mcp


def load_script(name):
    """Load a setup script the way the launchers run it, without side effects."""
    spec = importlib.util.spec_from_file_location(
        "yandex_disk_" + name, CONNECTOR / "scripts" / (name + ".py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH = "disk:/Учебные + файлы/Отчёт #1.txt"
RESOURCE = {"path": PATH, "name": "Отчёт #1.txt", "type": "file", "mime_type": "text/plain"}
TOKEN = "synthetic-test-token"
LINK = {"href": "https://downloader.dst.yandex.ru/disk/token?secret=1",
        "method": "GET", "templated": False}
UPLOAD_LINK = {"href": "https://uploader1d.dst.yandex.net:443/upload-target/private",
               "method": "PUT", "templated": False, "operation_id": "op_1"}


class Response(io.BytesIO):
    def __init__(self, value, headers=None, status=200):
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False).encode("utf-8")
        super().__init__(value)
        self.headers = headers or {}
        self.status = status


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
            if sys.platform != "win32":
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

    def test_write_access_probe_does_not_write(self):
        replies = [Response({"type": "dir"}), Response(UPLOAD_LINK)]
        with patch.object(self.client.opener, "open", side_effect=replies) as request:
            self.assertEqual(self.client.check_connection(check_write=True),
                             {"connected": True, "write_access": True})
        requests = [call.args[0] for call in request.call_args_list]
        self.assertEqual([sent.get_method() for sent in requests], ["GET", "GET"])
        self.assertTrue(urlsplit(requests[1].full_url).path.endswith("/resources/upload"))

    def test_create_folder_put_and_retry_when_folder_exists(self):
        directory = "disk:/Учебные + файлы"
        conflict = HTTPError("", 409, "Conflict", {}, None)
        replies = [Response({}, status=201), conflict, Response({"type": "dir"})]
        with patch.object(self.client.opener, "open", side_effect=replies) as request:
            self.assertEqual(self.client.create_folder(directory), {"path": directory, "created": True})
            self.assertEqual(self.client.create_folder(directory), {"path": directory, "created": False})
        sent = request.call_args_list[0].args[0]
        self.assertEqual(sent.get_method(), "PUT")
        self.assertEqual(sent.get_header("Authorization"), f"OAuth {TOKEN}")
        self.assertEqual(parse_qs(urlsplit(sent.full_url).query)["path"], [directory])

    def test_upload_utf8_does_not_overwrite_by_default_or_send_token_to_uploader(self):
        replies = [Response(UPLOAD_LINK), Response(b"", status=201)]
        with patch.object(self.client.opener, "open", side_effect=replies) as request:
            result = self.client.upload_text(PATH, "Привет", overwrite=False)
        reserve, upload = [call.args[0] for call in request.call_args_list]
        self.assertEqual(reserve.get_method(), "GET")
        self.assertEqual(parse_qs(urlsplit(reserve.full_url).query), {"path": [PATH], "overwrite": ["false"]})
        self.assertEqual(reserve.get_header("Authorization"), f"OAuth {TOKEN}")
        self.assertEqual(upload.get_method(), "PUT")
        self.assertEqual(upload.data, "Привет".encode())
        self.assertIsNone(upload.get_header("Authorization"))
        self.assertNotIn(UPLOAD_LINK["href"], str(result))
        self.assertEqual(result["status"], "completed")

    def test_upload_can_report_accepted_or_unknown_and_check_operation(self):
        for reply, status in [(Response(b"", status=202), "accepted"), (URLError("private"), "unknown")]:
            with self.subTest(status=status):
                replies = [Response(UPLOAD_LINK), reply, Response({"status": "success"})]
                with patch.object(self.client.opener, "open", side_effect=replies) as request:
                    result = self.client.upload_text(PATH, "", overwrite=True)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(self.client.get_upload_status(result["operation_id"])["status"], "success")
                self.assertIn("overwrite=true", request.call_args_list[0].args[0].full_url)
                self.assertEqual(urlsplit(request.call_args.args[0].full_url).path, "/v1/disk/operations/op_1")

    def test_write_refusal_does_not_send_a_put(self):
        with patch.object(self.client.opener, "open", side_effect=HTTPError("", 403, "Forbidden", {}, None)) as request:
            with self.assertRaisesRegex(DiskError, "cloud_api:disk.write"):
                self.client.upload_text(PATH, "example", overwrite=False)
        self.assertEqual(request.call_count, 1)

    def test_upload_blocks_foreign_host_and_does_not_follow_redirect(self):
        invalid = {**UPLOAD_LINK, "href": "https://uploader1d.dst.yandex.net.example.com/payload"}
        redirect = HTTPError("", 307, "Redirect", {"Location": "https://example.com/keep"}, None)
        for replies, attempts in [([Response(invalid)], 1), ([Response(UPLOAD_LINK), redirect], 2)]:
            with self.subTest(attempts=attempts), patch.object(self.client.opener, "open", side_effect=replies) as request:
                with self.assertRaises(DiskError):
                    self.client.upload_text(PATH, "example", overwrite=False)
                self.assertEqual(request.call_count, attempts)

    def test_upload_local_file_preserves_bytes_and_rejects_large_or_relative_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Учебный файл.bin"
            source.write_bytes(b"\x00\xff")
            with patch.object(self.client, "upload_bytes") as upload:
                self.client.upload_file(PATH, str(source), overwrite=False)
                upload.assert_called_once_with(PATH, b"\x00\xff", False)
            with patch.object(self.client, "upload_bytes") as upload, patch("disk_api.UPLOAD_LIMIT", 1):
                with self.assertRaisesRegex(DiskError, "превышает"):
                    self.client.upload_file(PATH, str(source), overwrite=False)
                with self.assertRaisesRegex(DiskError, "абсолютный"):
                    self.client.upload_file(PATH, "file.bin", overwrite=False)
                upload.assert_not_called()


class MCPSmokeTests(unittest.TestCase):
    def test_stdio_exposes_tools_and_recovers_from_bad_input(self):
        requests = [
            b"invalid\n",
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n',
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_folder","arguments":{"limit":true}}}\n',
        ]
        run = subprocess.run([sys.executable, str(CONNECTOR / "server" / "yandex_disk_mcp.py")],
                             input=b"".join(requests), capture_output=True, check=True, timeout=10)
        malformed, initialize, tools, bad_limit = map(json.loads, run.stdout.splitlines())
        self.assertEqual(malformed["error"]["code"], -32700)
        self.assertEqual(initialize["result"]["serverInfo"]["name"], "yandex-disk")
        self.assertEqual(initialize["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({item["name"] for item in tools["result"]["tools"]}, {
            "check_connection", "list_folder", "get_metadata", "search_files", "read_text", "download_file",
            "create_folder", "upload_file", "upload_text", "get_upload_status",
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
        with patch.object(credentials.sys, "platform", "darwin"), patch.object(
            credentials, "security", return_value=result,
        ) as security:
            credentials.write_token(TOKEN)
        self.assertEqual(security.call_args_list[0].args, ("-i",))
        self.assertIn(TOKEN, security.call_args_list[0].kwargs["command_input"])
        self.assertNotIn(TOKEN, security.call_args_list[1].args)

    def test_upload_does_not_coerce_overwrite_to_boolean(self):
        arguments = {"path": PATH, "text": "", "overwrite": "false"}
        with patch.object(mcp, "read_token") as read_token:
            with self.assertRaisesRegex(DiskError, "true и false"):
                mcp.call_tool({"name": "upload_text", "arguments": arguments})
            read_token.assert_not_called()
        del arguments["overwrite"]
        self.assertIs(mcp.validated_arguments("upload_text", arguments)["overwrite"], False)


class SetupScriptTests(unittest.TestCase):
    def report(self, script, expected_code):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = script.main()
        self.assertEqual(code, expected_code)
        return json.loads(stream.getvalue())

    def test_check_reports_missing_token_separately_from_a_failed_probe(self):
        script = load_script("check_connection")
        with patch.object(script, "read_token", side_effect=DiskError("нет токена")):
            self.assertEqual(self.report(script, 3), {"ready": False, "reason": "credentials_unavailable"})
        with patch.object(script, "read_token", return_value=TOKEN), patch.object(
            DiskClient, "check_connection", side_effect=[{"connected": True}, DiskError("нет записи")],
        ):
            self.assertEqual(self.report(script, 4), {
                "read": True, "write": False, "reason": "нет записи", "ready": False,
            })
        with patch.object(script, "read_token", return_value=TOKEN), patch.object(
            DiskClient, "check_connection", return_value={"connected": True},
        ):
            self.assertEqual(self.report(script, 0), {"read": True, "write": True, "ready": True})

    def test_disconnect_reports_the_credential_store_verdict(self):
        script = load_script("disconnect")
        with patch.object(script, "delete_token") as delete:
            self.assertEqual(self.report(script, 0), {"disconnected": True})
        delete.assert_called_once_with()
        with patch.object(script, "delete_token", side_effect=DiskError("хранилище отказало")):
            self.assertEqual(self.report(script, 1), {"disconnected": False, "reason": "хранилище отказало"})

    @unittest.skipUnless(sys.platform == "darwin", "the macOS dialog is used for setup")
    def test_macos_setup_stores_only_a_verified_token(self):
        script = load_script("setup_macos")
        entered = subprocess.CompletedProcess([], 0, stdout=TOKEN + "\n")
        with patch.object(script.subprocess, "run", return_value=entered), patch.object(
            DiskClient, "check_connection", side_effect=DiskError("токен отклонён"),
        ), patch.object(script, "write_token") as write:
            with self.assertRaisesRegex(SystemExit, "отклонён"):
                script.main()
            write.assert_not_called()
        with patch.object(script.subprocess, "run", return_value=entered), patch.object(
            DiskClient, "check_connection", return_value={"connected": True},
        ), patch.object(script, "write_token") as write:
            script.main()
        write.assert_called_once_with(TOKEN)
        cancelled = subprocess.CompletedProcess([], 1, stdout="", stderr="User canceled. (-128)")
        with patch.object(script.subprocess, "run", return_value=cancelled), patch.object(
            script, "write_token",
        ) as write:
            with self.assertRaises(SystemExit) as cancel:
                script.main()
            self.assertEqual(cancel.exception.code, 2)
            write.assert_not_called()


class NativeSetupTests(unittest.TestCase):
    def test_windows_credential_marshalling(self):
        storage = {}

        class FakeAPI:
            def CredWriteW(self, pointer, flags):
                item = ctypes.cast(pointer, windows_credentials.PCREDENTIALW).contents
                storage[item.TargetName] = (
                    ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize), item.Persist,
                )
                return True

            def CredReadW(self, name, kind, flags, pointer):
                secret, _ = storage[name]
                self.blob = (ctypes.c_ubyte * len(secret)).from_buffer_copy(secret)
                self.item = windows_credentials.CREDENTIALW(CredentialBlob=self.blob, CredentialBlobSize=len(secret))
                address = ctypes.cast(pointer, ctypes.POINTER(windows_credentials.PCREDENTIALW))
                address[0] = ctypes.pointer(self.item)
                return True

            def CredFree(self, pointer):
                self.freed = True

            def CredDeleteW(self, name, kind, flags):
                del storage[name]
                return True

        api = FakeAPI()
        with patch.object(windows_credentials, "credential_api", return_value=api):
            windows_credentials.write_token(TOKEN)
            self.assertEqual(storage[windows_credentials.TARGET_NAME], (TOKEN.encode(), 2))
            self.assertEqual(windows_credentials.read_token(), TOKEN)
            self.assertTrue(api.freed)
            windows_credentials.delete_token()
        self.assertEqual(storage, {})

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "native credential store is required")
    def test_native_credential_store_round_trip(self):
        name = "ru-connectors.yandex-disk-test." + uuid4().hex
        token = "synthetic-test-" + uuid4().hex
        with patch.object(credentials, "SERVICE", name), patch.object(windows_credentials, "TARGET_NAME", name):
            try:
                credentials.write_token(token)
                self.assertEqual(credentials.read_token(), token)
            finally:
                with suppress(DiskError):
                    credentials.delete_token()
            with self.assertRaises(DiskError):
                credentials.read_token()


if __name__ == "__main__":
    unittest.main()
