"""Only redacted infrastructure lines, no messages or credential values."""
import os
from pathlib import Path
import json,re,subprocess
root=(Path(os.environ['OH_HOST_ROOT'])/'data')
print(json.dumps({'log_files':[str(p.relative_to(root)) for p in (root/'logs').glob('*') if p.is_file()]},indent=2))
def redact(s):
    s=re.sub(r'\d{5,20}:[A-Za-z0-9_-]{20,}','[BOT_TOKEN_REDACTED]',s)
    s=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[JWT_REDACTED]',s)
    s=re.sub(r'(Bearer\s+)[^\s"\']+',r'\1[REDACTED]',s,flags=re.I)
    s=re.sub(r'(?i)((?:access_token|refresh_token|api_key|authorization)["\']?\s*[:=]\s*)[^\s,}\]]+',r'\1[REDACTED]',s)
    return s[:800]
logs=subprocess.run(['docker','logs','--since','5m','--tail','250','hermes-agent'],capture_output=True,text=True)
for line in (logs.stdout+logs.stderr).splitlines():
    if any(x in line.lower() for x in ['error','warning','multiplex','opportunity','traceback','failed','exception','gateway','connecting','connected','profile']):
        if not any(x in line.lower() for x in ['user message','received message','message text']):print(redact(line))
for p in (root/'logs').glob('*gateway*'):
    if p.is_file():
        lines=p.read_text(errors='replace').splitlines()[-150:]
        for line in lines:
            if any(x in line.lower() for x in ['error','warning','multiplex','opportunity','traceback','failed','exception','profile','connecting','connected']):print(redact(line))
