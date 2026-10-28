"""Compare credential-store structure and changes without displaying any values."""
import os
from pathlib import Path
import datetime,json
root=Path(os.environ['OH_HOST_ROOT'])
backup=sorted((root/'private-backups').glob('opportunity-hunter-preparation-*/manifest.json'))[-1].parent
old=json.loads((backup/'data/auth.json').read_text())
new=json.loads((root/'data/auth.json').read_text())

def compare(a,b,path='auth'):
    if type(a) != type(b): return [{'field':path,'change':'type'}]
    if isinstance(a,dict):
        out=[]
        for k in sorted(a.keys()|b.keys()):
            if k not in a or k not in b: out.append({'field':path+'.'+k,'change':'added' if k in b else 'removed'})
            else: out.extend(compare(a[k],b[k],path+'.'+k))
        return out
    if isinstance(a,list):
        if len(a)!=len(b):return [{'field':path,'change':'list length','before':len(a),'after':len(b)}]
        return [v for i,(aa,bb) in enumerate(zip(a,b)) for v in compare(aa,bb,path+f'[{i}]')]
    return [{'field':path,'change':'value changed; value withheld'}] if a!=b else []
old_rows=old.get('credential_pool',{}).get('openai-codex',[])
new_rows=new.get('credential_pool',{}).get('openai-codex',[])
pool_changes=[]
for row in new_rows:
    match=next((r for r in old_rows if r.get('id')==row.get('id')),None)
    pool_changes.append({'existed_by_id':bool(match),
      'changed_field_names':sorted(k for k in (row.keys()|match.keys()) if row.get(k)!=match.get(k)) if match else [],
      'new_entry_label_is_opportunity_hunter':row.get('label')=='opportunity-hunter',
      'token_pair_matches_previous_row':any(row.get('access_token')==r.get('access_token') and row.get('refresh_token')==r.get('refresh_token') for r in old_rows)})
print(json.dumps({'changed_fields':compare(old,new),
 'pool_changes':pool_changes,
 'old_pool_sources':[{'source':r.get('source'),'type':r.get('auth_type')} for r in old.get('credential_pool',{}).get('openai-codex',[])],
 'current_pool_sources':[{'source':r.get('source'),'type':r.get('auth_type')} for r in new.get('credential_pool',{}).get('openai-codex',[])],
 'existing_rows_preserved':all(r in new.get('credential_pool',{}).get('openai-codex',[]) for r in old.get('credential_pool',{}).get('openai-codex',[])),
 'singleton_providers_preserved':old.get('providers')==new.get('providers'),
 'current_mtime_utc':datetime.datetime.fromtimestamp((root/'data/auth.json').stat().st_mtime,datetime.timezone.utc).isoformat(),
 'backup_mtime_utc':datetime.datetime.fromtimestamp((backup/'data/auth.json').stat().st_mtime,datetime.timezone.utc).isoformat()},indent=2))
