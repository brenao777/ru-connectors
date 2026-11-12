#!/usr/bin/env python3
"""Collect an app password locally, verify IMAP, then save it in macOS Keychain."""
import imaplib
import re
import ssl
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox

SERVICE = 'org.openai.codex.mail-ru-readonly'
root = tk.Tk()
root.title('Mail.ru: подключение к агенту')
root.geometry('540x260')
root.resizable(False, False)
frame = tk.Frame(root, padx=20, pady=16)
frame.pack(fill='both', expand=True)
tk.Label(frame, text='Подключение Почты Mail по IMAP', font=('Helvetica', 16, 'bold')).pack(anchor='w')
tk.Label(frame, text='Полный адрес почты').pack(anchor='w', pady=(12, 0))
address = tk.Entry(frame, width=55)
address.pack(fill='x')
tk.Label(frame, text='Пароль внешнего приложения Mail.ru').pack(anchor='w', pady=(8, 0))
password = tk.Entry(frame, width=55, show='•')
password.pack(fill='x')
status = tk.Label(frame, text='Пароль сохраняется в Связке ключей macOS, в чат он не передаётся.', wraplength=480, justify='left')
status.pack(anchor='w', pady=(8, 0))

def run_check(user, secret):
    try:
        client = imaplib.IMAP4_SSL('imap.mail.ru', 993, ssl_context=ssl.create_default_context(), timeout=20)
        try:
            client.login(user, secret)
            typ, _ = client.list()
            if typ != 'OK':
                raise RuntimeError('Не удалось получить список папок.')
        finally:
            try:
                client.logout()
            except Exception:
                pass
        cmd = f'add-generic-password -U -s "{SERVICE}" -a "{user}" -w "{secret}"\n'
        result = subprocess.run(['/usr/bin/security', '-i'], input=cmd, text=True, capture_output=True, timeout=60)
        # security -i may return 0 on command errors; check record metadata too.
        if result.returncode or 'error' in result.stderr.lower() or 'error' in result.stdout.lower():
            raise RuntimeError('Связка ключей не сохранила пароль.')
        subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', SERVICE, '-a', user], capture_output=True, check=True, timeout=15)
    except (imaplib.IMAP4.error, OSError, ssl.SSLError, RuntimeError, subprocess.SubprocessError):
        root.after(0, lambda: failure())
        return
    root.after(0, success)

def failure():
    status.config(text='Вход или сохранение не удалось. Проверьте пароль приложения и доступ IMAP.')
    button.config(state='normal')
    messagebox.showerror('Не удалось подключить', 'Проверьте адрес, пароль внешнего приложения и доступ к почте по IMAP. Пароль в чат не передавался.')

def success():
    password.delete(0, 'end')
    status.config(text='Подключение подтверждено, пароль сохранён в Связке ключей.')
    print('MAIL_RU_SETUP_OK', flush=True)
    messagebox.showinfo('Готово', 'Вход по IMAP выполнен, пароль сохранён в Связке ключей. Вернитесь к агенту для проверки.')
    root.destroy()

def start():
    user, secret = address.get().strip(), password.get()
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+', user):
        messagebox.showerror('Адрес', 'Введите полный адрес почты.')
        return
    if not secret or not re.fullmatch(r'[\x21-\x7e]+', secret) or any(c in secret for c in "\"'\\"):
        messagebox.showerror('Пароль', 'Введите пароль приложения без пробелов и кавычек.')
        return
    button.config(state='disabled')
    status.config(text='Проверяю соединение и сохраняю пароль...')
    threading.Thread(target=run_check, args=(user, secret), daemon=True).start()

button = tk.Button(frame, text='Проверить и подключить', command=start)
button.pack(anchor='e', pady=8)
address.focus_set()
root.mainloop()
