"""Client for the Yandex Disk REST API."""

from contextlib import contextmanager, suppress
import http.client
import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


API_ROOT = "https://cloud-api.yandex.net/v1/disk"
TIMEOUT = 12
UPLOAD_TIMEOUT = 75
METADATA_LIMIT = 2 * 1024 * 1024
DOWNLOAD_LIMIT = 50 * 1024 * 1024
UPLOAD_LIMIT = 50 * 1024 * 1024
UPLOAD_TEXT_LIMIT = 1024 * 1024
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
    409: "На Диске уже есть ресурс по указанному пути или не найдена родительская папка.",
    413: "Яндекс Диск отклонил файл из-за размера.",
    423: "Яндекс Диск временно ограничил операции записи.",
    429: "Яндекс Диск ограничил частоту запросов. Повторите запрос позже.",
    507: "На Яндекс Диске недостаточно места.",
}
NETWORK_ERRORS = (URLError, OSError, http.client.HTTPException)


class DiskError(RuntimeError):
    pass


class DiskHTTPError(DiskError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_~.+/=-]{1,2048}", token):
        raise DiskError("Вставьте только значение OAuth-токена со страницы Яндекса.")


def http_error(code, write=False):
    message = HTTP_MESSAGES.get(code, f"Яндекс Диск вернул ошибку HTTP {code}.")
    if code == 403 and write:
        message = "Яндекс Диск отказал в записи. Проверьте право cloud_api:disk.write, доступ к папке и свободное место."
    return DiskHTTPError(code, message)


def transfer_url(url, upload=False):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if upload:
            allowed = re.fullmatch(
                r"uploader[a-z0-9-]*\.(?:dst\.yandex\.(?:net|ru)|disk\.yandex\.(?:ru|com))", host,
            )
        else:
            allowed = host in {
                "downloader.dst.yandex.ru", "downloader.disk.yandex.ru", "downloader.disk.yandex.com",
            } or host.endswith(".storage.yandex.net")
        if (parts.scheme == "https" and allowed and parts.port in (None, 443)
                and parts.username is None and parts.password is None and not parts.fragment):
            return url
    except ValueError:
        pass
    action = "загрузки" if upload else "скачивания"
    raise DiskError(f"Яндекс Диск вернул недопустимый адрес для {action}.")


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
        return self.request_json(endpoint, **params)

    def request_json(self, endpoint, *, method="GET", expected_status=200, write=False, **params):
        url = f"{API_ROOT}{endpoint}?{urlencode(params)}"
        request = Request(url, method=method, headers={
            **self.auth, "Accept": "application/json", "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                if response.status != expected_status:
                    raise http_error(response.status, write=write)
                payload = response.read(METADATA_LIMIT + 1)
            if len(payload) > METADATA_LIMIT:
                raise DiskError("Ответ Яндекс Диска слишком большой.")
            data = json.loads(payload)
        except HTTPError as error:
            error.close()
            raise http_error(error.code, write=write) from None
        except NETWORK_ERRORS:
            if method == "PUT":
                raise DiskError("Яндекс Диск не подтвердил результат. Проверьте путь перед повтором.") from None
            raise DiskError("Не удалось связаться с Яндекс Диском. Проверьте соединение и повторите запрос.") from None
        except ValueError:
            raise DiskError("Яндекс Диск вернул ответ в неожиданном формате.") from None
        if not isinstance(data, dict):
            raise DiskError("Яндекс Диск вернул ответ в неожиданном формате.")
        return data

    def check_connection(self, check_write=False):
        root = self.get("/resources", path="disk:/", limit=1, fields="type")
        if root.get("type") != "dir":
            raise DiskError("Яндекс Диск не вернул корневую папку.")
        if check_write:
            self.upload_link(f"disk:/ru-connectors-write-check-{uuid4().hex}.txt", overwrite=False)
        return {"connected": True, **({"write_access": True} if check_write else {})}

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
        url = transfer_url(link["href"])
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
                url = transfer_url(urljoin(url, location))
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
            raise DiskError("Не удалось сохранить файл во временную папку.") from None
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

    def create_folder(self, path):
        try:
            self.request_json("/resources", method="PUT", expected_status=201, write=True, path=path)
        except DiskHTTPError as error:
            if error.code != 409:
                raise
            try:
                existing = self.get_metadata(path)
            except DiskError:
                raise error from None
            if existing["type"] != "dir":
                raise error
            return {"path": path, "created": False}
        return {"path": path, "created": True}

    def upload_link(self, path, overwrite):
        link = self.request_json("/resources/upload", write=True, path=path,
                                 overwrite="true" if overwrite else "false")
        if link.get("method") != "PUT" or link.get("templated") or not isinstance(link.get("href"), str):
            raise DiskError("Яндекс Диск не вернул готовый адрес для загрузки.")
        operation_id = link.get("operation_id")
        if not isinstance(operation_id, str) or not valid_operation_id(operation_id):
            operation_id = None
        return transfer_url(link["href"], upload=True), operation_id

    def upload_bytes(self, path, payload, overwrite):
        url, operation_id = self.upload_link(path, overwrite)
        request = Request(url, data=payload, method="PUT",
                          headers={"Content-Type": "application/octet-stream"})
        try:
            with self.opener.open(request, timeout=UPLOAD_TIMEOUT) as response:
                code = response.status
        except HTTPError as error:
            error.close()
            code = error.code
        except NETWORK_ERRORS:
            code = None

        result = {"path": path, "bytes": len(payload), "operation_id": operation_id}
        if code in (201, 202):
            return {**result, "status": "completed" if code == 201 else "accepted"}
        if code is None or 500 <= code < 600:
            return {**result, "status": "unknown",
                    "message": "Яндекс Диск не подтвердил результат. Проверьте операцию или файл перед повтором."}
        raise http_error(code, write=True)

    def upload_file(self, path, local_path, overwrite):
        source = Path(local_path)
        try:
            if not source.is_absolute() or not source.is_file():
                raise DiskError("Укажите абсолютный путь к локальному файлу.")
            with source.open("rb") as file:
                payload = file.read(UPLOAD_LIMIT + 1)
        except OSError:
            raise DiskError("Не удалось прочитать локальный файл.") from None
        if len(payload) > UPLOAD_LIMIT:
            raise DiskError("Размер файла превышает 50 МиБ.")
        return self.upload_bytes(path, payload, overwrite)

    def upload_text(self, path, text, overwrite):
        try:
            payload = text.encode("utf-8")
        except UnicodeError:
            raise DiskError("Текст содержит символы, которые не удалось записать в UTF-8.") from None
        if len(payload) > UPLOAD_TEXT_LIMIT:
            raise DiskError("Размер текста в UTF-8 превышает 1 МиБ.")
        return self.upload_bytes(path, payload, overwrite)

    def get_upload_status(self, operation_id):
        if not valid_operation_id(operation_id):
            raise DiskError("Укажите идентификатор из результата загрузки.")
        operation = self.get(f"/operations/{quote(operation_id, safe='')}")
        status = operation.get("status")
        if status not in ("success", "failed", "in-progress"):
            raise DiskError("Яндекс Диск вернул неизвестный статус операции.")
        return {"operation_id": operation_id, "status": status}


def valid_operation_id(operation_id):
    return re.fullmatch(r"[A-Za-z0-9_-]{1,256}", operation_id) is not None
