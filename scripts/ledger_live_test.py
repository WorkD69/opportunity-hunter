"""Live evidence-preserving SQLite/favorites/notification tests; never creates fictional offers."""
from pathlib import Path
import datetime,json,logging,os,re,sqlite3,sys
home=Path('/opt/data/profiles/opportunity-hunter')
sys.dont_write_bytecode=True
sys.path.insert(0,str(home/'plugins/opportunity-hunter'))
logging.disable(logging.CRITICAL)
os.umask(0o077)
from dotenv import dotenv_values
from opportunity_hunter.runtime import Runtime
secrets=dotenv_values(home/'.env',interpolate=False)
runtime=Runtime(home,lambda *args: (_ for _ in ()).throw(RuntimeError('No live search in ledger-only test')),secrets.get,lambda key:None)
result={}
with sqlite3.connect(home/'opportunities.db') as db:
    db.row_factory=sqlite3.Row
    rule=db.execute("SELECT url,text FROM page_cache WHERE url LIKE 'https://quote.trade/alphaleague/rules%' LIMIT 1").fetchone()
if rule:
    text=rule['text']
    index=text.lower().find('alphaleague')
    if index>=0:
        quote=text[max(0,index):index+200].strip()
        item={'category':'C','title':'AlphaLeague — Paper Division: требуется проверка условий',
          'organizer':'Quote.Trade','url':'https://quote.trade/alphaleague/','official_url':rule['url'],
          'identity_url':'https://quote.trade/alphaleague/','round':'2026','status':'unknown',
          'reward':{'type':'prize','amount':None},'cost_flags':{},'requirements':[],
          'evidence':[{'url':rule['url'],'quote':quote}],
          'next_actions':['Проверить полные правила, возраст, санкционные и региональные ограничения и условия получения призов.',
                          'Финансовые условия пока не подтверждены валидатором; не подключать кошелёк, API и не вносить средства.']}
        stored=runtime.handle({'action':'store','item':item})
        result['C_conservative_record']={'id':stored['id'],'status':stored['evaluation']['status'],
            'eligibility':stored['evaluation']['eligibility'],'free_status':stored['evaluation']['free_status']}
        assert stored['evaluation']['free_status']=='unverified'
        assert stored['id'] not in {r['id'] for r in runtime.store.notification_candidates()}

with sqlite3.connect(home/'opportunities.db') as db:
    db.row_factory=sqlite3.Row
    target=next((r for r in db.execute('SELECT id,data,bookmarked FROM items') if json.loads(r['data'])['category']=='B'),None)
assert target is not None,'No real B record for ledger test'
item_id=target['id']
baseline=runtime.store.stats()
raw=json.loads(target['data'])
one=runtime.handle({'action':'store','item':raw})
two=runtime.handle({'action':'store','item':raw})
after=runtime.store.stats()
result['repeated_store']={'same_id':one['id']==two['id']==item_id,
    'same_item_count':baseline['items']==after['items'],'same_history_count':baseline['history']==after['history']}
assert all(result['repeated_store'].values())
was_bookmarked=bool(target['bookmarked'])
runtime.handle({'action':'save','id':item_id})
assert item_id in {r['id'] for r in runtime.handle({'action':'list','bookmarked':True})}
if not was_bookmarked:runtime.handle({'action':'unsave','id':item_id})
assert runtime.store.get(item_id)['bookmarked']==was_bookmarked
result['favorites']={'save_and_read_verified':True,'previous_state_restored':True,'tested_id':item_id}
try:runtime.command('profile')
except PermissionError:result['unauthenticated_command_rejected']=True
else:raise AssertionError('Owner gate failed')

preview=runtime.handle({'action':'notify','kind':'new','dry_run':True})
first=runtime.handle({'action':'notify','kind':'new'})
second=runtime.handle({'action':'notify','kind':'new'})
result['notifications']={'preview_count':len(preview['messages']),'first':first,'second':second}
assert not second.get('outcomes'), 'Repeat notification emitted'
with sqlite3.connect(home/'opportunities.db') as db:
    result['sqlite_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
    result['duplicate_aliases']=db.execute('SELECT COUNT(*) FROM (SELECT alias FROM aliases GROUP BY alias HAVING COUNT(*)>1)').fetchone()[0]
    result['notification_rows']=[{'state':state,'message_id_recorded':bool(mid)} for state,mid in db.execute('SELECT state,message_id FROM notifications')]
result['final_stats']={k:v for k,v in runtime.store.stats().items() if k not in ['latest_runs','latest_errors']}
result['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(home/'operator/live-tests/ledger-summary.json').write_text(json.dumps(result,ensure_ascii=False))
print(json.dumps(result,ensure_ascii=True,indent=2))
