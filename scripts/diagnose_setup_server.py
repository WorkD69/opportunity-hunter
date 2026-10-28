"""Safe diagnostics of setup paths, permissions and artifact metadata."""
from pathlib import Path
import hashlib,json,os,tempfile
home=Path('/opt/data/profiles/opportunity-hunter')
def meta(p):
    return {'exists':p.exists(),'mode':oct(p.stat().st_mode & 0o777) if p.exists() else None,
     'uid':p.stat().st_uid if p.exists() else None,'writable':os.access(p,os.W_OK),
     'symlink':p.is_symlink()}
out={'uid':os.getuid(),'home_resolved_correctly':home.resolve()==home,'parked':(home/'gateway.parked').is_file(),
 'paths':{n:meta(home/n) for n in ['.','operator','.env','auth.json']},
 'helpers':{n:{'exists':(home/'operator'/n).is_file(),'sha256':hashlib.sha256((home/'operator'/n).read_bytes()).hexdigest()} for n in ['configure_telegram.py','profile_oauth.py']}}
fd,name=tempfile.mkstemp(prefix='.setup-write-probe-',dir=home)
os.close(fd)
os.unlink(name)
out['profile_write_probe']=True
from dotenv import dotenv_values
old=dotenv_values('/opt/data/.env',interpolate=False)
out['existing_owner_allowlist_present']=bool(old.get('TELEGRAM_ALLOWED_USERS'))
print(json.dumps(out,indent=2))
