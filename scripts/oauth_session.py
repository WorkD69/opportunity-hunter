"""Run the Telegram-gated native profile OAuth; no login codes or CLI output saved to disk."""
import argparse
import subprocess
from vps import ssh_args
from telegram_setup import log
parser=argparse.ArgumentParser()
parser.add_argument('--check',action='store_true')
args=parser.parse_args()
command=('docker exec -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -u -B '
         '/opt/data/profiles/opportunity-hunter/operator/profile_oauth.py')
if args.check:command+=' --check'
log('oauth','PREFLIGHT_STARTED' if args.check else 'SSH_STARTED')
result=subprocess.run(ssh_args()+['-T',command],stdin=subprocess.DEVNULL)
log('oauth','SSH_EXIT',result.returncode)
print('OAUTH_SSH_EXIT_CODE='+str(result.returncode),flush=True)
raise SystemExit(result.returncode)
