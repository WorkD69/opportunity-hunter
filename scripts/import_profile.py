"""Import the owner-reviewed private master profile into the profile's SQLite on the VPS.

Dry run by default: prints what would change and does not change profile data (opening the
database may still apply schema migrations). With --apply the live database is
first copied by SQLite online backup into the profile's backups/ directory (integrity-checked).
The file travels over SSH stdin, never in argv; only a summary without personal values is printed.

  python scripts/import_profile.py tmp/profile/master.json [--apply]
"""
import argparse
import base64
import json
from pathlib import Path

from vps import run

SOURCE = r'''import base64,datetime,json,os,sqlite3,sys
from pathlib import Path
home=Path('/opt/data/profiles/opportunity-hunter')
sys.path.insert(0,str(home/'plugins/opportunity-hunter'))
from opportunity_hunter.core import Store
master=json.loads(base64.b64decode(__PAYLOAD__).decode('utf-8'))
apply=__APPLY__
db=home/'opportunities.db'
backup=None
if apply:
    os.umask(0o077)
    folder=home/'backups'
    folder.mkdir(exist_ok=True,mode=0o700)
    backup=folder/('before-profile-import-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.db')
    with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
summary=Store(db).import_master(master,apply=apply)
summary['backup']=backup.name if backup else None
print(json.dumps(summary,ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    master = json.loads(args.file.read_text(encoding='utf-8'))  # fail locally on invalid JSON
    encoded = base64.b64encode(json.dumps(master, ensure_ascii=False).encode('utf-8')).decode()
    # Markers contain '_' which never occurs in standard base64, and APPLY is substituted first.
    source = SOURCE.replace('__APPLY__', repr(bool(args.apply))).replace('__PAYLOAD__', repr(encoded))
    return run(source, container=True).returncode


if __name__ == '__main__':
    raise SystemExit(main())
