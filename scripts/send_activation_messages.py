"""Send one explicitly authorized activation check to each profile's configured owner."""
from pathlib import Path
import json,os,sys,urllib.request
sys.dont_write_bytecode=True
from dotenv import dotenv_values
for name,home,text in [
 ('opportunity-hunter',Path('/opt/data/profiles/opportunity-hunter'),
  'Opportunity Hunter подключён. Это тест доставки после активации. Cron пока PAUSED. Можно проверять: /oh health, /oh profile, /oh saved и обычные запросы о вакансиях, стипендиях, турнирах и обучении. Я ещё выполняю проверку качества поиска; неподтверждённые условия будут отмечены.'),
 ('default',Path('/opt/data'),
  'Проверка после управляемого перезапуска: основной профиль Hermes подключён. Его настройки и авторизация сохранены; расписание Opportunity Hunter пока выключено.')]:
    env=dotenv_values(home/'.env',interpolate=False)
    token=env.get('TELEGRAM_BOT_TOKEN') or ''
    owner=env.get('OH_OWNER_ID') or env.get('TELEGRAM_HOME_CHANNEL')
    try:
        payload={'chat_id':owner,'text':text,'disable_web_page_preview':True}
        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as response:result=json.loads(response.read(64000))
        print(json.dumps({'profile':name,'sent':bool(result.get('ok')),'message_id':result.get('result',{}).get('message_id')}),flush=True)
    except Exception:
        print(json.dumps({'profile':name,'sent':False,'error':'Telegram delivery outcome unknown; do not blindly retry'}),flush=True)
