#!/usr/bin/env python3
"""Prompt for a Yandex Disk OAuth token and store it in Windows Credential Manager.

One field is collected: the OAuth token issued for an application with the
cloud_api:disk.read and cloud_api:disk.write permissions. The field is masked
(show="*") and the value is never printed, logged, or passed as a command-line
argument -- write_token() hands it to CredWriteW through a ctypes struct and
zeroes its local buffer afterwards.

The dialog block below is shared verbatim with the mail connectors of this
plugin.
"""

from pathlib import Path
import sys


# The Windows console code page is not always UTF-8 (it can be 866/1251), and
# this script prints Cyrillic text through SystemExit, which writes its message
# to stderr. Reconfigure both streams to UTF-8 explicitly, otherwise the text
# turns into mojibake or SystemExit raises UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from credentials import write_token  # noqa: E402
from disk_api import DiskClient, DiskError, validate_token  # noqa: E402


DIALOG_TITLE = "Яндекс Диск"


# --- Общий диалог ввода со вставкой из буфера -------------------------------
# Одинаковый блок продублирован во всех четырёх сборках коннекторов: общего
# модуля между скиллами нет, каждая сборка автономна.
#
# Зачем свой диалог вместо tkinter.simpledialog.askstring. На Windows Tk вешает
# виртуальное событие <<Paste>> на физические привязки <Control-Key-v>,
# <Shift-Key-Insert> и <Control-Lock-Key-V> (см. tk.tcl, ветка win32). Привязка
# сопоставляется по СИМВОЛУ клавиши, а не по её позиции: при русской раскладке
# та же клавиша даёт кириллическую «м», <Control-Key-v> не совпадает, и Ctrl+V
# молча не делает ничего. Сам по себе show="*" вставку не блокирует - Entry
# вставляет текст обычным insert независимо от маскировки.
#
# Поэтому здесь пять независимых путей вставки, чтобы хоть один сработал в
# любой раскладке:
#   1) кнопка «Вставить из буфера» - не зависит от клавиатуры вообще;
#   2) Ctrl+V, разобранный вручную по КОДУ клавиши (keycode 86 = VK_V на
#      Windows) и по кириллическим keysym;
#   3) Shift+Insert - keysym Insert одинаков в любой раскладке;
#   4) правая кнопка мыши → «Вставить»;
#   5) штатное <<Paste>> класса Entry, которое мы не ломаем.

_VK_V = 86  # VK_V: код клавиши V на Windows, одинаковый в любой раскладке
_PASTE_KEYSYMS = ("v", "м", "cyrillic_em")


def _present_modal(tk, window, focus_widget=None):
    """Показывает собранное модальное окно и забирает на него ввод.

    Порядок шагов одинаков для всех окон сборки и важен целиком:
    update_idletasks() - чтобы знать реальный размер окна; -topmost и
    центрирование по экрану - потому что окно не transient, а родитель скрыт,
    и сам Tk его никуда не поставит; wait_visibility() до grab_set() - захват
    ввода и перевод фокуса на ещё не показанное окно молча не срабатывают, и
    тогда окно открывается без фокуса: ни ввод, ни Ctrl+V, ни Shift+Insert до
    него не доходят.
    """
    window.update_idletasks()
    window.attributes("-topmost", True)
    # Без родителя окно нужно позиционировать самим, иначе оно уезжает в угол.
    x = max(0, (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2)
    y = max(0, (window.winfo_screenheight() - window.winfo_reqheight()) // 2)
    window.geometry("+%d+%d" % (x, y))
    window.deiconify()
    window.lift()
    try:
        window.wait_visibility()
    except tk.TclError:
        pass
    window.grab_set()
    window.focus_force()
    if focus_widget is not None:
        focus_widget.focus_set()


def _show_message(tk, root, show, title, message):
    """Показывает messagebox так, чтобы он не остался позади чужих окон.

    Нативный диалог Windows принадлежит окну-родителю. Пока root скрыт
    (withdraw), у диалога нет своей кнопки в панели задач и он может уехать за
    другие окна - та же беда, что была со вторым окном ввода. Поэтому на время
    показа делаем root настоящим окном, но размером 1x1 и полностью прозрачным:
    диалог получает живого родителя и выходит вперёд, а пустого окна человек не
    видит. Если -alpha не поддерживается, ничего не трогаем и показываем диалог
    как раньше.
    """
    restored = False
    try:
        root.attributes("-alpha", 0.0)
        root.geometry("1x1+0+0")
        root.deiconify()
        root.lift()
        root.update_idletasks()
        restored = True
    except tk.TclError:
        pass
    try:
        return show(title, message, parent=root)
    finally:
        if restored:
            try:
                root.withdraw()
                root.attributes("-alpha", 1.0)
            except tk.TclError:
                pass


def ask_value(tk, root, title, prompt, secret=False):
    """Модальное окно ввода одного значения.

    Возвращает введённую строку либо None, если человек нажал «Отмена», Esc
    или закрыл окно крестиком. При secret=True ввод маскируется звёздочками.
    """
    from tkinter import messagebox

    window = tk.Toplevel(root)
    window.title(title)
    window.resizable(False, False)
    # Не делаем окно transient для скрытого root. На Windows связанный с
    # withdrawn-родителем Toplevel иногда остаётся невидимым после закрытия
    # предыдущего модального окна: процесс продолжает ждать wait_window(), но
    # пользователю нечего открыть даже через панель задач.
    result = {"value": None}

    tk.Label(window, text=prompt, justify="left", anchor="w").pack(
        fill="x", padx=16, pady=(14, 8)
    )

    entry = tk.Entry(window, width=54, show="*" if secret else "")
    entry.pack(fill="x", padx=16)

    counter = tk.Label(window, text="Введено символов: 0", anchor="w", fg="#666666")
    counter.pack(fill="x", padx=16, pady=(4, 0))

    def refresh_counter(_event=None):
        # entry.index("end") даёт длину без создания копии секрета в Python.
        counter.config(text="Введено символов: %d" % entry.index("end"))

    def paste_from_clipboard(_event=None):
        try:
            data = window.clipboard_get()
        except tk.TclError:
            data = ""
        data = data.strip() if data else ""
        if not data:
            messagebox.showwarning(
                title,
                "Буфер обмена пуст или в нём не текст.\n"
                "Скопируйте значение ещё раз и повторите вставку.",
                parent=window,
            )
            entry.focus_set()
            return "break"
        try:
            if entry.selection_present():
                entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        entry.insert("insert", data)
        entry.focus_set()
        refresh_counter()
        # "break" останавливает разбор события дальше по bindtags, иначе на
        # латинской раскладке сработает ещё и штатное <<Paste>> класса Entry и
        # значение вставится дважды.
        return "break"

    def on_control_key(event):
        """Ctrl+<любая клавиша>: сами решаем, вставка это или нет.

        На Windows event.keycode - это виртуальный код клавиши, он не зависит
        от раскладки. Проверка по keysym оставлена запасной: разные версии Tk
        отдают для кириллической «м» то имя keysym (Cyrillic_em), то сам
        символ, то «??».
        """
        if event.keycode == _VK_V or (event.keysym or "").lower() in _PASTE_KEYSYMS:
            return paste_from_clipboard()
        return None

    entry.bind("<Control-KeyPress>", on_control_key)
    entry.bind("<Shift-Insert>", paste_from_clipboard)
    entry.bind("<Shift-KP_Insert>", paste_from_clipboard)
    entry.bind("<KeyRelease>", refresh_counter)

    menu = tk.Menu(window, tearoff=0)
    menu.add_command(label="Вставить из буфера", command=paste_from_clipboard)

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    entry.bind("<Button-3>", show_menu)
    entry.bind("<Button-2>", show_menu)

    def confirm(_event=None):
        result["value"] = entry.get()
        entry.delete(0, "end")  # не оставляем значение в виджете Tk
        window.destroy()
        return "break"

    def cancel(_event=None):
        result["value"] = None
        entry.delete(0, "end")
        window.destroy()
        return "break"

    buttons = tk.Frame(window)
    buttons.pack(fill="x", padx=16, pady=(10, 14))
    tk.Button(buttons, text="Вставить из буфера", command=paste_from_clipboard).pack(
        side="left"
    )
    tk.Button(buttons, text="ОК", width=10, command=confirm).pack(side="right")
    tk.Button(buttons, text="Отмена", width=10, command=cancel).pack(
        side="right", padx=(0, 8)
    )

    window.bind("<Return>", confirm)
    window.bind("<KP_Enter>", confirm)
    window.bind("<Escape>", cancel)
    window.protocol("WM_DELETE_WINDOW", cancel)

    _present_modal(tk, window, entry)
    root.wait_window(window)
    return result["value"]


def ask_text(tk, root, title, prompt):
    """Обычное поле: логин, идентификатор организации."""
    return ask_value(tk, root, title, prompt, secret=False)


def ask_secret(tk, root, title, prompt):
    """Скрытое поле: токен, пароль приложения."""
    return ask_value(tk, root, title, prompt, secret=True)


# --- конец общего блока ------------------------------------------------------


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Эта настройка требует нативный Windows, не WSL.")
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError as exc:
        raise SystemExit(
            "Требуется модуль tkinter. Переустановите Python с поддержкой Tcl/Tk "
            "(на python.org отметьте компонент 'tcl/tk and IDLE')."
        ) from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    token = ""
    try:
        token = ask_secret(
            tk,
            root,
            DIALOG_TITLE,
            "Вставьте OAuth-токен Яндекс Диска.\n"
            "Пароль от аккаунта вводить не нужно.",
        )
        if token is None:
            raise SystemExit(2)
        token = token.strip()
        if not token:
            _show_message(
                tk,
                root,
                messagebox.showerror,
                DIALOG_TITLE,
                "Токен не введён. Получите токен на странице Яндекс OAuth "
                "и повторите подключение.",
            )
            raise SystemExit(1)

        validate_token(token)
        # Reading the root confirms the token before it reaches the credential store.
        DiskClient(token).check_connection()
        write_token(token)
        token = ""
        _show_message(
            tk,
            root,
            messagebox.showinfo,
            DIALOG_TITLE,
            "Токен сохранён в Диспетчере учётных данных Windows. "
            "Теперь агент проверит доступ к Диску.",
        )
    except DiskError as exc:
        token = ""
        _show_message(tk, root, messagebox.showerror, DIALOG_TITLE, str(exc))
        raise SystemExit(1) from None
    finally:
        token = ""
        del token
        root.destroy()


if __name__ == "__main__":
    main()
