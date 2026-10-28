"""Minimal native Codex request with foreign credential reads denied; only safe booleans emitted."""
from pathlib import Path
import contextlib
import datetime
import io
import json
import logging
import os
import sys

is_default = '--default' in sys.argv
home = Path('/opt/data') if is_default else Path('/opt/data/profiles/opportunity-hunter')
os.environ.clear()
os.environ.update({'HOME':str(home if is_default else home/'home'),'HERMES_HOME':str(home),
    'PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin','USER':'hermes','LANG':'C.UTF-8',
    'TZ':'Europe/Moscow','PYTHONDONTWRITEBYTECODE':'1'})
os.chdir(home)
os.umask(0o077)
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
denied=[]
def guard(event,args):
    if event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):
        return
    path=Path(os.fsdecode(args[0])).resolve()
    if path.name in {'auth.json','auth.lock','.env','.credentials.json'}:
        allowed = path.parent==home or home in path.parents
        if is_default and Path('/opt/data/profiles') in path.parents:
            allowed=False
        if not allowed:
            denied.append(path.name)
            raise PermissionError('Foreign credential file denied')
sys.addaudithook(guard)
result={'profile':'default' if is_default else 'opportunity-hunter',
 'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'foreign_credential_guard':True,'model_call_succeeded':False}
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope
        set_multiplex_active(True)
        set_secret_scope(build_profile_secret_scope(home),profile_home=str(home))
        from hermes_cli.config import load_config_readonly
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_cli.auth import _auth_file_path
        cfg=load_config_readonly()
        assert _auth_file_path().resolve()==home/'auth.json'
        state=json.loads((home/'auth.json').read_text()) if (home/'auth.json').exists() else {}
        rows=state.get('credential_pool',{}).get('openai-codex') or []
        result['own_oauth_rows']=len(rows)
        if not rows:
            raise RuntimeError('Own OAuth missing')
        runtime=resolve_runtime_provider(requested='openai-codex',target_model=cfg['model']['default'])
        assert runtime['provider']=='openai-codex' and runtime['api_mode']=='codex_responses'
        assert runtime['base_url'].rstrip('/')=='https://chatgpt.com/backend-api/codex'
        after=json.loads((home/'auth.json').read_text())
        rows=after.get('credential_pool',{}).get('openai-codex') or []
        selected=[i for i,r in enumerate(rows) if r.get('access_token')==runtime['api_key']]
        assert len(selected)==1,'Selected token must be in this profile pool'
        result['selected_token_in_own_pool']=True
        if is_default:
            result['selected_row_highest_priority']=rows[selected[0]].get('priority',0)==min(r.get('priority',0) for r in rows)
            result['selected_row_index']=selected[0]
        from openai import OpenAI
        from agent.auxiliary_client import CodexAuxiliaryClient,_codex_cloudflare_headers
        real=OpenAI(api_key=runtime['api_key'],base_url=runtime['base_url'],
             default_headers=_codex_cloudflare_headers(runtime['api_key'],base_url=runtime['base_url']),
             timeout=40,max_retries=0)
        client=CodexAuxiliaryClient(real,cfg['model']['default'])
        try:
            response=client.chat.completions.create(model=cfg['model']['default'],
                messages=[{'role':'system','content':'Reply briefly with the exact requested text.'},
                          {'role':'user','content':'Return exactly: OH_AUTH_OK'}],
                max_tokens=32,timeout=45)
            answer=response.choices[0].message.content or ''
            result['model_call_succeeded']='OH_AUTH_OK' in answer
            result['nonempty_response']=bool(answer.strip())
            result['model']=cfg['model']['default']
        finally:
            client.close()
except Exception as exc:
    result['error_type']=type(exc).__name__
    if isinstance(getattr(exc,'status_code',None),int):
        result['http_status']=exc.status_code
    result['own_auth_file_missing']=not (home/'auth.json').exists()
result['foreign_credential_reads_blocked']=len(denied)
print(json.dumps(result,indent=2))
