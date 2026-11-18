"""Hot-deploy the whole Opportunity Hunter plugin package into its profile; no Gateway restart.

Every replaced file is saved first under OH_BACKUP_DIR/plugin-before/; afterwards the plugin is
reloaded through the gateway control socket and the script verifies: target hashes, the profile's
Telegram adapter re-wired, Gateway PID unchanged, default profile config/.env/auth/cron unchanged.

Usage (Git Bash: prefix with MSYS_NO_PATHCONV=1):
  python scripts/deploy_plugin.py --env OH_BACKUP_DIR=<verified backup dir>
"""
import argparse
import base64
import hashlib
from pathlib import Path

from vps import parse_env, run

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'opportunity_hunter'


def payload_files():
    files = {'plugins/opportunity-hunter/__init__.py': PACKAGE / 'assets/plugin/__init__.py',
             'plugins/opportunity-hunter/plugin.yaml': PACKAGE / 'assets/plugin/plugin.yaml',
             'skills/opportunity-hunter/SKILL.md': PACKAGE / 'assets/skill/SKILL.md',
             'AGENTS.md': PACKAGE / 'assets/AGENTS.md'}
    for module in sorted(PACKAGE.glob('*.py')):
        if module.name not in ('packaging.py', 'cli.py'):  # same exclusions as packaging.build
            files['plugins/opportunity-hunter/opportunity_hunter/' + module.name] = module
    return files


SOURCE = r'''from pathlib import Path
import base64,hashlib,json,os,subprocess,tempfile
root=Path(os.environ['OH_HOST_ROOT']).resolve()/'data'
home=root/'profiles/opportunity-hunter'
backup=Path(os.environ['OH_BACKUP_DIR']).resolve()
assert backup.parent==root.parent/'private-backups' and (backup/'manifest.json').is_file(), 'verified backup required'
assert home.resolve()==home and not (home/'gateway.parked').exists(), 'profile must be served (not parked)'
default_files=[root/n for n in ['config.yaml','.env','auth.json','cron/jobs.json'] if (root/n).exists()]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in default_files}
pid=json.loads((root/'gateway_state.json').read_text())['pid']
os.umask(0o077)
def safe_target(relative):
    # Root writes here: refuse any symlink in ANY component below the profile home, then confirm
    # the resolved path still lies inside it (string checks alone could be redirected).
    target=home/relative
    probe=target
    while probe!=home:
        assert not probe.is_symlink(), 'symlink in deploy path: '+relative
        probe=probe.parent
    assert target.resolve(strict=False).is_relative_to(home), relative
    return target
decoded={}
for relative,(encoded,digest) in PAYLOAD.items():  # verify the whole payload before touching anything
    data=base64.b64decode(encoded)
    assert hashlib.sha256(data).hexdigest()==digest, relative
    safe_target(relative)
    decoded[relative]=data
written={}
for relative,(encoded,digest) in PAYLOAD.items():
    target=safe_target(relative)
    for parent in reversed(target.relative_to(home).parents):
        folder=home/parent
        if not folder.exists():
            folder.mkdir(mode=0o700)
            os.chown(folder,10000,10000)
    data=decoded[relative]
    if target.exists():
        saved=backup/'plugin-before'/relative
        saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not saved.exists():
            saved.write_bytes(target.read_bytes())
            saved.chmod(0o600)
        if target.read_bytes()==data:
            continue
    fd,name=tempfile.mkstemp(prefix='.oh-deploy-',dir=target.parent)
    with os.fdopen(fd,'wb') as f:
        f.write(data)
    os.chmod(name,0o600)
    os.chown(name,10000,10000)
    os.replace(name,target)
    written[relative]=digest
assert all(hashlib.sha256((home/r).read_bytes()).hexdigest()==d for r,(_,d) in PAYLOAD.items())
code=("from pathlib import Path; import json; from gateway.control_socket import reload_gateway_plugins; "
      "r=reload_gateway_plugins(Path('/opt/data'),profile_home=Path('/opt/data/profiles/opportunity-hunter')); "
      "print(json.dumps({k:r.get(k) for k in ['reloaded','adapters_rewired','home']} if r else {'reloaded':False}))")
result=subprocess.run(['docker','exec','-u','hermes','-w','/opt/hermes','-e','HOME=/opt/data','-e','HERMES_HOME=/opt/data',
                       'hermes-agent','/opt/hermes/.venv/bin/python','-B','-c',code],capture_output=True,text=True,timeout=120)
reply=json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {'reloaded':False,'stderr':result.stderr[-300:]}
same_pid=json.loads((root/'gateway_state.json').read_text())['pid']==pid
unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
print(json.dumps({'files_written':sorted(written),'files_total':len(PAYLOAD),'plugin_reload':reply,
                  'gateway_pid_unchanged':same_pid,'default_files_unchanged':unchanged}))
raise SystemExit(0 if reply.get('reloaded') and same_pid and unchanged else 1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', action='append', metavar='KEY=VALUE', required=True)
    args = parser.parse_args()
    extra = parse_env(args.env)
    if 'OH_BACKUP_DIR' not in extra:
        raise SystemExit('--env OH_BACKUP_DIR=<verified backup dir> is required')
    payload = {}
    for relative, path in payload_files().items():
        data = path.read_bytes()
        payload[relative] = (base64.b64encode(data).decode(), hashlib.sha256(data).hexdigest())
    return run(SOURCE.replace('PAYLOAD', repr(payload)), extra_env=extra).returncode


if __name__ == '__main__':
    raise SystemExit(main())
