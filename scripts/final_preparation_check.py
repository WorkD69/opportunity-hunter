"""Redacted readiness report. Does not start services, login, search or send messages."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess

root = Path(os.environ['OH_HOST_ROOT']).resolve()
data = root/'data'
home = data/'profiles/opportunity-hunter'
backups = sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))
backup = json.loads(backups[-1].read_text())
verified = all(not e['exists'] or hashlib.sha256((backups[-1].parent/e['path']).read_bytes()).hexdigest()==e['sha256'] for e in backup['files'])
unchanged = {e['path']:(hashlib.sha256((root/e['path']).read_bytes()).hexdigest()==e['sha256'] if e['exists'] else not (root/e['path']).exists())
             for e in backup['files'] if e['path'] in ['data/config.yaml','data/.env','data/auth.json','data/cron/jobs.json','compose.yaml','.env']}
inspection = json.loads(subprocess.check_output(['docker','inspect','hermes-agent']))[0]
jobs = json.loads((home/'cron/jobs.json').read_text())
jobs = jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
old_jobs = json.loads((data/'cron/jobs.json').read_text()) if (data/'cron/jobs.json').exists() else []
old_jobs = old_jobs.get('jobs',[]) if isinstance(old_jobs,dict) else old_jobs
credentials = json.loads((home/'auth.json').read_text()) if (home/'auth.json').is_file() else {}
rows = credentials.get('credential_pool',{}).get('openai-codex') or []
old_auth = json.loads((data/'auth.json').read_text())

def tokens(obj):
    if isinstance(obj,dict):
        return {v for k,v in obj.items() if k in ['access_token','refresh_token'] and isinstance(v,str) and v} | set().union(*(tokens(v) for v in obj.values()),set())
    if isinstance(obj,list): return set().union(*(tokens(v) for v in obj),set())
    return set()

own_tokens = tokens(credentials)
shared = bool(own_tokens & tokens(old_auth))
private_env = (home/'.env').read_text()
telegram_configured = all(any(line.startswith(k+'=') and line.split('=',1)[1] for line in private_env.splitlines()) for k in ['TELEGRAM_BOT_TOKEN','TELEGRAM_ALLOWED_USERS','TELEGRAM_HOME_CHANNEL','OH_OWNER_ID'])
state = json.loads((data/'gateway_state.json').read_text())
pid_record = json.loads((data/'gateway.pid').read_text())
result = {'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'profile':str(home),'parked':(home/'gateway.parked').is_file(),
 'backup':str(backups[-1].parent),'backup_verified':verified,
 'existing_files_unchanged':unchanged,
 'same_container_start':inspection['State']['StartedAt']==backup['container_started'],
 'same_gateway_pid':pid_record.get('pid')==backup['gateway_pid'],
 'gateway_pid':pid_record.get('pid'),'runtime_served_profiles':state.get('served_profiles'),
 'oom_killed':inspection['State']['OOMKilled'],
 'new_jobs':[{'name':j['name'],'enabled':j.get('enabled'),'state':j.get('state')} for j in jobs],
 'cron_name_conflicts':bool({j['name'] for j in jobs}&{j.get('name') for j in old_jobs}),
 'telegram_configured':telegram_configured,'own_oauth_pool_rows':len(rows),
 'own_oauth_present':bool(own_tokens),'oauth_shares_token_material':shared,
 'profile_file_count':len([p for p in home.rglob('*') if p.is_file()]),
 'profile_bytes':sum(p.stat().st_size for p in home.rglob('*') if p.is_file()),
 'symlinks':[str(p.relative_to(home)) for p in home.rglob('*') if p.is_symlink()],
 'unsafe_file_permissions':[str(p.relative_to(home)) for p in home.rglob('*') if p.stat().st_mode & 0o077],
 'profile_uid':home.stat().st_uid,'profile_mode':oct(home.stat().st_mode & 0o777),
 'protected_service':(subprocess.run(['systemctl','is-active',os.environ['OH_PROTECTED_SERVICE']],capture_output=True,text=True).stdout.strip() if os.environ.get('OH_PROTECTED_SERVICE') else 'not-configured')}
print(json.dumps(result,ensure_ascii=False,indent=2))
assert verified and all(unchanged.values()), 'Baseline drift; review before proceeding'
assert result['same_gateway_pid'] and result['same_container_start']
assert result['parked'] and len(jobs)==5 and all(j.get('enabled') is False and j.get('state')=='paused' for j in jobs)
assert not result['cron_name_conflicts'] and not shared and not result['symlinks']
assert result['protected_service']=='active'
assert not result['unsafe_file_permissions']
print(subprocess.check_output(['free','-m'],text=True))
print(subprocess.check_output(['df','-h','/'],text=True))
print(subprocess.check_output(['docker','stats','--no-stream','--format','{{.Name}} {{.MemUsage}} {{.CPUPerc}}'],text=True))
