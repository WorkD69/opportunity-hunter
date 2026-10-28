"""Validate installed own-profile Python/YAML/JSON and canonical command routing, without model calls."""
from pathlib import Path
import ast,contextlib,io,json,logging,os,py_compile,sys
home=Path('/opt/data/profiles/opportunity-hunter')
os.environ.clear()
os.environ.update({'HOME':'/opt/data','HERMES_HOME':str(home),'PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin','LANG':'C.UTF-8'})
os.umask(0o077)
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
cache=home/'operator/validation-cache'
cache.mkdir(mode=0o700,exist_ok=True)
files=[home/'plugins/opportunity-hunter/__init__.py',home/'plugins/opportunity-hunter/opportunity_hunter/runtime.py']
for n,p in enumerate(files):py_compile.compile(str(p),cfile=str(cache/(str(n)+'.pyc')),doraise=True)
import hermes_yaml as yaml
for p in [Path('/opt/data/config.yaml'),home/'config.yaml']:
    with p.open() as f:cfg=yaml.safe_load(f)
    assert isinstance(cfg,dict)
with (home/'cron/jobs.json').open() as f:jobs=json.load(f)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    from hermes_constants import set_hermes_home_override
    from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope
    set_multiplex_active(True)
    set_hermes_home_override(home)
    set_secret_scope(build_profile_secret_scope(home),profile_home=str(home))
    from hermes_cli.plugins import discover_plugins,get_plugin_commands,get_plugin_command_handler
    discover_plugins()
    commands=get_plugin_commands()
    names=['oh_health','oh_profile','oh_saved','oh_list','oh_save','oh_details','oh_hide']
    mapped={name:callable(get_plugin_command_handler(name.replace('_','-'))) for name in names}
print(json.dumps({'python_py_compile':True,'yaml_safe_load':True,'json_load':True,
 'telegram_normalized_handlers':mapped,'plugin_command_count':len(commands)}))
assert all(mapped.values())
