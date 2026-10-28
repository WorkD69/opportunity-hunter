"""Locate setup artifacts and compare root OAuth generations; no private contents emitted."""
import os
from pathlib import Path
import datetime
import json
import subprocess
root=Path(os.environ['OH_HOST_ROOT'])
paths=[]
for parent in [root/'data',root/'data/profiles']:
    for p in parent.glob('*/auth.json'):
        paths.append(p)
paths.extend([root/'data/auth.json',root/'data/profiles/opportunity-hunter/.env'])
out=[]
for p in dict.fromkeys(paths):
    if p.is_file():
        out.append({'path':str(p),'bytes':p.stat().st_size,'mtime_utc':datetime.datetime.fromtimestamp(p.stat().st_mtime,datetime.timezone.utc).isoformat()})
current=json.loads((root/'data/auth.json').read_text())
rows=current.get('credential_pool',{}).get('openai-codex') or []
baseline=sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))[0].parent
before=json.loads((baseline/'data/auth.json').read_text())
before_rows=before.get('credential_pool',{}).get('openai-codex') or []
metadata=[]
for row in rows:
    metadata.append({'present_before_recent_account_change':any(row.get('id')==r.get('id') for r in before_rows),
     'label_matches_opportunity_hunter':row.get('label')=='opportunity-hunter',
     'has_access_token':bool(row.get('access_token')),'has_refresh_token':bool(row.get('refresh_token')),
     'disabled':row.get('disabled'),'priority':row.get('priority'),
     'has_account_identifier':bool(row.get('account_id'))})
processes=subprocess.check_output(['docker','top','hermes-agent','-eo','pid,comm,args'],text=True).splitlines()[1:]
print(json.dumps({'artifacts':out,'root_oauth_rows_metadata':metadata,
 'profile_oauth_process_count':sum('profile_oauth.py' in x for x in processes),
 'configure_telegram_process_count':sum('configure_telegram.py' in x for x in processes)},indent=2))
