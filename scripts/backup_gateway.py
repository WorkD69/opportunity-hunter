"""Root-only verified configuration snapshot. Never emits secret values."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess

root = Path(os.environ['OH_HOST_ROOT']).resolve()
data = root / 'data'
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = root / 'private-backups' / ('opportunity-hunter-preparation-' + stamp)
os.umask(0o077)
backup.mkdir(parents=True, mode=0o700, exist_ok=False)
entries = []
paths = ['data/config.yaml', 'data/.env', 'data/auth.json', 'data/cron/jobs.json',
         'data/gateway_state.json', 'compose.yaml', '.env']
profile = 'data/profiles/opportunity-hunter/'
paths += [profile+n for n in ['config.yaml','.env','auth.json','cron/jobs.json',
                             'AGENTS.md','SOUL.md','gateway.parked','opportunities.db']]
for directory in [data/'memories',root/profile/'memories']:
    if directory.is_dir():
        paths += [str(p.relative_to(root)) for p in directory.rglob('*') if p.is_file()]
for relative in paths:
    src = root / relative
    if not src.exists():
        entries.append({'path': relative, 'exists': False})
        continue
    if src.is_symlink() or root not in src.resolve().parents:
        raise SystemExit('Unexpected snapshot path')
    dst = backup / relative
    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if src.suffix == '.db':
        with sqlite3.connect('file:'+str(src)+'?mode=ro',uri=True) as source_db:
            with sqlite3.connect(dst) as target_db:
                source_db.backup(target_db)
                assert target_db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        payload = dst.read_bytes()
    else:
        payload = src.read_bytes()
        dst.write_bytes(payload)
    dst.chmod(0o600)
    stat = src.stat()
    digest = hashlib.sha256(payload).hexdigest()
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == digest
    entries.append({'path': relative, 'exists': True, 'sha256': digest,
                    'mode': oct(stat.st_mode & 0o777), 'uid': stat.st_uid, 'gid': stat.st_gid,
                    'bytes': len(payload),'method':'sqlite_online_backup' if src.suffix=='.db' else 'file_copy'})
inspection = json.loads(subprocess.check_output(['docker', 'inspect', 'hermes-agent']))[0]
runtime = json.loads((data / 'gateway_state.json').read_text())
result = {'backup': str(backup), 'verified': True, 'created_utc': stamp,
          'files': entries, 'container_started': inspection['State']['StartedAt'],
          'container_image': inspection['Image'], 'container_oom': inspection['State']['OOMKilled'],
          'gateway_pid': json.loads((data/'gateway.pid').read_text()).get('pid') if (data/'gateway.pid').exists() else runtime.get('pid'),
          'served_profiles': runtime.get('served_profiles'),
          'bytes': sum(e.get('bytes', 0) for e in entries)}
(backup / 'manifest.json').write_text(json.dumps(result, indent=2))
safe = {k:v for k,v in result.items() if k != 'files'}
safe['config_sha256'] = next(e['sha256'] for e in entries if e['path']=='data/config.yaml')
print(json.dumps(safe, indent=2))
