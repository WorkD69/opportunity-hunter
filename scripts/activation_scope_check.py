"""Native scoped path/plugin checks, without installing Cron or invoking a model."""
from pathlib import Path
import contextlib
import io
import json
import logging
import os
import sys
home=Path('/opt/data/profiles/opportunity-hunter')
os.environ.clear()
os.environ.update({'HOME':'/opt/data','HERMES_HOME':'/opt/data','PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin',
 'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
os.chdir(home)
os.umask(0o077)
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    from hermes_constants import set_hermes_home_override,get_hermes_home
    from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope,get_secret
    set_multiplex_active(True)
    set_hermes_home_override(home)
    scope=build_profile_secret_scope(home)
    set_secret_scope(scope,profile_home=str(home))
    os.environ['OH_FOREIGN_SCOPE_SENTINEL']='must-not-be-visible'
    from hermes_cli.config import load_config_readonly,validate_config_structure
    cfg=load_config_readonly()
    from agent.context_file_sources import list_context_file_sources
    sources=list_context_file_sources(cwd=str(home),home_override=home)
    paths=[Path(s['path']).resolve() for s in sources if s['status'] not in ['shadowed','empty','suppressed']]
    from hermes_cli.plugins import discover_plugins,get_plugin_manager
    discover_plugins()
    plugins=get_plugin_manager().list_plugins()
    own=[p for p in plugins if p['name']=='opportunity-hunter']
    from hermes_cli.auth import _auth_file_path
    from cron.jobs import JOBS_FILE,load_jobs,get_due_jobs
    jobs=load_jobs()
    result={'hermes_home_own':get_hermes_home()==home,'auth_path_own':_auth_file_path()==home/'auth.json',
      'cron_path_own':JOBS_FILE.resolve()==home/'cron/jobs.json','config_valid':not validate_config_structure(cfg),
      'context_files':[str(p.relative_to(home)) for p in paths if home in p.parents],
      'foreign_context_files':any(home not in p.parents for p in paths),
      'ambient_secret_blocked':get_secret('OH_FOREIGN_SCOPE_SENTINEL') is None,
      'foreign_api_keys_absent':not any(scope.get(k) for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','FIRECRAWL_API_KEY']),
      'plugin_ready':len(own)==1 and own[0]['enabled'] and not own[0]['error'],
      'all_five_paused':len(jobs)==5 and all(j.get('enabled') is False and j.get('state')=='paused' for j in jobs),
      'due_jobs_count':len(get_due_jobs()),'new_memories_file_count':len(list((home/'memories').glob('*'))),
      'os_isolation':False}
print(json.dumps(result,indent=2))
