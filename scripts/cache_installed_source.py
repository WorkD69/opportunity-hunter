"""Cache only public program source for compatibility review; no /opt/data files."""
import base64
import io
from pathlib import Path
import subprocess
import zipfile
from vps import ssh_args

source = '''from pathlib import Path
import base64,io,zipfile
r=Path('/opt/hermes')
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for pattern in ['hermes_cli/*.py','gateway/*.py','cron/*.py','agent/*.py','run_agent.py','tools/terminal_scope.py','tools/cronjob_tools.py','tools/registry.py','toolsets.py','hermes_constants.py','pyproject.toml','hermes_cli/plugins/*.py','plugins/web/*.py']:
  for p in r.glob(pattern):
   if p.is_file(): z.writestr(str(p.relative_to(r)),p.read_bytes())
print(base64.b64encode(b.getvalue()).decode())
'''
result = subprocess.run(ssh_args() + ['docker exec -i -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -'], input=source.encode(), capture_output=True, check=True)
target=Path('runtime/installed-hermes-source')
target.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(base64.b64decode(result.stdout))) as archive:
    archive.extractall(target)
    print('Cached public installed source files:', len(archive.namelist()))
