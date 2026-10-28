"""Read installed source, never private configuration contents."""
from pathlib import Path
import json
root = Path('/opt/hermes')
for name in ['hermes_cli/gateway_multiplex_mode.py', 'hermes_cli/gateway_multiplex_s6.py',
             'agent/secret_scope.py', 'hermes_constants.py']:
    print('\nSOURCE:', name)
    print((root / name).read_text())
print('S6 SERVICE FILES')
for directory in ['/etc/s6-overlay/s6-rc.d', '/run/service']:
    p = Path(directory)
    print(directory, [x.name for x in p.iterdir()] if p.exists() else [])
for p in root.glob('hermes_cli/*auth*'):
    print('AUTH SOURCE', str(p), p.stat().st_size)
print('PROFILE FILES', [str(p.relative_to(root)) for p in root.glob('hermes_cli/*profile*')])
