"""Redacted live runtime/SQLite/Cron evidence; no prompts, messages or secrets printed."""
import os
from pathlib import Path
import datetime,json,sqlite3,sys
sys.dont_write_bytecode=True
root=(Path(os.environ['OH_HOST_ROOT'])/'data')
home=root/'profiles/opportunity-hunter'
state=json.loads((root/'gateway_state.json').read_text())
jobs=json.loads((home/'cron/jobs.json').read_text())
jobs=jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'gateway':{k:state.get(k) for k in ['gateway_state','pid','served_profiles','active_agents']},
 'platforms':{k:v.get('state') for k,v in state.get('platforms',{}).items() if isinstance(v,dict)},
 'cron':[{'name':j['name'],'state':j.get('state'),'enabled':j.get('enabled')} for j in jobs]}
with sqlite3.connect('file:'+str(home/'opportunities.db')+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    result['sqlite_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
    result['counts']={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ['items','sources','history','runs','notifications','page_cache','search_cache']}
    result['runs']=[dict(r) for r in db.execute('SELECT category,started_at,lease_until,ended_at,error FROM runs ORDER BY started_at DESC LIMIT 8')]
    result['usage']=[dict(r) for r in db.execute('SELECT * FROM research_usage')]
    result['records']=[{k:item.get(k) for k in ['id','category','title','url','status','deadline']} for item in [{**json.loads(r['data']),'id':r['id']} for r in db.execute('SELECT id,data FROM items')]]
print(json.dumps(result,ensure_ascii=True,indent=2))
