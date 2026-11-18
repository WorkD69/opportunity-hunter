"""Telegram-only secure desktop input. Credentials travel via SSH stdin, never argv or logs."""
from pathlib import Path
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
from vps import ssh_args, bot_username, preflight

ROOT=Path(__file__).resolve().parent.parent
LOG=ROOT/'reports/setup-events.jsonl'
COMMAND=('docker exec -i -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -B '
         '/opt/data/profiles/opportunity-hunter/operator/configure_telegram.py')
CODES={'TOKEN_MISSING_OR_INVALID','BOT_ASSIGNED_TO_OTHER_PROFILE','WRONG_BOT','BOT_HAS_WEBHOOK',
 'TOKEN_REJECTED','CHAT_FORBIDDEN','BOT_POLLING_CONFLICT','TELEGRAM_RATE_LIMIT','TELEGRAM_HTTP_ERROR',
 'TELEGRAM_CONNECTION_FAILED','TELEGRAM_REQUEST_REJECTED','EXISTING_OWNER_ALLOWLIST_MISSING',
 'OWNER_NOT_CONFIGURED','OWNER_CHAT_MISMATCH','ENV_READBACK_FAILED','ENV_PERMISSIONS_FAILED',
 'PROFILE_PATH_INVALID','PROFILE_NOT_PARKED','UNSAFE_ENV_PATH','UNSAFE_LOG_PATH','SETUP_INTERNAL_ERROR',
 'SEND_START','TELEGRAM_CONFIGURED','BOT_USERNAME_NOT_CONFIGURED'}

def expected_bot():
    """Bot username from the operator's private settings, read only when needed."""
    return bot_username()

def log(stage,code,exit_code=None):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'stage':stage,'code':code,'exit_code':exit_code})+'\n')

def request(flag='',token=None):
    assert flag in {'',' --finish',' --check'}
    log('ssh','BEGIN')
    try:  # private connection check BEFORE the token is sent anywhere
        preflight()
    except SystemExit as exc:
        log('ssh','PRIVATE_SSH_FAILED')
        return {'ok':False,'code':'PRIVATE_SSH_FAILED','detail':str(exc)[:200]}
    try:
        result=subprocess.run(ssh_args()+['-T',COMMAND+' --bot-username '+expected_bot()+flag],input=(token+'\n') if token is not None else '',
            text=True,encoding='utf-8',capture_output=True,timeout=100,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:
        log('ssh','TIMEOUT')
        return {'ok':False,'code':'SSH_TIMEOUT'}
    except Exception:
        log('ssh','LAUNCH_FAILED')
        return {'ok':False,'code':'SSH_LAUNCH_FAILED'}
    try:
        value=json.loads(result.stdout)
        code=value.get('code')
        if code not in CODES: raise ValueError()
        if result.returncode not in {0,1,20}: raise ValueError()
        if bool(value.get('ok')) != (result.returncode==0): raise ValueError()
        safe={k:value[k] for k in ['ok','status','code','token_saved','owner_verified','readback_verified'] if k in value}
    except Exception:
        safe={'ok':False,'code':'SSH_FAILED' if result.returncode==255 else 'REMOTE_PROTOCOL_ERROR'}
    log('telegram',safe['code'],result.returncode)
    return safe

class Window:
    def __init__(self):
        self.root=tk.Tk()
        self.root.title('Opportunity Hunter — Telegram')
        self.root.geometry('640x330')
        self.root.resizable(False,False)
        self.root.attributes('-topmost',True)
        self.events=queue.Queue()
        self.busy=False
        self.pending=False
        self.sent=False
        self.status=tk.StringVar(value='Проверяю сохранённую настройку на VPS…')
        frame=ttk.Frame(self.root,padding=22)
        frame.pack(fill='both',expand=True)
        ttk.Label(frame,textvariable=self.status,wraplength=590,justify='left',font=('Segoe UI',11)).pack(anchor='w',pady=(0,15))
        self.field=ttk.Entry(frame,show='●',width=68)
        self.field.pack(fill='x',pady=8)
        self.button=ttk.Button(frame,text='Сохранить и проверить Telegram',command=self.submit)
        self.button.pack(anchor='w',pady=8)
        ttk.Label(frame,text='Только Telegram. OAuth будет отдельным этапом.\nТокен не попадает в историю команд, аргументы процессов или журналы.',wraplength=590).pack(anchor='w',pady=8)
        self.root.bind('<Return>',lambda _:self.submit())
        self.button.config(state='disabled')
        self.root.after(150,self.consume)
        self.run(' --finish')

    def run(self,flag,token=None):
        if self.busy:return
        self.busy=True
        self.button.config(state='disabled')
        def worker():
            self.events.put(request(flag,token))
        threading.Thread(target=worker,daemon=True).start()

    def submit(self):
        if self.busy:return
        if self.sent:
            self.status.set('Повторно проверяю сохранённый токен и Telegram ID на VPS…')
            self.run(' --finish')
            return
        token=self.field.get().strip()
        if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}',token):
            self.status.set(f'Нужен токен BotFather для @{expected_bot()}. Проверьте формат в скрытом поле.')
            return
        self.field.delete(0,'end')
        self.field.config(state='disabled')
        self.sent=True
        log('input','SUBMITTED')
        self.status.set('Проверяю бота и сохраняю токен на VPS. Не закрывайте окно…')
        self.run('',token)

    def consume(self):
        try: value=self.events.get_nowait()
        except queue.Empty:
            self.root.after(150,self.consume)
            return
        self.busy=False
        code=value['code']
        if value.get('ok'):
            self.status.set(f'Telegram успешно настроен.\n\nБот @{expected_bot()} подтверждён. Токен и все поля Telegram ID сохранены на VPS и перечитаны для проверки.\n\nOAuth ещё не запускался. Можно вернуться в чат.')
            self.field.config(state='disabled')
            self.button.config(text='Готово',command=self.root.destroy,state='normal')
        elif code=='TOKEN_MISSING_OR_INVALID' and not self.sent:
            self.status.set(f'Вставьте токен BotFather для @{expected_bot()} один раз. Поле скрыто. После нажатия дождитесь явного результата.')
            self.field.config(state='normal')
            self.field.focus_set()
            self.button.config(state='normal')
        elif code=='SEND_START':
            self.sent=True
            self.status.set(f'Токен проверен и уже сохранён на VPS.\n\nОтправьте /start боту @{expected_bot()} с вашего обычного Telegram-аккаунта. ID будет проверен автоматически; повторно вводить токен не нужно.')
            self.button.config(text='Проверить /start сейчас',state='normal')
            self.root.after(7000,lambda:self.run(' --finish') if not self.busy else None)
        else:
            self.status.set('Настройка не завершена. Код: '+code+'\n\nОкно остаётся открытым; агент видит безопасный код ошибки. Не вводите токен повторно и не отправляйте его в чат.')
            self.button.config(text='Проверить сохранение на VPS',state='normal')
        self.root.after(150,self.consume)

def terminal():
    import getpass
    if not sys.stdin.isatty():
        log('terminal','TTY_REQUIRED')
        return 1
    value=request(' --finish')
    if value['code']=='TOKEN_MISSING_OR_INVALID':
        token=getpass.getpass(f'BotFather token for @{expected_bot()} (hidden): ')
        value=request('',token=token.strip())
        token=None
    while value['code']=='SEND_START':
        print(f'Token saved. Send /start to @{expected_bot()}, then press Enter. Do not paste the token again.')
        input()
        value=request(' --finish')
    print('Telegram successfully configured.' if value.get('ok') else 'Telegram setup error: '+value['code'])
    return 0 if value.get('ok') else 1

if __name__=='__main__':
    try:
        if '--terminal' in sys.argv:
            raise SystemExit(terminal())
        log('window','OPENED')
        Window().root.mainloop()
        log('window','CLOSED')
    except Exception:
        log('window','WINDOW_FAILED')
        raise SystemExit(1) from None
