"""Inspect public offer evidence and errors, without the owner's private profile or conversations."""
import os
from pathlib import Path
import json,sqlite3
home=(Path(os.environ['OH_HOST_ROOT'])/'data/profiles/opportunity-hunter')
with sqlite3.connect('file:'+str(home/'opportunities.db')+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    result=[]
    for row in db.execute('SELECT id,data FROM items'):
        item=json.loads(row['data'])
        out={k:item.get(k) for k in ['category','title','url','official_url','status','deadline','round','reward','cost_flags','requirements']}
        out['id']=row['id']
        out['evidence']=item.get('evidence')
        result.append(out)
    print(json.dumps(result,ensure_ascii=True,indent=2))
