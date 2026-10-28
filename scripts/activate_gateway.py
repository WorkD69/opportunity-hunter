"""Approved one-service s6 activation, guarded against drift/activity; Cron untouched."""
from pathlib import Path
import datetime,hashlib,json,os,sqlite3,subprocess,time
ROOT=Path(os.environ['OH_HOST_ROOT'])
DATA=ROOT/'data'
PROFILE=DATA/'profiles/opportunity-hunter'
BACKUP=Path(os.environ['OH_BACKUP_DIR'])
manifest=json.loads((BACKUP/'manifest.json').read_text())
def emit(stage,**kwargs):print(json.dumps({'stage':stage,**kwargs}),flush=True)
def container(args):
    return subprocess.run(['docker','exec','-u','hermes','-w','/opt/hermes','-e','HOME=/opt/data','-e','HERMES_HOME=/opt/data','hermes-agent',*args],capture_output=True,text=True,check=True)
def service(verb):
    return container(['/opt/hermes/.venv/bin/python','-B','-c',"from hermes_cli.service_manager import S6ServiceManager; S6ServiceManager()."+verb+"('gateway-default')"])
def digest():
    code="""from pathlib import Path
import hashlib,json,hermes_yaml as yaml
p=yaml.safe_load(Path('/opt/data/config.yaml').read_text())
flag=p.get('gateway',{}).pop('multiplex_profiles',None)
if p.get('gateway')=={}:p.pop('gateway',None)
print(json.dumps({'semantic_hash':hashlib.sha256(json.dumps(p,sort_keys=True,default=str).encode()).hexdigest(),'multiplex':flag}))
"""
    return json.loads(container(['/opt/hermes/.venv/bin/python','-B','-c',code]).stdout)
def preconditions():
    assert (PROFILE/'gateway.parked').is_file(),'Profile marker changed'
    for e in manifest['files']:
        if e['exists']:assert hashlib.sha256((BACKUP/e['path']).read_bytes()).hexdigest()==e['sha256'],'Backup mismatch'
        if e['path'] in ['data/config.yaml','data/.env','data/auth.json','data/cron/jobs.json','compose.yaml','.env','data/profiles/opportunity-hunter/config.yaml','data/profiles/opportunity-hunter/.env','data/profiles/opportunity-hunter/auth.json','data/profiles/opportunity-hunter/cron/jobs.json']:
            p=ROOT/e['path']
            assert (hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256'] if e['exists'] else not p.exists()),'Configuration/credentials drift'
    state=json.loads((DATA/'gateway_state.json').read_text())
    assert state['gateway_state']=='running' and state['active_agents']==0 and not state.get('restart_requested'),'Gateway busy or unhealthy'
    stamp=datetime.datetime.fromisoformat(state['updated_at'].replace('Z','+00:00'))
    assert (datetime.datetime.now(datetime.timezone.utc)-stamp).total_seconds()<90,'Stale heartbeat'
    for home in [DATA,PROFILE]:
        path=home/'cron/jobs.json'
        jobs=json.loads(path.read_text()) if path.exists() else []
        jobs=jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
        if home==PROFILE:assert len(jobs)==5 and all(j.get('enabled') is False and j.get('state')=='paused' for j in jobs),'Cron not paused'
        assert all(j.get('state')!='running' for j in jobs),'Running Cron'
        ledger=home/'cron/executions.db'
        if ledger.exists():
            with sqlite3.connect('file:'+str(ledger)+'?mode=ro',uri=True) as db:
                assert db.execute("SELECT COUNT(*) FROM executions WHERE status IN ('claimed','running')").fetchone()[0]==0,'Active Cron ledger'
    mem={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()}
    assert mem['MemAvailable']>500*1024,'RAM below safe threshold'
    assert __import__('shutil').disk_usage(ROOT).free>1024**3,'Disk below safe threshold'
    inspect=json.loads(subprocess.check_output(['docker','inspect','hermes-agent']))[0]
    assert inspect['State']['Running'] and not inspect['State']['OOMKilled'] and inspect['State']['StartedAt']==manifest['container_started'],'Container state changed'
    assert (subprocess.run(['systemctl','is-active',os.environ['OH_PROTECTED_SERVICE']],capture_output=True,text=True).stdout.strip() if os.environ.get('OH_PROTECTED_SERVICE') else 'not-configured')=='active','Protected neighbour service unhealthy or OH_PROTECTED_SERVICE not set'
    return state

stopped=False
try:
    before_semantic=digest()
    assert before_semantic['multiplex'] is None,'Unexpected multiplex flag'
    preconditions()
    time.sleep(2)
    state=preconditions()
    emit('PRESTOP_CHECK_OK',active_agents=0,cron_running=0,old_pid=state.get('pid'))
    service('stop')
    stopped=True
    emit('STOP_REQUESTED')
    subprocess.run(['docker','exec','hermes-agent','/command/s6-svwait','-d','-t','30000','/run/service/gateway-default'],check=True,capture_output=True)
    emit('STOP_CONFIRMED')
    container(['/opt/hermes/.venv/bin/hermes','config','set','gateway.multiplex_profiles','true'])
    after_semantic=digest()
    assert after_semantic['multiplex'] is True and after_semantic['semantic_hash']==before_semantic['semantic_hash'],'Unexpected config semantic change'
    emit('ONLY_MULTIPLEX_CONFIG_CHANGED')
    container(['/opt/hermes/.venv/bin/python','-B','-c',"from pathlib import Path; p=Path('/opt/data/profiles/opportunity-hunter/gateway.parked'); assert p.is_file() and not p.is_symlink(); p.unlink()"])
    service('start')
    emit('START_REQUESTED')
    subprocess.run(['docker','exec','hermes-agent','/command/s6-svwait','-u','-t','30000','/run/service/gateway-default'],check=True,capture_output=True)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        state=json.loads((DATA/'gateway_state.json').read_text())
        platforms=state.get('platforms',{})
        if state.get('gateway_state')=='running' and len(state.get('served_profiles') or [])>=2:
            emit('GATEWAY_RUNNING',pid=state.get('pid'),served_profiles=state.get('served_profiles'),
                 platforms={k:v.get('state') for k,v in platforms.items() if isinstance(v,dict)})
            break
        time.sleep(3)
    else:raise RuntimeError('Gateway failed readiness deadline')
    emit('ACTIVATION_COMPLETE',cron_untouched=True)
except Exception as exc:
    emit('ACTIVATION_FAILED',error_type=type(exc).__name__,stage_after_stop=stopped)
    if stopped:
        current=hashlib.sha256((DATA/'config.yaml').read_bytes()).hexdigest()
        result=subprocess.run(['python3',str(BACKUP/'rollback_gateway.py'),'--backup',str(BACKUP),'--apply','--expected-current-config-sha256',current],capture_output=True,text=True)
        emit('AGREED_ROLLBACK',exit_code=result.returncode)
    raise SystemExit(1) from None
