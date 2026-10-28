"""Reviewed rollback plan. --apply is ONLY for a separately authorized maintenance window.

Run on the VPS host with Python 3. Never restore a stale auth.json or old runtime databases.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import tempfile

ROOT = Path(os.environ['OH_HOST_ROOT'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-current-config-sha256')
    args=parser.parse_args()
    backup=args.backup.resolve()
    assert backup.parent==ROOT/'private-backups' and backup.name.startswith('opportunity-hunter-preparation-')
    manifest=json.loads((backup/'manifest.json').read_text())
    assert manifest['verified']
    item=next(e for e in manifest['files'] if e['path']=='data/config.yaml')
    payload=(backup/'data/config.yaml').read_bytes()
    assert hashlib.sha256(payload).hexdigest()==item['sha256']
    profile=ROOT/'data/profiles/opportunity-hunter'
    assert profile.resolve()==profile and profile.is_dir() and not profile.is_symlink()
    assert all(not p.is_symlink() for p in profile.rglob('*'))
    destination=ROOT/'disabled-profiles'/('opportunity-hunter-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    assert destination.parent==ROOT/'disabled-profiles' and not destination.exists()
    config=ROOT/'data/config.yaml'
    current=hashlib.sha256(config.read_bytes()).hexdigest()
    plan={'mode':'APPLY' if args.apply else 'PLAN ONLY','backup_verified':True,
      'preserve_profile_at':str(destination),'current_config_sha256':current,
      'steps':['Mark new profile parked','Stop only gateway-default through installed S6ServiceManager',
               'Preserve entire new profile outside profiles/','Atomically restore ONLY original config.yaml with uid/gid/mode',
               'Start gateway-default through installed S6ServiceManager','Verify default Telegram, services and resources'],
      'auth_env_cron_databases_restored':False}
    print(json.dumps(plan,indent=2))
    if not args.apply: return
    assert args.expected_current_config_sha256==current, 'Expected current configuration hash required; review drift first'
    assert config.stat().st_mtime_ns > 0
    os.umask(0o077)
    (profile/'gateway.parked').touch(mode=0o600)

    def service(verb):
        code=f"from hermes_cli.service_manager import S6ServiceManager; S6ServiceManager().{verb}('gateway-default')"
        subprocess.run(['docker','exec','-u','hermes','-w','/opt/hermes','hermes-agent','/opt/hermes/.venv/bin/python','-c',code],check=True)
    service('stop')
    subprocess.run(['docker','exec','hermes-agent','/command/s6-svwait','-d','-t','30000','/run/service/gateway-default'],check=True)
    destination.parent.mkdir(mode=0o700,exist_ok=True)
    profile.rename(destination)
    fd,name=tempfile.mkstemp(prefix='.config.rollback.',dir=config.parent)
    with os.fdopen(fd,'wb') as f: f.write(payload)
    os.chmod(name,int(item['mode'],8))
    os.chown(name,item['uid'],item['gid'])
    os.replace(name,config)
    assert hashlib.sha256(config.read_bytes()).hexdigest()==item['sha256']
    service('start')
    print('Rollback applied; verify actual service behavior before claiming recovery.')


if __name__=='__main__': main()
