"""Final immutable-default and backup audit. Never outputs credential contents."""
import os
from pathlib import Path
import datetime,hashlib,json,sqlite3,subprocess
root=Path(os.environ['OH_HOST_ROOT'])
data=root/'data'
home=data/'profiles/opportunity-hunter'
baseline=Path(os.environ['OH_BACKUP_DIR'])
manifest=json.loads((baseline/'manifest.json').read_text())
unchanged={}
for entry in manifest['files']:
    if entry['path'] not in ['data/config.yaml','data/.env','data/auth.json','data/cron/jobs.json','compose.yaml','.env']:continue
    path=root/entry['path']
    unchanged[entry['path']]=(hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'] if entry['exists'] else not path.exists())
latest=sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))[-1]
snapshot=json.loads(latest.read_text())
verified=all(not e['exists'] or hashlib.sha256((latest.parent/e['path']).read_bytes()).hexdigest()==e['sha256'] for e in snapshot['files'])
backed_paths={e['path'] for e in snapshot['files'] if e['exists']}
expected={'data/profiles/opportunity-hunter/'+n for n in ['config.yaml','.env','auth.json','cron/jobs.json','opportunities.db']}
permissions=all((p.stat().st_mode & 0o077)==0 for p in [latest.parent,*latest.parent.rglob('*')])
with sqlite3.connect('file:'+str(latest.parent/'data/profiles/opportunity-hunter/opportunities.db')+'?mode=ro',uri=True) as db:
    sqlite_ok=db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
runtime=json.loads((data/'gateway_state.json').read_text())
container=json.loads(subprocess.check_output(['docker','inspect','hermes-agent']))[0]
events=[json.loads(line) for line in (home/'operator/setup-events.jsonl').read_text().splitlines()]
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'old_files_unchanged_from_before_repair':unchanged,'backup':str(latest.parent),
 'all_snapshot_hashes_verified':verified,'snapshot_includes_new_credentials_config_cron_sqlite':expected<=backed_paths,
 'backup_permissions_private':permissions,'backup_sqlite_integrity_ok':sqlite_ok,
 'oauth_started_only_after_telegram_success':next(i for i,e in enumerate(events) if e['stage']=='oauth' and e['code']=='NATIVE_LOGIN_STARTED')>next(i for i,e in enumerate(events) if e['stage']=='telegram' and e['code']=='SUCCESS'),
 'own_auth_saved_success_logged':any(e['stage']=='oauth' and e['code']=='OWN_AUTH_SAVED_VERIFIED' for e in events),
 'gateway_state':runtime.get('gateway_state'),'active_agents':runtime.get('active_agents'),
 'gateway_pid_unchanged':runtime.get('pid')==json.loads((baseline/'manifest.json').read_text())['gateway_pid'],
 'parked':(home/'gateway.parked').is_file(),
 'container_start_unchanged':container['State']['StartedAt']==manifest['container_started']}
print(json.dumps(result,indent=2))
assert all(unchanged.values()) and verified and expected<=backed_paths and permissions and sqlite_ok
assert result['oauth_started_only_after_telegram_success'] and result['own_auth_saved_success_logged']
assert result['gateway_pid_unchanged'] and result['parked']
assert result['container_start_unchanged']
