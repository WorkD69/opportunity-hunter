"""Run the Telegram-gated native profile OAuth; no login codes or CLI output saved to disk."""
import argparse
import subprocess
from vps import ssh_args, bot_username
from telegram_setup import log
parser=argparse.ArgumentParser()
parser.add_argument('--check',action='store_true')
parser.add_argument('--replace-invalid',action='store_true',
                    help='replace a saved Codex session that `hermes auth refresh` reported as invalid')
args=parser.parse_args()
command=('docker exec -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -u -B '
         '/opt/data/profiles/opportunity-hunter/operator/profile_oauth.py')
command+=' --bot-username '+bot_username()
if args.check:command+=' --check'
if args.replace_invalid:command+=' --replace-invalid'
log('oauth','PREFLIGHT_STARTED' if args.check else 'SSH_STARTED')
result=subprocess.run(ssh_args()+['-T',command],stdin=subprocess.DEVNULL)
log('oauth','SSH_EXIT',result.returncode)
print('OAUTH_SSH_EXIT_CODE='+str(result.returncode),flush=True)
raise SystemExit(result.returncode)
