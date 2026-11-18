"""Read-only client for the Yandex Disk REST API."""

from contextlib import contextmanager, suppress
import http.client
import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ROOT = "https://cloud-api.yandex.net/v1/disk"
TIMEOUT = 12
METADATA_LIMIT = 2 * 1024 * 1024
DOWNLOAD_LIMIT = 50 * 1024 * 1024
TEXT_BYTES_LIMIT = 1024 * 1024
TEXT_CHARS_LIMIT = 12_000
SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 3
RESOURCE_KEYS = ("name", "path", "type", "size", "mime_type", "created", "modified")
RESOURCE_FIELDS = ",".join(RESOURCE_KEYS)
REDIRECT_CODES = {301, 302, 303, 307, 308}
HTTP_MESSAGES = {
    401: "Яндекс Диск не принял OAuth-токен. Получите новый токен и повторите настройку.",
    403: "Яндекс Диск отказал в доступе. Проверьте право cloud_api:disk.read и доступ к файлу.",
    404: "Файл или папка по этому пути не найдены.",
    429: "Яндекс Диск ограничил частоту запросов. Повторите запрос позже.",
}
NETWORK_ERRORS = (URLError, OSError, http.client.HTTPException)


class DiskError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_~.+/=-]{1,2048}", token):
        raise DiskError("Вставьте только значение OAuth-токена со страницы Яндекса.")


def http_error(code):
    message = HTTP_MESSAGES.get(code, f"Яндекс Диск вернул ошибку HTTP {code}.")
    return DiskError(message)


def download_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        allowed = host in {
            "downloader.dst.yandex.ru", "downloader.disk.yandex.ru", "downloader.disk.yandex.com",
        } or host.endswith(".storage.yandex.net")
        if (parts.scheme == "https" and allowed and parts.port in (None, 443)
                and parts.username is None and parts.password is None and not parts.fragment):
            return url
    except ValueError:
        pass
    raise DiskError("Яндекс Диск вернул недопустимый адрес для скачивания.")


def metadata(resource):
    return {key: resource[key] for key in RESOURCE_KEYS if key in resource}


def sized_chunks(response, maximum):
    content_length = response.headers.get("Content-Length")
    declared = int(content_length) if content_length is not None and content_length.isdecimal() else None
    if declared is not None and declared > maximum:
        raise DiskError("Размер файла превышает лимит этого инструмента.")
    total = 0
    try:
        while chunk := response.read(min(64 * 1024, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise DiskError("Размер файла превышает лимит этого инструмента.")
            yield chunk
    except NETWORK_ERRORS:
        raise DiskError("Не удалось завершить скачивание файла с Яндекс Диска.") from None
    if declared is not None and total != declared:
        raise DiskError("Скачивание прервалось. Яндекс Диск вернул неполный файл.")


class DiskClient:
    def __init__(self, token):
        validate_token(token)
        self.auth = {"Authorization": f"OAuth {token}"}
        self.opener = build_opener(NoRedirect())

    def get(self, endpoint, **params):
        url = f"{API_ROOT}{endpoint}?{urlencode(params)}"
        request = Request(url, headers={
            **self.auth, "Accept": "application/json", "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                payload = response.read(METADATA_LIMIT + 1)
            if len(payload) > METADATA_LIMIT:
                raise DiskError("Ответ Яндекс Диска слишком большой.")
            data = json.loads(payload)
        except HTTPError as error:
            error.close()
            raise http_error(error.code) from None
        except NETWORK_ERRORS:
            raise DiskError("Не удалось связаться с Яндекс Диском. Проверьте соединение и повторите запрос.") from None
        except ValueError:
            raise DiskError("Яндекс Диск вернул ответ в неожиданном формате.") from None
        if not isinstance(data, dict):
            raise DiskError("Яндекс Диск вернул ответ в неожиданном формате.")
        return data

    def check_connection(self):
        root = self.get("/resources", path="disk:/", limit=1, fields="type")
        if root.get("type") != "dir":
            raise DiskError("Яндекс Диск не вернул корневую папку.")
        return {"connected": True}

    def get_metadata(self, path):
        resource = self.get("/resources", path=path, fields=RESOURCE_FIELDS)
        if resource.get("type") not in ("dir", "file"):
            raise DiskError("Яндекс Диск вернул неизвестный тип ресурса.")
        return metadata(resource)

    def list_folder(self, path, limit, offset):
        fields = ["type", "path", "_embedded.total", "_embedded.limit"]
        fields.extend(f"_embedded.items.{key}" for key in RESOURCE_KEYS)
        folder = self.get("/resources", path=path, limit=limit, offset=offset, fields=",".join(fields))
        if folder.get("type") != "dir":
            raise DiskError("По указанному пути находится файл. Укажите путь к папке.")
        listing = folder.get("_embedded") or {}
        items = listing.get("items", [])
        total = listing.get("total")
        next_offset = offset + len(items)
        has_more = (next_offset < total if isinstance(total, int)
                    else len(items) >= listing.get("limit", limit))
        return {
            "path": folder.get("path", path),
            "items": [metadata(item) for item in items],
            "total": total,
            "next_offset": next_offset if items and has_more else None,
        }

    def search_files(self, query, limit, offset):
        matches = []
        scanned = 0
        needle = query.casefold()
        fields = "limit," + ",".join(f"items.{key}" for key in RESOURCE_KEYS)
        for _ in range(SEARCH_MAX_PAGES):
            data = self.get("/resources/files", limit=SEARCH_PAGE_SIZE,
                            offset=offset + scanned, fields=fields)
            items = data["items"]
            for index, item in enumerate(items, start=1):
                if needle in item["name"].casefold():
                    matches.append(metadata(item))
                    if len(matches) == limit:
                        scanned += index
                        has_more = index < len(items) or len(items) >= data.get("limit", SEARCH_PAGE_SIZE)
                        return self._search_result(matches, scanned, offset, has_more)
            scanned += len(items)
            if not items or len(items) < data.get("limit", SEARCH_PAGE_SIZE):
                return self._search_result(matches, scanned, offset, False)
        return self._search_result(matches, scanned, offset, True)

    @staticmethod
    def _search_result(matches, scanned, offset, has_more):
        return {
            "items": matches, "scanned": scanned,
            "next_offset": offset + scanned if has_more else None,
        }

    @contextmanager
    def file_stream(self, resource, path, maximum):
        if resource["type"] != "file":
            raise DiskError("По указанному пути находится папка. Укажите путь к файлу.")
        size = resource.get("size")
        if isinstance(size, int) and size > maximum:
            raise DiskError("Размер файла превышает лимит этого инструмента.")
        link = self.get("/resources/download", path=path)
        if link.get("method") != "GET" or link.get("templated") or not isinstance(link.get("href"), str):
            raise DiskError("Яндекс Диск не вернул готовый адрес для скачивания.")
        url = download_url(link["href"])
        headers = {**self.auth, "Accept-Encoding": "identity"}
        for _ in range(4):
            request = Request(url, headers=headers)
            try:
                response = self.opener.open(request, timeout=TIMEOUT)
            except HTTPError as error:
                with error:
                    location = error.headers.get("Location")
                    if error.code not in REDIRECT_CODES or not location:
                        raise http_error(error.code) from None
                url = download_url(urljoin(url, location))
                # Yandex redirects to signed storage URLs. Keep OAuth on the first request only.
                headers.pop("Authorization", None)
                continue
            except NETWORK_ERRORS:
                raise DiskError("Не удалось завершить скачивание файла с Яндекс Диска.") from None
            with response:
                if response.status != 200:
                    raise http_error(response.status)
                yield sized_chunks(response, maximum)
            return
        raise DiskError("Слишком много перенаправлений при скачивании файла.")

    def download_file(self, path):
        resource = self.get_metadata(path)
        suffix = Path(resource.get("name", "")).suffix
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", suffix):
            suffix = ""
        local_path = None
        size = 0
        complete = False
        try:
            with self.file_stream(resource, path, DOWNLOAD_LIMIT) as chunks:
                with tempfile.NamedTemporaryFile(prefix="ru-yandex-disk-", suffix=suffix, delete=False) as file:
                    local_path = Path(file.name)
                    for chunk in chunks:
                        file.write(chunk)
                        size += len(chunk)
            complete = True
        except OSError:
            raise DiskError("Не удалось сохранить файл во временную папку macOS.") from None
        finally:
            if not complete and local_path is not None:
                with suppress(OSError):
                    local_path.unlink(missing_ok=True)
        return {"file": resource, "local_path": str(local_path), "bytes": size}

    def read_text(self, path, encoding):
        resource = self.get_metadata(path)
        mime = resource.get("mime_type") or ""
        suffix = Path(resource.get("name", "")).suffix.lower()
        if resource["type"] != "file" or not (mime.startswith("text/") or suffix in {
            ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".log",
        }):
            raise DiskError("Укажите текстовый файл. Для остальных форматов используйте download_file.")
        with self.file_stream(resource, path, TEXT_BYTES_LIMIT) as chunks:
            raw = b"".join(chunks)
        try:
            content = raw.decode(encoding)
        except UnicodeDecodeError:
            raise DiskError("Файл не читается в выбранной кодировке. Попробуйте другую кодировку или скачайте файл.") from None
        if "\x00" in content:
            raise DiskError("Файл содержит двоичные данные. Используйте download_file.")
        return {"file": resource, "text": content[:TEXT_CHARS_LIMIT],
                "truncated": len(content) > TEXT_CHARS_LIMIT}
