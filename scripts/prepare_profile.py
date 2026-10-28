"""Install the exact secret-free release atomically, parked; never control services."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile
from vps import run

manifest = json.loads(Path('reports/build-manifest.json').read_text(encoding='utf-8'))
payload = Path('build/opportunity-hunter-v0.1.0.zip').read_bytes()
assert hashlib.sha256(payload).hexdigest() == manifest['archive']['sha256']
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(manifest['files'])
    for item in manifest['files']:
        data = archive.read('opportunity-hunter/' + item['path'])
        assert hashlib.sha256(data).hexdigest() == item['sha256']
        assert data == (Path('build/opportunity-hunter') / item['path']).read_bytes()
        rel = item['path']
        if rel.startswith('plugins/opportunity-hunter/opportunity_hunter/'):
            src = Path('opportunity_hunter') / Path(rel).name
        elif rel == 'plugins/opportunity-hunter/__init__.py':
            src = Path('opportunity_hunter/assets/plugin/__init__.py')
        elif rel == 'plugins/opportunity-hunter/plugin.yaml':
            src = Path('opportunity_hunter/assets/plugin/plugin.yaml')
        elif rel == 'skills/opportunity-hunter/SKILL.md':
            src = Path('opportunity_hunter/assets/skill/SKILL.md')
        else:
            src = Path('opportunity_hunter/assets') / rel
        if src.is_file():
            assert data == src.read_bytes(), f'Source differs: {rel}'

source = '''from pathlib import Path
import base64,hashlib,io,json,os,zipfile
root=Path(os.environ['OH_HOST_ROOT']).resolve()
data=root/'data'
target=data/'profiles/opportunity-hunter'
assert not target.exists(), 'Refusing to overwrite an existing profile'
backups=sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))
assert backups, 'Verified backup required'
backup=json.loads(backups[-1].read_text())
assert backup['verified']
for e in backup['files']:
 if e['exists']:
  assert hashlib.sha256((backups[-1].parent/e['path']).read_bytes()).hexdigest()==e['sha256']
baseline={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [data/'config.yaml',data/'.env',data/'auth.json',data/'cron/jobs.json'] if p.exists()}
os.umask(0o077)
parent=target.parent
parent.mkdir(mode=0o700,exist_ok=True)
stage=parent/('.opportunity-hunter-preparing-'+str(os.getpid()))
stage.mkdir(mode=0o700,exist_ok=False)
(stage/'gateway.parked').touch(mode=0o600)
payload=base64.b64decode(PAYLOAD)
assert hashlib.sha256(payload).hexdigest()==ARCHIVE_HASH
with zipfile.ZipFile(io.BytesIO(payload)) as z:
 assert z.testzip() is None
 for name in z.namelist():
  rel=Path(name)
  assert rel.parts[0]=='opportunity-hunter' and '..' not in rel.parts and not rel.is_absolute()
  out=stage/Path(*rel.parts[1:])
  out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  out.write_bytes(z.read(name))
for directory in ['memories','sessions','skills','skins','logs','plans','workspace','cron','home','operator']:
 (stage/directory).mkdir(mode=0o700,exist_ok=True)
(stage/'.env').write_text('# Private Opportunity Hunter credentials; pending owner input.\\n')
(stage/'.no-bundled-skills').write_text('Only the reviewed Opportunity Hunter skill is installed.\\n')
for p in [stage,*stage.rglob('*')]:
 assert not p.is_symlink()
 p.chmod(0o700 if p.is_dir() else 0o600)
 os.chown(p,10000,10000)
os.chown(parent,10000,10000)
stage.rename(target)
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline.items()), 'Existing configuration changed'
print(json.dumps({'installed':str(target),'parked':True,'release_hash':ARCHIVE_HASH,'files':len(list(target.rglob('*'))),'backup_verified':str(backups[-1].parent),'existing_config_env_auth_cron_unchanged':True},indent=2))
'''
source = source.replace('PAYLOAD', repr(base64.b64encode(payload).decode())).replace('ARCHIVE_HASH', repr(manifest['archive']['sha256']))
raise SystemExit(run(source).returncode)
