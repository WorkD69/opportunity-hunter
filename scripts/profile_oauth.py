"""Separate native OAuth, gated on verified Telegram and guarded against foreign credentials."""
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import logging
import os
import re
import sys
from types import SimpleNamespace

HOME=Path('/opt/data/profiles/opportunity-hunter')

class PublicOutput:
    """Display only the official device URL/code; never persist CLI output or credentials."""
    def __init__(self,stream):
        self.stream=stream
        self.buffer=''
    def write(self,value):
        self.buffer+=value
        while '\n' in self.buffer:
            line,self.buffer=self.buffer.split('\n',1)
            line=re.sub(r'\x1b\[[0-9;]*m','',line).strip()
            if line=='https://auth.openai.com/codex/device':
                self.stream.write('OpenAI URL: '+line+'\n')
            elif re.fullmatch(r'[A-Z0-9]{3,10}-[A-Z0-9]{3,10}',line):
                self.stream.write('OpenAI device code: '+line+'\n')
            elif line.startswith('Waiting for sign-in'):
                self.stream.write('Waiting for your browser authorization.\n')
            self.stream.flush()
        return len(value)
    def flush(self):self.stream.flush()
    def isatty(self):return False

def main():
    requested_check='--check' in sys.argv
    # --replace-invalid: the owner confirmed the saved session is dead (auth refresh failed);
    # old rows are removed only AFTER a new login is saved and verified.
    replace_invalid='--replace-invalid' in sys.argv
    expected_bot=sys.argv[sys.argv.index('--bot-username')+1] if '--bot-username' in sys.argv[:-1] else ''
    os.environ.clear()
    os.environ.update({'HOME':str(HOME/'home'),'HERMES_HOME':str(HOME),
        'PATH':'/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin','USER':'hermes',
        'LANG':'C.UTF-8','TZ':'Europe/Moscow','PYTHONDONTWRITEBYTECODE':'1'})
    os.chdir(HOME)
    os.umask(0o077)
    sys.dont_write_bytecode=True
    logging.disable(logging.CRITICAL)
    spec=importlib.util.spec_from_file_location('oh_telegram_setup',HOME/'operator/configure_telegram.py')
    telegram=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(telegram)
    def event(code):telegram.journal(HOME,'oauth',code)
    def stop(code):
        event(code)
        print(json.dumps({'ok':False,'stage':'oauth','code':code}),flush=True)
        return 1
    try:
        # No OAuth starts until Telegram token, owner and read-back all pass.
        telegram.configure(home=HOME,check_only=True,expected_bot=expected_bot)
    except Exception:
        return stop('TELEGRAM_NOT_READY')
    if (HOME/'auth.json').is_symlink():return stop('UNSAFE_AUTH_PATH')
    state=json.loads((HOME/'auth.json').read_text()) if (HOME/'auth.json').exists() else {}
    old_ids={r.get('id') for r in state.get('credential_pool',{}).get('openai-codex') or [] if r.get('id')}
    if (old_ids or state.get('providers',{}).get('openai-codex')) and not replace_invalid:
        return stop('OWN_AUTH_ALREADY_EXISTS_VERIFY_INSTEAD')

    def guard(event,args):
        if event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):return
        path=Path(os.fsdecode(args[0])).resolve()
        if path.name in {'auth.json','auth.lock','.env','.credentials.json'} and HOME not in path.parents:
            raise PermissionError('Foreign credential file access denied')
    sys.addaudithook(guard)
    from hermes_constants import get_hermes_home
    from hermes_cli.auth import _load_global_auth_store,_auth_file_path
    if get_hermes_home()!=HOME or _auth_file_path()!=HOME/'auth.json':return stop('AUTH_PATH_MISMATCH')
    if _load_global_auth_store():return stop('FOREIGN_AUTH_VISIBLE')
    event('ISOLATION_CHECKED')
    if requested_check:
        print(json.dumps({'ok':True,'stage':'oauth','code':'PREFLIGHT_OK','login_started':False}),flush=True)
        return 0
    event('NATIVE_LOGIN_STARTED')
    label='opportunity-hunter' if not old_ids else 'opportunity-hunter-relogin'
    sys.argv=['hermes','-p','opportunity-hunter','auth','add','openai-codex',
              '--label',label,'--no-browser']
    code=0
    try:
        with contextlib.redirect_stdout(PublicOutput(sys.stdout)),contextlib.redirect_stderr(io.StringIO()):
            from hermes_cli.main import main as native_main
            native_main()
    except SystemExit as exc:
        code=exc.code if isinstance(exc.code,int) else (0 if exc.code is None else 1)
    except KeyboardInterrupt:
        return stop('LOGIN_CANCELLED')
    except Exception:
        return stop('NATIVE_LOGIN_EXCEPTION')
    if code!=0:return stop('NATIVE_LOGIN_FAILED')
    state=json.loads((HOME/'auth.json').read_text()) if (HOME/'auth.json').is_file() else {}
    rows=state.get('credential_pool',{}).get('openai-codex') or []
    if not any(r.get('access_token') and r.get('refresh_token') and r.get('id') not in old_ids for r in rows):
        return stop('OWN_AUTH_READBACK_FAILED')
    if old_ids:
        from hermes_cli.auth_commands import auth_remove_command
        try:
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                for old_id in sorted(old_ids):
                    auth_remove_command(SimpleNamespace(provider='openai-codex',target=old_id,index=None))
        except BaseException:
            return stop('NEW_AUTH_SAVED_OLD_REMOVAL_FAILED')
        state=json.loads((HOME/'auth.json').read_text())
        left={r.get('id') for r in state.get('credential_pool',{}).get('openai-codex') or []}
        if left & old_ids or not left:
            return stop('NEW_AUTH_SAVED_OLD_REMOVAL_FAILED')
        event('INVALID_AUTH_REPLACED')
    (HOME/'auth.json').chmod(0o600)
    event('OWN_AUTH_SAVED_VERIFIED')
    print(json.dumps({'ok':True,'stage':'oauth','code':'OWN_AUTH_SAVED_VERIFIED','own_auth_file':True}),flush=True)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'ok':False,'stage':'oauth','code':'OAUTH_INTERNAL_ERROR'}),flush=True)
        raise SystemExit(1) from None
