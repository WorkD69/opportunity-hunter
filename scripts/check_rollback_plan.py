"""Install rollback beside the verified snapshot and verify PLAN ONLY, no services touched."""
import base64
from pathlib import Path
from vps import run
encoded=base64.b64encode(Path('scripts/rollback_gateway.py').read_bytes()).decode()
source='''from pathlib import Path
import base64,os,subprocess,sys
root=Path(os.environ['OH_HOST_ROOT'])
manifest=sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))[-1]
script=manifest.parent/'rollback_gateway.py'
script.write_bytes(base64.b64decode(ENCODED))
script.chmod(0o600)
os.chown(script,0,0)
subprocess.run(['python3',str(script),'--backup',str(manifest.parent)],check=True)
'''.replace('ENCODED',repr(encoded))
raise SystemExit(run(source).returncode)
