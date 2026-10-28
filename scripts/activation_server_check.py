"""Redacted host services, workloads and resources, without mutations."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import time

root=Path(os.environ['OH_HOST_ROOT'])
data=root/'data'
def state():
    value=json.loads((data/'gateway_state.json').read_text())
    out={k:value.get(k) for k in ['gateway_state','updated_at','active_agents','restart_requested','served_profiles','pid']}
    out['platforms']={k:{f:v.get(f) for f in ['state','connected','updated_at']} for k,v in value.get('platforms',{}).items() if isinstance(v,dict)}
    return out
def cpu():
    return [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
a=cpu()
time.sleep(1)
b=cpu()
total=sum(b)-sum(a)
idle=(b[3]+b[4])-(a[3]+a[4])
containers=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()]))
jobs=json.loads((data/'cron/jobs.json').read_text()) if (data/'cron/jobs.json').exists() else []
jobs=jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
active_cron=[]
dbpath=data/'cron/executions.db'
if dbpath.exists():
    with sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True) as db:
        active_cron=[dict(zip(['status','pid','claimed_at','started_at'],r)) for r in db.execute("SELECT status,pid,claimed_at,started_at FROM executions WHERE status IN ('claimed','running')")]
mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'gateway':state(),'gateway_pid_record':json.loads((data/'gateway.pid').read_text()).get('pid'),
 'containers':[{'name':c['Name'].lstrip('/'),'running':c['State']['Running'],'oom':c['State']['OOMKilled'],
    'health':c['State'].get('Health',{}).get('Status'),'started':c['State']['StartedAt'],
    'memory_limit_mib':c['HostConfig']['Memory']/1048576,'cpu_limit':c['HostConfig']['NanoCpus']/1e9} for c in containers],
 'protected_service':(subprocess.run(['systemctl','is-active',os.environ['OH_PROTECTED_SERVICE']],capture_output=True,text=True).stdout.strip() if os.environ.get('OH_PROTECTED_SERVICE') else 'not-configured'),
 'cpu_count':os.cpu_count(),'cpu_used_percent_1s':round(100*(total-idle)/total,1),'load_average':os.getloadavg(),
 'memory_mib':{k:round(mem[k]/1024,1) for k in ['MemTotal','MemAvailable','SwapTotal','SwapFree']},
 'disk_free_gib':round(__import__('shutil').disk_usage(root).free/1024**3,2),
 'existing_cron_count':len(jobs),'existing_cron_running_states':sum(j.get('state')=='running' for j in jobs),
 'active_cron_executions':active_cron,
 'baseline_files':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ['data/config.yaml','data/.env','data/auth.json','data/cron/jobs.json','compose.yaml','.env'] if (root/p).is_file()}}
print(json.dumps(result,indent=2))
print(subprocess.check_output(['docker','stats','--no-stream','--format','{{.Name}} {{.MemUsage}} {{.CPUPerc}}'],text=True))
