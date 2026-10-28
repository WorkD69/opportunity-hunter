"""Use native profile config for bot checks; publish one-word commands on new bot only."""
from pathlib import Path
import json,logging,os,sys,urllib.error,urllib.request
os.environ.clear()
os.environ.update({'HOME':'/opt/data','HERMES_HOME':'/opt/data','PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin','LANG':'C.UTF-8'})
os.umask(0o077)
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
from dotenv import dotenv_values
from hermes_constants import set_hermes_home_override
from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope
from gateway.config import load_gateway_config,Platform
set_multiplex_active(True)
owner=dotenv_values('/opt/data/profiles/opportunity-hunter/.env',interpolate=False)['OH_OWNER_ID']
for name,home in [('default',Path('/opt/data')),('opportunity-hunter',Path('/opt/data/profiles/opportunity-hunter'))]:
    set_hermes_home_override(home)
    set_secret_scope(build_profile_secret_scope(home),profile_home=str(home))
    cfg=load_gateway_config()
    platform=cfg.platforms.get(Platform.TELEGRAM)
    token=platform.token if platform else None
    def call(method,payload=None):
        try:
            req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=json.dumps(payload or {}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=20) as response:return json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:return {'ok':False,'http_status':exc.code}
        except Exception:return {'ok':False,'outcome':'unknown'}
    me=call('getMe')
    out={'profile':name,'native_token_configured':bool(token),'getMe_ok':me.get('ok'),
         'username':me.get('result',{}).get('username')}
    if name=='default':
        # The earlier direct .env-only probe had no destination configured. The actual
        # gateway uses YAML+env; send the authorized check to the verified common owner.
        reply=call('sendMessage',{'chat_id':owner,'text':'Основной Hermes после перезапуска подключён. Текущая авторизация сохранена. Это проверка доставки; Opportunity Hunter работает отдельным профилем, его Cron пока PAUSED.'})
        out['delivery']={k:v for k,v in reply.items() if k in ['ok','http_status','outcome']}
        out['message_id']=reply.get('result',{}).get('message_id')
    else:
        names={'oh_health':'Состояние поиска и расписания','oh_profile':'Мой профиль поиска','oh_saved':'Избранные предложения',
               'oh_list':'Список предложений','oh_save':'Сохранить предложение: ID','oh_details':'Условия предложения: ID',
               'oh_hide':'Скрыть предложение: ID','oh_recheck':'Повторная проверка: ID','oh_instructions':'Как подать заявку самостоятельно: ID'}
        current=call('getMyCommands')
        commands=current.get('result') or []
        existing={row['command']:row for row in commands}
        existing.update({k:{'command':k,'description':v} for k,v in names.items()})
        menu=call('setMyCommands',{'commands':list(existing.values())[:100]})
        after=call('getMyCommands')
        actual={r['command'] for r in after.get('result',[])}
        out['menu_updated']=bool(menu.get('ok'))
        out['single_word_commands_verified']=set(names)<=actual
        out['commands']=list(names)
    print(json.dumps(out,ensure_ascii=True),flush=True)
