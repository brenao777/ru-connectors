# Почта Mail

Коннектор входит в плагин [ru-connectors](../../../README.md). Установите плагин и попросите агента «Подключи Почту Mail».

Сервер читает почту по TLS через `imap.mail.ru:993`. Он открывает папки в режиме чтения и использует IMAP BODY.PEEK. Содержимое вложений, отправка и черновики для Почты Mail не поддерживаются. Пароль внешнего приложения предоставляет доступ к ящику на стороне Mail. Его можно отозвать в [настройках Почты Mail](https://account.mail.ru/user/2-step-auth/passwords/).

Для удаления локального доступа удалите запись `Codex_MailRuReadonly` из Windows Credential Manager либо записи `org.openai.codex.mail-ru-readonly.v2` и `org.openai.codex.mail-ru-readonly` из Связки ключей macOS, если они есть.
