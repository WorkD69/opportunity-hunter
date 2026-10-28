"""Send public test source to a VPS native agent; all tools run remotely."""
import argparse
from pathlib import Path
import subprocess
import sys
sys.stdout.reconfigure(encoding='utf-8')
from vps import ssh_args
parser=argparse.ArgumentParser()
parser.add_argument('category',choices=list('ABCDQ'))
args=parser.parse_args()
source="import sys\nsys.argv.append("+repr(args.category)+")\n"+Path('scripts/live_agent_test.py').read_text()
result=subprocess.run(ssh_args()+['docker exec -i -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -u -B -'],input=source,encoding='utf-8',text=True,capture_output=True)
Path('reports/live-agent-'+args.category+'.log').write_text(result.stdout+result.stderr,encoding='utf-8')
print(result.stdout,end='')
if result.stderr:print('REMOTE_STDERR_PRESENT; withheld')
raise SystemExit(result.returncode)
