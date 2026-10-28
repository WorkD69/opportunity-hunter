"""Update reviewed helper files only; verify default profile byte-for-byte unchanged."""
import argparse
import base64
import hashlib
from pathlib import Path
from vps import run

parser=argparse.ArgumentParser()
parser.add_argument('stage',choices=['telegram','oauth'])
args=parser.parse_args()
name='configure_telegram.py' if args.stage=='telegram' else 'profile_oauth.py'
payload=base64.b64encode((Path(__file__).parent/name).read_bytes()).decode()
digest=hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
source='''from pathlib import Path
import base64,hashlib,json,os,tempfile
root=(Path(os.environ['OH_HOST_ROOT'])/'data')
home=root/'profiles/opportunity-hunter'
assert home.resolve()==home and (home/'gateway.parked').is_file()
files=[root/n for n in ['config.yaml','.env','auth.json','cron/jobs.json']]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}
payload=base64.b64decode(PAYLOAD)
assert hashlib.sha256(payload).hexdigest()==DIGEST
target=home/'operator'/NAME
assert target.parent.resolve()==home/'operator' and not target.is_symlink()
os.umask(0o077)
if target.exists():
 previous=target.with_suffix('.previous.py')
 if not previous.exists():
  previous.write_bytes(target.read_bytes())
  previous.chmod(0o600)
  os.chown(previous,10000,10000)
fd,temporary=tempfile.mkstemp(prefix='.helper-',dir=target.parent)
with os.fdopen(fd,'wb') as f: f.write(payload)
os.chmod(temporary,0o600)
os.chown(temporary,10000,10000)
os.replace(temporary,target)
assert hashlib.sha256(target.read_bytes()).hexdigest()==DIGEST
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==d for p,d in before.items())
print(json.dumps({'helper':NAME,'installed_verified':True,'default_config_env_auth_cron_unchanged':True,'parked':True}))
'''.replace('PAYLOAD',repr(payload)).replace('DIGEST',repr(digest)).replace('NAME',repr(name))
raise SystemExit(run(source).returncode)
