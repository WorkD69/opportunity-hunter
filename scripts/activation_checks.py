"""Local redacted evidence runner over existing pinned SSH access."""
import argparse
from pathlib import Path
import subprocess
from vps import ssh_args

parser=argparse.ArgumentParser()
parser.add_argument('check',choices=['profile','server','metadata','scope','own-model','default-model','snapshot','rollback-plan','recovery-final'])
args=parser.parse_args()
files={'profile':'activation_profile_check.py','server':'activation_server_check.py',
 'metadata':'activation_setup_metadata.py','own-model':'activation_auth_probe.py',
 'default-model':'activation_auth_probe.py','scope':'activation_scope_check.py','snapshot':'backup_gateway.py',
 'recovery-final':'setup_recovery_final.py'}
if args.check=='rollback-plan':
    # This wrapper already uses pinned SSH and only invokes PLAN ONLY.
    result=subprocess.run(['python','scripts/check_rollback_plan.py'],capture_output=True,text=True,encoding='utf-8')
else:
    source=(Path('scripts')/files[args.check]).read_text(encoding='utf-8')
    if args.check=='default-model':
        source="import sys\nsys.argv.append('--default')\n"+source
    command='docker exec -i -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -' if args.check in {'profile','scope','own-model','default-model'} else 'python3 -'
    result=subprocess.run(ssh_args()+[command],input=source,capture_output=True,text=True,encoding='utf-8')
path=Path('reports')/('activation-'+args.check+'.log')
path.write_text(result.stdout+result.stderr,encoding='utf-8')
print(result.stdout,end='')
if result.stderr:
    print(result.stderr,end='')
raise SystemExit(result.returncode)
