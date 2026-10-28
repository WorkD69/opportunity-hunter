"""Read-only redacted profile/Telegram checks. Never polls updates or starts Gateway."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request

home = Path('/opt/data/profiles/opportunity-hunter')
root = Path('/opt/data')
os.umask(0o077)
sys.dont_write_bytecode = True
from dotenv import dotenv_values
import hermes_yaml as yaml

def env(path):
    return dotenv_values(path, interpolate=False) if path.is_file() else {}

def secret_values(obj):
    if isinstance(obj, dict):
        return {v for k,v in obj.items() if k in {'access_token','refresh_token','api_key'} and isinstance(v,str) and v} | set().union(*(secret_values(v) for v in obj.values()), set())
    if isinstance(obj, list):
        return set().union(*(secret_values(v) for v in obj), set())
    return set()

own = env(home/'.env')
old = env(root/'.env')
token = own.get('TELEGRAM_BOT_TOKEN') or ''
owner = own.get('OH_OWNER_ID') or ''
auth = json.loads((home/'auth.json').read_text()) if (home/'auth.json').exists() else {}
old_auth = json.loads((root/'auth.json').read_text())
cfg = yaml.safe_load((home/'config.yaml').read_text())
old_cfg = yaml.safe_load((root/'config.yaml').read_text())
jobs = json.loads((home/'cron/jobs.json').read_text())
jobs = jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
other_envs = [root/'.env', *[p for p in (root/'profiles').glob('*/.env') if p.parent != home]]
conflicts = []
for p in other_envs:
    other = env(p).get('TELEGRAM_BOT_TOKEN') or ''
    if token and other and (token == other or token.split(':')[0] == other.split(':')[0]):
        conflicts.append('default' if p.parent == root else p.parent.name)

def api(method, payload=None):
    req = urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
        data=json.dumps(payload or {}).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=20) as response:
            result=json.loads(response.read(524288))
        return result.get('result') if result.get('ok') else None
    except Exception:
        return None

me = api('getMe') if token else None
webhook = api('getWebhookInfo') if me else None
chat = api('getChat', {'chat_id':owner}) if me and owner.isdigit() else None
result = {
 'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'parked':(home/'gateway.parked').is_file(),
 'multiplex_config':old_cfg.get('gateway',{}).get('multiplex_profiles'),
 'telegram':{'private_env_present':bool(token),'env_mode':oct((home/'.env').stat().st_mode & 0o777),
   'getMe_ok':bool(me),'expected_bot':bool(me and me.get('username','').lower()==os.environ['OH_BOT_USERNAME'].lower()),
   'owner_numeric':owner.isdigit(),'owner_fields_equal':bool(owner and own.get('TELEGRAM_ALLOWED_USERS')==own.get('TELEGRAM_HOME_CHANNEL')==owner),
   'owner_matches_existing_allowlist':owner in re.findall(r'\d+',old.get('TELEGRAM_ALLOWED_USERS') or ''),
   'owner_private_chat_verified':bool(chat and chat.get('type')=='private' and str(chat.get('id'))==owner),
   'bot_conflicts':conflicts,'webhook_check_ok':webhook is not None,'has_webhook':bool(webhook and webhook.get('url'))},
 'auth':{'own_file_exists':(home/'auth.json').is_file(),'own_pool_rows':len(auth.get('credential_pool',{}).get('openai-codex') or []),
   'own_secret_material':bool(secret_values(auth)),'shares_secret_material':bool(secret_values(auth)&secret_values(old_auth)),
   'own_auth_mode':oct((home/'auth.json').stat().st_mode & 0o777) if (home/'auth.json').exists() else None,
   'old_pool_rows':len(old_auth.get('credential_pool',{}).get('openai-codex') or [])},
 'config':{'new_model':{k:cfg.get('model',{}).get(k) for k in ['provider','default','api_mode']},
   'old_model':{k:old_cfg.get('model',{}).get(k) for k in ['provider','default','api_mode']},
   'external_logins_disabled':cfg.get('auth',{}).get('adopt_external_logins') is False,
   'mcp_empty':cfg.get('mcp_servers')=={},'disabled_toolsets':cfg.get('agent',{}).get('disabled_toolsets'),
   'cwd':cfg.get('terminal',{}).get('cwd'),'fallback_configured':bool(cfg.get('fallback_model') or cfg.get('fallback_models'))},
 'cron':[{'name':j['name'],'enabled':j.get('enabled'),'state':j.get('state'),'workdir':j.get('workdir')} for j in jobs],
 'symlinks':[str(p.relative_to(home)) for p in home.rglob('*') if p.is_symlink()],
 'unsafe_permissions':[str(p.relative_to(home)) for p in home.rglob('*') if p.stat().st_mode & 0o077],
}
with sqlite3.connect('file:'+str(home/'opportunities.db')+'?mode=ro',uri=True) as db:
    result['sqlite_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
print(json.dumps(result,ensure_ascii=False,indent=2))
