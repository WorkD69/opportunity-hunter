"""Pin deployed cwd to the profile and audit native prompt paths without model calls."""
from pathlib import Path
import hashlib,json,os,sys
home=Path('/opt/data/profiles/opportunity-hunter')
assert (home/'gateway.parked').is_file()
os.environ.clear()
os.environ.update({'HOME':'/opt/data','HERMES_HOME':'/opt/data',
 'PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin','LANG':'C.UTF-8'})
os.umask(0o077)
sys.dont_write_bytecode=True
config=home/'config.yaml'
text=config.read_text()
old_hash=hashlib.sha256(text.encode()).hexdigest()
if '  cwd: /opt/data/profiles/opportunity-hunter\n' not in text:
    assert text.count('terminal:\n')==1
    text=text.replace('terminal:\n','terminal:\n  cwd: /opt/data/profiles/opportunity-hunter\n')
    config.write_text(text)
from hermes_constants import set_hermes_home_override
from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope
set_multiplex_active(True)
set_hermes_home_override(home)
set_secret_scope(build_profile_secret_scope(home),profile_home=str(home))
from tools.terminal_scope import build_profile_terminal_scope
scope=build_profile_terminal_scope(home)
assert scope['TERMINAL_CWD']==str(home)
from agent.context_file_sources import list_context_file_sources
sources=list_context_file_sources(cwd=str(home),home_override=home)
paths=[Path(s['path']).resolve() for s in sources if s['status'] not in ['shadowed','empty','suppressed']]
assert paths and all(home in p.parents for p in paths), paths
assert home/'AGENTS.md' in paths and home/'SOUL.md' in paths
from hermes_cli.config import load_config_readonly,validate_config_structure
assert not validate_config_structure(load_config_readonly())
from cron.jobs import load_jobs,get_due_jobs
from tools.cronjob_tools import cronjob
jobs=load_jobs()
assert len(jobs)==5 and all(j.get('enabled') is False and j.get('state')=='paused' for j in jobs)
for job in jobs:
    result=json.loads(cronjob(action='update',job_id=job['id'],workdir=str(home)))
    assert result.get('success') is not False,result
jobs=load_jobs()
assert all(j.get('workdir')==str(home) and j.get('enabled') is False and j.get('state')=='paused' for j in jobs)
assert get_due_jobs()==[]
print(json.dumps({'terminal_cwd':scope['TERMINAL_CWD'],'context_files':[str(p.relative_to(home)) for p in paths],
 'cron_workdir_pinned':True,'all_five_jobs_still_paused':True,
 'foreign_context_files':False,'deployment_config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),
 'previous_config_sha256':old_hash,'model_called':False},indent=2))
