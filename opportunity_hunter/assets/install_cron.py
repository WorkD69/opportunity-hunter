"""Operator-run only, after approval: create paused native jobs in THIS profile.

Run from Hermes's Python environment with HERMES_HOME naming opportunity-hunter.
Does not start the Gateway or modify another profile's jobs.
"""
import json
import os
from pathlib import Path


def main():
    explicit_home = os.environ.get('HERMES_HOME', '')
    home = Path(explicit_home).resolve()
    if not explicit_home or home.name != 'opportunity-hunter' or not (home / 'config.yaml').is_file():
        raise SystemExit('Refusing: explicit Opportunity Hunter HERMES_HOME required')
    from hermes_constants import get_hermes_home
    if Path(get_hermes_home()).resolve() != home:
        raise SystemExit('Hermes profile scope mismatch')
    from tools.cronjob_tools import cronjob
    listed = json.loads(cronjob(action='list', include_disabled=True))
    if listed.get('success') is False or not isinstance(listed.get('jobs'), list):
        raise SystemExit('Cannot inspect native cron store; no jobs created')
    names = {j['name'] for j in listed['jobs']}
    for spec in json.loads((home / 'cron-spec.json').read_text(encoding='utf-8')):
        spec.pop('category', None)
        if spec['name'] in names:
            print(spec['name'] + ': already exists, left unchanged')
            continue
        result = json.loads(cronjob(action='create', paused=True, **spec))
        if result.get('success') is False:
            raise SystemExit('Native Cron rejected ' + spec['name'] + ': ' + str(result.get('error')))
        print(spec['name'] + ': created PAUSED; enable after gateway/Telegram verification')


if __name__ == '__main__':
    main()
