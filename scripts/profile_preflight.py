"""Native Hermes smoke checks in a short-lived, scrubbed profile process."""
from pathlib import Path
import json
import os
import runpy
import sqlite3
import sys

home = Path('/opt/data/profiles/opportunity-hunter')
os.environ.clear()
os.environ.update({'HOME': str(home/'home'), 'HERMES_HOME': str(home),
                   'PATH': '/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin',
                   'USER': 'hermes', 'LANG':'C.UTF-8', 'TZ':'Europe/Moscow',
                   'PYTHONDONTWRITEBYTECODE':'1'})
os.umask(0o077)
sys.dont_write_bytecode = True
assert (home/'gateway.parked').is_file()
from hermes_constants import get_hermes_home
assert get_hermes_home().resolve() == home
from hermes_cli.config import load_config_readonly, validate_config_structure
cfg = load_config_readonly()
issues = validate_config_structure(cfg)
print('CONFIG_VALIDATION', [str(x) for x in issues])
from agent.secret_scope import (set_multiplex_active, set_secret_scope,
                                build_profile_secret_scope, get_secret)
set_multiplex_active(True)
scope = build_profile_secret_scope(home)
set_secret_scope(scope, profile_home=str(home))
os.environ['OH_FOREIGN_SCOPE_SENTINEL'] = 'must-not-be-visible'
assert get_secret('OH_FOREIGN_SCOPE_SENTINEL') is None
assert not any(scope.get(k) for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','FIRECRAWL_API_KEY'])
assert cfg['mcp_servers'] == {}
assert {'terminal','file','browser','connections','delegation'} <= set(cfg['agent']['disabled_toolsets'])
from hermes_cli.plugins import get_plugin_manager, discover_plugins, get_plugin_command_handler
discover_plugins()
manager = get_plugin_manager()
plugins = manager.list_plugins()
own = [p for p in plugins if p['name'] == 'opportunity-hunter']
assert len(own) == 1 and own[0]['enabled'] and not own[0]['error'], own
assert own[0]['tools']==1 and own[0]['commands']==15, own
from tools.registry import registry
health = json.loads(registry.dispatch('opportunity_hunter', {'action':'health'}))
assert not health.get('error'), health
handler = get_plugin_command_handler('oh')
assert callable(handler)
try:
    result = handler('confirm unauthorized-preflight')
except PermissionError:
    pass
else:
    assert 'requires the owner' in str(result) or 'личном' in str(result), str(result)
print('NATIVE_PLUGIN', json.dumps(own))
print('HEALTH', json.dumps(health,ensure_ascii=False))
from cron.jobs import load_jobs, get_due_jobs, JOBS_FILE
assert JOBS_FILE.resolve() == home/'cron/jobs.json'
before=load_jobs()
assert not before or (len(before)==5 and {j['name'] for j in before}=={'oh-scan-A','oh-scan-B','oh-scan-C','oh-scan-D','oh-daily'}), 'Unexpected jobs; review rather than overwrite'
runpy.run_path(str(home/'install_cron.py'), run_name='__main__')
jobs = load_jobs()
expected = {'oh-scan-A','oh-scan-B','oh-scan-C','oh-scan-D','oh-daily'}
assert len(jobs)==5 and {j['name'] for j in jobs}==expected
assert all(j.get('enabled') is False and j.get('state')=='paused' for j in jobs), [(j['name'],j.get('enabled'),j.get('state')) for j in jobs]
assert get_due_jobs() == []
with sqlite3.connect(home/'opportunities.db') as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
print('CRON', json.dumps([{'name':j['name'],'enabled':j['enabled'],'state':j.get('state'),'schedule':j['schedule'],'deliver':j.get('deliver'),'skills':j.get('skills'),'enabled_toolsets':j.get('enabled_toolsets')} for j in jobs]))
print('PREFLIGHT_OK: native plugin, SQLite, secret scope, five PAUSED jobs; zero due jobs; no model or Telegram invocation')
