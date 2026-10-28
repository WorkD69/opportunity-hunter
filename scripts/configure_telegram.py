"""Separate, resumable Telegram setup. Secret input only on stdin; no raw errors logged."""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request

HOME=Path('/opt/data/profiles/opportunity-hunter')
TOKEN_RE=re.compile(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}')
BOT_RE=re.compile(r'[A-Za-z0-9_]{5,32}')

class SetupError(Exception):
    """Only fixed, public error codes may cross the SSH boundary."""

def env_values(path):
    from dotenv import dotenv_values
    return dotenv_values(path,interpolate=False) if path.is_file() else {}

def journal(home,stage,code):
    path=home/'operator/setup-events.jsonl'
    path.parent.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink(): raise SetupError('UNSAFE_LOG_PATH')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a',encoding='utf-8') as stream:
        stream.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                 'stage':stage,'code':code})+'\n')
    path.chmod(0o600)

def telegram_api(token):
    def call(method,payload=None):
        request=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
            data=json.dumps(payload or {}).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:
                value=json.loads(response.read(524288))
        except urllib.error.HTTPError as exc:
            code={401:'TOKEN_REJECTED',403:'CHAT_FORBIDDEN',409:'BOT_POLLING_CONFLICT',429:'TELEGRAM_RATE_LIMIT'}.get(exc.code,'TELEGRAM_HTTP_ERROR')
            raise SetupError(code) from None
        except Exception:
            raise SetupError('TELEGRAM_CONNECTION_FAILED') from None
        if not value.get('ok'): raise SetupError('TELEGRAM_REQUEST_REJECTED')
        return value['result']
    return call

def write_env(home,changes):
    path=home/'.env'
    if path.is_symlink(): raise SetupError('UNSAFE_ENV_PATH')
    previous=path.read_text() if path.exists() else ''
    lines=[line for line in previous.splitlines() if not any(re.match(r'^\s*(?:export\s+)?'+re.escape(key)+r'\s*=',line) for key in changes)]
    text='\n'.join(lines+[key+'='+value for key,value in changes.items()])+'\n'
    fd,name=tempfile.mkstemp(prefix='.env.setup-',dir=home)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name,0o600)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
    stored=env_values(path)
    if not all(stored.get(k)==v for k,v in changes.items()): raise SetupError('ENV_READBACK_FAILED')
    if os.name!='nt' and path.stat().st_mode & 0o077: raise SetupError('ENV_PERMISSIONS_FAILED')

def configure(token=None,*,home=HOME,api=None,check_only=False,expected_bot=None):
    expected_bot=(expected_bot or os.environ.get('OH_BOT_USERNAME') or '').lstrip('@')
    if not BOT_RE.fullmatch(expected_bot): raise SetupError('BOT_USERNAME_NOT_CONFIGURED')
    os.umask(0o077)
    if home.resolve()!=home or home.name!='opportunity-hunter' or home.parent.name!='profiles':
        raise SetupError('PROFILE_PATH_INVALID')
    if not (home/'gateway.parked').is_file(): raise SetupError('PROFILE_NOT_PARKED')
    if (home/'.env').is_symlink(): raise SetupError('UNSAFE_ENV_PATH')
    own=env_values(home/'.env')
    token=token if token is not None else own.get('TELEGRAM_BOT_TOKEN') or ''
    if not TOKEN_RE.fullmatch(token): raise SetupError('TOKEN_MISSING_OR_INVALID')
    root=home.parent.parent
    other_envs=[root/'.env',*[p for p in (root/'profiles').glob('*/.env') if p.parent!=home]]
    for path in other_envs:
        other=env_values(path).get('TELEGRAM_BOT_TOKEN') or ''
        if other and (token==other or token.split(':')[0]==other.split(':')[0]):
            raise SetupError('BOT_ASSIGNED_TO_OTHER_PROFILE')
    api=api or telegram_api(token)
    journal(home,'telegram','VALIDATING')
    me=api('getMe')
    if me.get('username','').lower()!=expected_bot.lower(): raise SetupError('WRONG_BOT')
    if api('getWebhookInfo').get('url'): raise SetupError('BOT_HAS_WEBHOOK')
    if not check_only:
        write_env(home,{'TELEGRAM_BOT_TOKEN':token})
        journal(home,'telegram','TOKEN_SAVED_VERIFIED')
    allowed=set(re.findall(r'\d+',env_values(root/'.env').get('TELEGRAM_ALLOWED_USERS') or ''))
    if not allowed: raise SetupError('EXISTING_OWNER_ALLOWLIST_MISSING')
    own=env_values(home/'.env')
    owner=own.get('OH_OWNER_ID') or ''
    if not (owner.isdigit() and owner in allowed and own.get('TELEGRAM_ALLOWED_USERS')==own.get('TELEGRAM_HOME_CHANNEL')==owner):
        if check_only: raise SetupError('OWNER_NOT_CONFIGURED')
        updates=api('getUpdates',{'limit':100,'timeout':0,'allowed_updates':['message']})
        candidates=set()
        for update in updates:
            message=update.get('message') or {}
            sender,chat=message.get('from') or {},message.get('chat') or {}
            words=str(message.get('text','')).split()
            if (words and words[0].split('@')[0]=='/start' and chat.get('type')=='private'
                    and sender.get('id')==chat.get('id') and str(sender.get('id')) in allowed and not sender.get('is_bot')):
                candidates.add(str(sender['id']))
        if len(candidates)!=1:
            journal(home,'telegram','AWAITING_START')
            return {'ok':False,'status':'awaiting_start','code':'SEND_START','token_saved':True}
        owner=candidates.pop()
    chat=api('getChat',{'chat_id':owner})
    if chat.get('type')!='private' or str(chat.get('id'))!=owner: raise SetupError('OWNER_CHAT_MISMATCH')
    if not check_only:
        write_env(home,{'TELEGRAM_BOT_TOKEN':token,'TELEGRAM_ALLOWED_USERS':owner,
                        'TELEGRAM_HOME_CHANNEL':owner,'OH_OWNER_ID':owner})
    saved=env_values(home/'.env')
    if saved.get('TELEGRAM_BOT_TOKEN')!=token or any(saved.get(k)!=owner for k in ['TELEGRAM_ALLOWED_USERS','TELEGRAM_HOME_CHANNEL','OH_OWNER_ID']):
        raise SetupError('ENV_READBACK_FAILED')
    journal(home,'telegram','SUCCESS')
    return {'ok':True,'status':'success','code':'TELEGRAM_CONFIGURED','bot':'@'+expected_bot,
       'token_saved':True,'owner_verified':True,'readback_verified':True,'token_conflicts':False,'gateway_still_parked':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--finish',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--bot-username',required=True)
    args=parser.parse_args()
    try:
        token=None if args.finish or args.check else sys.stdin.read(512).strip()
        result=configure(token,check_only=args.check,expected_bot=args.bot_username)
        print(json.dumps(result))
        return 0 if result['ok'] else 20
    except SetupError as exc:
        code=str(exc)
    except Exception:
        code='SETUP_INTERNAL_ERROR'
    try: journal(HOME,'telegram',code)
    except Exception: pass
    print(json.dumps({'ok':False,'status':'error','code':code}))
    return 1

if __name__=='__main__': raise SystemExit(main())
