"""Post-activation read-only invariants and exact Cron configuration audit."""
from pathlib import Path
import datetime,hashlib,json,os,sqlite3,subprocess
from urllib.parse import urlsplit
root=Path(os.environ['OH_HOST_ROOT'])
data=root/'data'
home=data/'profiles/opportunity-hunter'
backup=Path(os.environ['OH_BACKUP_DIR'])
manifest=json.loads((backup/'manifest.json').read_text())
state=json.loads((data/'gateway_state.json').read_text())
unchanged={}
for e in manifest['files']:
    if e['path'] in ['data/.env','data/auth.json','data/cron/jobs.json','compose.yaml','.env','data/profiles/opportunity-hunter/cron/jobs.json']:
        p=root/e['path']
        unchanged[e['path']]=hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256'] if e['exists'] else not p.exists()
jobs=json.loads((home/'cron/jobs.json').read_text())
jobs=jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
expected=json.loads((home/'cron-spec.json').read_text())
audit=[]
for job in jobs:
    spec=next(x for x in expected if x['name']==job['name'])
    schedule=job.get('schedule')
    expr=schedule.get('expr') if isinstance(schedule,dict) else schedule
    expected_expr=spec['schedule']
    audit.append({'name':job['name'],'paused':job.get('enabled') is False and job.get('state')=='paused',
       'schedule':schedule,'expected_expression':expected_expr,'schedule_matches':expr==expected_expr,
       'workdir_own':job.get('workdir')=='/opt/data/profiles/opportunity-hunter',
       'delivery_local':job.get('deliver')=='local','skills':job.get('skills'),
       'enabled_toolsets':job.get('enabled_toolsets'),'prompt_matches':job.get('prompt')==spec['prompt']})
with sqlite3.connect('file:'+str(home/'opportunities.db')+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    domains={urlsplit(r[0]).hostname for r in db.execute('SELECT url FROM sources')}
    counts={n:db.execute('SELECT COUNT(*) FROM '+n).fetchone()[0] for n in ['items','history','sources','page_cache','search_cache','notifications']}
    usage=[dict(r) for r in db.execute('SELECT * FROM research_usage')]
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    active_research=db.execute('SELECT COUNT(*) FROM runs WHERE ended_at IS NULL').fetchone()[0]
containers=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()]))
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'gateway_state':state.get('gateway_state'),
 'gateway_pid':state.get('pid'),'same_pid_after_hot_reload':str(state.get('pid'))==os.environ.get('OH_EXPECTED_GATEWAY_PID'),
 'served_profiles':state.get('served_profiles'),'platforms':{k:v.get('state') for k,v in state.get('platforms',{}).items() if isinstance(v,dict)},
 'active_agents':state.get('active_agents'),'active_research_runs':active_research,
 'protected_files_unchanged':unchanged,'new_profile_not_parked':not (home/'gateway.parked').exists(),
 'cron':audit,'sqlite_integrity':integrity,'counts':counts,'source_domain_count':len(domains),'usage':usage,
 'containers':[{'name':c['Name'],'running':c['State']['Running'],'oom_killed':c['State']['OOMKilled'],'started_at':c['State']['StartedAt']} for c in containers],
 'protected_service':(subprocess.run(['systemctl','is-active',os.environ['OH_PROTECTED_SERVICE']],capture_output=True,text=True).stdout.strip() if os.environ.get('OH_PROTECTED_SERVICE') else 'not-configured'),
 'vps_mounts_only':all(not ':' in m.get('Source','') for c in containers for m in c.get('Mounts',[]))}
print(json.dumps(result,indent=2))
assert all(unchanged.values()) and integrity=='ok'
assert state.get('gateway_state')=='running' and set(state.get('served_profiles') or [])=={'default','opportunity-hunter'}
assert len(audit)==5 and all(j['paused'] and j['schedule_matches'] and j['workdir_own'] and j['delivery_local'] and j['prompt_matches'] for j in audit)
assert all(c['State']['Running'] and not c['State']['OOMKilled'] for c in containers)
assert result['protected_service']=='active'
