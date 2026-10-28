"""Hot-reload only the Opportunity Hunter plugin; no Gateway/service restart."""
import base64,json
from pathlib import Path
from vps import run
files={'plugins/opportunity-hunter/__init__.py':Path('opportunity_hunter/assets/plugin/__init__.py'),
       'plugins/opportunity-hunter/opportunity_hunter/runtime.py':Path('opportunity_hunter/runtime.py'),
       'skills/opportunity-hunter/SKILL.md':Path('opportunity_hunter/assets/skill/SKILL.md'),
       'AGENTS.md':Path('opportunity_hunter/assets/AGENTS.md')}
payload={name:base64.b64encode(path.read_bytes()).decode() for name,path in files.items()}
source='''from pathlib import Path
import base64,hashlib,json,os,subprocess,tempfile
root=(Path(os.environ['OH_HOST_ROOT'])/'data')
home=root/'profiles/opportunity-hunter'
backup=(Path(os.environ['OH_BACKUP_DIR'])/'command-patch-before')
assert home.resolve()==home and not (home/'gateway.parked').exists()
default_files=[root/n for n in ['config.yaml','.env','auth.json','cron/jobs.json'] if (root/n).exists()]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in default_files}
cron_hash=hashlib.sha256((home/'cron/jobs.json').read_bytes()).hexdigest()
pid=json.loads((root/'gateway_state.json').read_text())['pid']
os.umask(0o077)
for relative,encoded in PAYLOAD.items():
 target=home/relative
 assert target.resolve()==target and home in target.parents and not target.is_symlink()
 saved=backup/relative
 saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 if not saved.exists(): saved.write_bytes(target.read_bytes())
 saved.chmod(0o600)
 fd,name=tempfile.mkstemp(prefix='.oh-command-',dir=target.parent)
 with os.fdopen(fd,'wb') as f:f.write(base64.b64decode(encoded))
 os.chmod(name,0o600)
 os.chown(name,10000,10000)
 os.replace(name,target)
code="from pathlib import Path; import json; from gateway.control_socket import reload_gateway_plugins; r=reload_gateway_plugins(Path('/opt/data'),profile_home=Path('/opt/data/profiles/opportunity-hunter')); print(json.dumps({k:r.get(k) for k in ['reloaded','adapters_rewired','home']} if r else {'reloaded':False}))"
result=subprocess.run(['docker','exec','-u','hermes','-w','/opt/hermes','-e','HOME=/opt/data','-e','HERMES_HOME=/opt/data','hermes-agent','/opt/hermes/.venv/bin/python','-B','-c',code],capture_output=True,text=True,check=True)
reply=json.loads(result.stdout)
assert reply.get('reloaded') and reply.get('adapters_rewired')==1, 'New profile plugin did not rewire'
assert json.loads((root/'gateway_state.json').read_text())['pid']==pid
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
assert hashlib.sha256((home/'cron/jobs.json').read_bytes()).hexdigest()==cron_hash
print(json.dumps({'plugin_hot_reload':reply,'gateway_pid_unchanged':True,'default_files_unchanged':True,'cron_unchanged':True}))
'''.replace('PAYLOAD',repr(payload))
raise SystemExit(run(source).returncode)
