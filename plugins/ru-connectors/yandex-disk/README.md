# Яндекс Диск

Коннектор входит в плагин [ru-connectors](../../../README.md). Установите плагин и попросите агента «Подключи Яндекс Диск».

Сервер работает с личным Диском подключённого аккаунта через [REST API Яндекс Диска](https://yandex.ru/dev/disk-api/doc/ru/concepts/quickstart) по HTTPS. Раздел «Общие диски» Яндекс 360 использует отдельный API и недоступен. Нужен OAuth-токен приложения с правами `cloud_api:disk.read` и `cloud_api:disk.write`. Токен вводится в локальном окне и хранится в Диспетчере учётных данных Windows или в Связке ключей macOS под именем `ru-connectors.yandex-disk`.

| Инструменты | Условия |
| --- | --- |
| `check_connection` | Читает корень Диска. При `check_write=true` запрашивает адрес для загрузки, не создавая файл |
| `list_folder`, `get_metadata`, `search_files` | Поиск сверяет часть имени без учёта регистра и просматривает до 300 имён за вызов. При наличии `next_offset` поиск можно продолжить |
| `read_text` | Текстовый файл до 1 МиБ в UTF-8 или Windows-1251, в ответе до 12 000 символов |
| `download_file` | Файл до 50 МиБ сохраняется во временную папку, в ответе `local_path` |
| `create_folder` | Родительская папка должна существовать |
| `upload_file`, `upload_text` | До 50 МиБ для локального файла и до 1 МиБ для текста в UTF-8. Существующие файлы сохраняются, пока пользователь не попросит замену |
| `get_upload_status` | Статус операции по `operation_id` после ответа `accepted` или `unknown` |

Загрузка и скачивание идут только на адреса Яндекса, а OAuth-токен не передаётся на серверы хранилища после первого перехода. Сообщения об ошибках не содержат подписанных ссылок и ответов API.

Проверить подключение, не открывая сессию агента, можно из каталога плагина. На macOS:

```sh
/bin/zsh scripts/connector.sh yandex-disk check
```

На Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\connector.ps1 -Provider yandex-disk -Mode Check
```

Отчёт содержит `read` и `write`. Замените `check` на `setup` (`-Mode Setup`), чтобы подключить Диск или ввести новый токен с `--reconnect` (`-Reconnect`), и на `disconnect` (`-Mode Disconnect`), чтобы удалить токен с компьютера. Доступ приложения отзывается в [Яндекс ID](https://id.yandex.ru/security).

Автоматические тесты не обращаются к сети и не читают токен:

```sh
python3 -m unittest discover -s tests -v
```
