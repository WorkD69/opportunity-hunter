"""Copy reviewed operator helpers only into the parked new profile."""
import base64
from pathlib import Path
from vps import run

source = '''from pathlib import Path
import base64,os,subprocess
home=(Path(os.environ['OH_HOST_ROOT'])/'data/profiles/opportunity-hunter')
assert home.resolve()==home and (home/'gateway.parked').is_file()
os.umask(0o077)
'''
for name in ['configure_telegram.py','profile_oauth.py']:
    encoded = base64.b64encode((Path('scripts')/name).read_bytes()).decode()
    source += f"p=home/'operator/{name}'\np.write_bytes(base64.b64decode({encoded!r}))\np.chmod(0o600)\nos.chown(p,10000,10000)\n"
source += """print('Installed helper files only. Telegram and OAuth must be run separately; no login started.')
"""
raise SystemExit(run(source).returncode)
