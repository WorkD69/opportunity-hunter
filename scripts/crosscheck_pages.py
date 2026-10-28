"""Fetch actual primary pages again and compare stored public quotes, without changing records."""
from pathlib import Path
import json,logging,os,re,sqlite3,sys
home=Path('/opt/data/profiles/opportunity-hunter')
sys.dont_write_bytecode=True
sys.path.insert(0,str(home/'plugins/opportunity-hunter'))
logging.disable(logging.CRITICAL)
from opportunity_hunter.research import PublicFetcher
normalize=lambda s:re.sub(r'\s+',' ',s).strip().lower()
with sqlite3.connect('file:'+str(home/'opportunities.db')+'?mode=ro',uri=True) as db:
    rows=list(db.execute('SELECT id,data FROM items'))
fetcher=PublicFetcher()
for item_id,data in rows:
    item=json.loads(data)
    try:
        page=fetcher(item['official_url'])
        text=normalize(page.plain_text)
        evidence=[ev for ev in item.get('evidence',[]) if ev.get('url')==item['official_url']]
        print(json.dumps({'id':item_id,'category':item['category'],'url':item['official_url'],
             'direct_fetch_ok':True,'direct_text_chars':len(page.plain_text),
             'primary_quotes_found':sum(normalize(ev['quote']) in text for ev in evidence),
             'primary_quotes_total':len(evidence),
             'amount_quote_found':any(ev.get('field')=='reward.amount' and normalize(ev['quote']) in text for ev in evidence)}),flush=True)
    except Exception as exc:
        print(json.dumps({'id':item_id,'category':item['category'],'url':item['official_url'],
          'direct_fetch_ok':False,'error_type':type(exc).__name__}),flush=True)
