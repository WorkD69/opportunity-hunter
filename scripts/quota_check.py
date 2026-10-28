"""Read-only subscription quota metadata for explicit tokens; no refresh or paid API."""
from pathlib import Path
import json,logging,os,sys
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
from agent.account_usage import _fetch_codex_account_usage_read_only,_codex_snapshot_identity
identities=[]
tokens=[]
for name,home in [('default',Path('/opt/data')),('opportunity-hunter',Path('/opt/data/profiles/opportunity-hunter'))]:
    auth=json.loads((home/'auth.json').read_text())
    rows=auth.get('credential_pool',{}).get('openai-codex') or []
    selected=min(rows,key=lambda r:r.get('priority',0))
    identities.append(_codex_snapshot_identity(selected['access_token']))
    tokens.append(selected['access_token'])
    try:
        value=_fetch_codex_account_usage_read_only('https://chatgpt.com/backend-api/codex',selected['access_token'])
        print(json.dumps({'profile':name,'available':bool(value and value.available),
         'windows':[{'label':w.label,'used_percent':w.used_percent,'reset_at':w.reset_at.isoformat() if w.reset_at else None} for w in value.windows] if value else []}),flush=True)
    except Exception as exc:
        print(json.dumps({'profile':name,'available':False,'error_type':type(exc).__name__}),flush=True)
print(json.dumps({'account_identity_comparison_available':all(identities),
 'same_model_account':bool(all(identities) and identities[0]==identities[1]),
 'separate_oauth_tokens':len(set(tokens))==len(tokens)}),flush=True)
