"""Safe live metadata and supervisor inspection; no writes or model calls."""
from pathlib import Path
import json
import os
import subprocess
import hermes_yaml as yaml
from hermes_constants import get_hermes_home, get_default_hermes_root
from hermes_cli.gateway_multiplex_served import recorded_served_profiles
root = get_default_hermes_root()
cfg = yaml.safe_load((root/'config.yaml').read_text())
print(json.dumps({'home': str(get_hermes_home()), 'root':str(root),
 'model': {k: cfg.get('model',{}).get(k) for k in ['provider','default','api_mode']},
 'multiplex_config':cfg.get('gateway',{}).get('multiplex_profiles'),
 'served_profiles':recorded_served_profiles(),
 'new_profile_exists':(root/'profiles/opportunity-hunter').exists()}, indent=2))
for p in [Path('/run/service/gateway-default/run'), Path('/run/service/gateway-default/finish')]:
    print(str(p), p.read_text())
print(subprocess.check_output(['/command/s6-svstat','/run/service/gateway-default'],text=True))
print(subprocess.check_output(['/opt/hermes/.venv/bin/hermes','--version'],text=True))
