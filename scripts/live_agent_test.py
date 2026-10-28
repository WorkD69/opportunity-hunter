"""Live native Hermes research turn in own profile; never schedules Cron or applies anywhere."""
from pathlib import Path
import contextlib,datetime,io,json,logging,os,sys,time
category=sys.argv[1]
assert category in 'ABCDQ' and len(category)==1
home=Path('/opt/data/profiles/opportunity-hunter')
os.environ.clear()
os.environ.update({'HOME':str(home/'home'),'HERMES_HOME':str(home),'PATH':'/opt/hermes/.venv/bin:/usr/bin:/bin',
 'USER':'hermes','LANG':'C.UTF-8','TZ':'Europe/Moscow','PYTHONDONTWRITEBYTECODE':'1'})
os.chdir(home)
os.umask(0o077)
sys.dont_write_bytecode=True
logging.disable(logging.CRITICAL)
def guard(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(args[0])).resolve()
        if path.name in {'auth.json','auth.lock','.env','.credentials.json'} and home not in path.parents:
            raise PermissionError('Foreign credential denied during live test')
sys.addaudithook(guard)
output=home/'operator/live-tests'
output.mkdir(mode=0o700,exist_ok=True)
started=time.monotonic()
tool_calls=[]
def tool_start(call_id,name,args=None,*extra,**kwargs):
    action=args.get('action') if isinstance(args,dict) else None
    tool_calls.append({'tool':str(name),'action':action})
    print(json.dumps({'category':category,'tool_started':str(name),'action':action}),flush=True)
topics={'A':'личные стипендии, гранты и денежные конкурсы/хакатоны',
 'B':'подходящие оплачиваемые junior/trainee Python/AI/FinTech/quant/crypto вакансии и стажировки',
 'C':'бесплатные simulated/paper/algorithm trading и количественные соревнования без депозита/реальных сделок',
 'D':'бесплатные образовательные и карьерные программы',
 'Q':'сравнение уже сохранённых результатов'}
prompt=f'''Это разрешённый ручной живой тест MVP, категория {category}: {topics[category]}. Сегодня 18 октября 2026, Москва.
Загрузи профиль и preferences через opportunity_hunter. Выполни один настоящий research по категории с разнообразными запросами (RU/EN) и проверь текущие официальные условия. Сохрани в SQLite через store хотя бы одну реальную найденную запись, если есть достаточная первичная цитата; можно сохранить closed/unknown/needs_clarification, если это честно отражает доказательства. Не придумывай активный набор или соответствие профилю. Не выдавай закрытый/неподходящий конкурс за рекомендацию. Отдельно проверяй актуальность, географию/гражданство/год выпуска и оплату/бесплатность.
Максимум один обычный research (до двух запросов) и один дополнительный целевой поиск, если нужен первоисточник; не расходуй бюджет на бесконечные повторы. Страницы читай полностью порциями; сохраняй точные короткие цитаты по каждому важному полю. При ошибке доступа сохрани статус ошибки, не обходи ограничения. Для C все четыре финансовых флага должны быть подтверждены первоисточником для рекомендации; если не подтверждены — unknown и needs_clarification. Не отправляй уведомления в этом тесте, не вызывай notify, Cron, send_message, не меняй профиль/preferences, не регистрируйся и не подавай заявок. Только opportunity_hunter+web+skills.
В конце дай кратко по-русски: запросы и источники, реальные названия/URL, ID сохранённых записей, статус/срок/деньги и все ограничения. Если актуальных проверенных подходящих предложений нет, прямо скажи это. Это независимый операторский агентный тест, не сообщение владельца в Telegram.'''
if category=='Q':
    prompt='Сравни уже сохранённые предложения Сбера и Т1 через opportunity_hunter list/details. Объясни по-русски место работы, оплату, актуальность набора и неизвестные условия. Только чтение ledger: не выполняй новый поиск, не меняй данные, не отправляй уведомления. Это тест произвольного запроса в действующем профиле, а не запрос регистрации.'
result={'category':category,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        from agent.secret_scope import set_multiplex_active,set_secret_scope,build_profile_secret_scope
        set_multiplex_active(True)
        set_secret_scope(build_profile_secret_scope(home),profile_home=str(home))
        from hermes_cli.config import load_config_readonly
        from hermes_cli.runtime_provider import resolve_runtime_provider
        cfg=load_config_readonly()
        runtime=resolve_runtime_provider(requested='openai-codex',target_model=cfg['model']['default'])
        own=json.loads((home/'auth.json').read_text())['credential_pool']['openai-codex']
        assert any(r.get('access_token')==runtime['api_key'] for r in own)
        from run_agent import AIAgent
        agent=AIAgent(model=cfg['model']['default'],provider=runtime['provider'],api_key=runtime['api_key'],
            base_url=runtime['base_url'],api_mode=runtime['api_mode'],credential_pool=runtime.get('credential_pool'),
            enabled_toolsets=['skills','web','opportunity_hunter'],disabled_toolsets=cfg['agent']['disabled_toolsets'],
            platform='cli',quiet_mode=True,max_iterations=24,run_budget_seconds=300,max_tokens=3500,
            reasoning_config={'effort':'low'},skip_background_review=True,load_soul_identity=True,
            ephemeral_system_prompt=(home/'skills/opportunity-hunter/SKILL.md').read_text(),
            tool_start_callback=tool_start,cwd=str(home))
        try:
            response=agent.run_conversation(prompt)
            # Persist full agent evidence only inside the private profile, not public logs.
            (output/(category+'-response.json')).write_text(json.dumps(response,ensure_ascii=False,default=str))
            answer=response.get('final_response') or response.get('response') or response.get('content') or ''
            if not answer:
                messages=response.get('messages') or []
                answer=next((m.get('content') for m in reversed(messages) if m.get('role')=='assistant' and m.get('content')), '')
            result.update({'ok':not bool(response.get('error') or response.get('failed')),'answer':answer,
               'response_keys':sorted(response.keys()),'tool_calls':tool_calls,
               'tokens':{k:response.get(k) for k in ['input_tokens','output_tokens','total_tokens','api_calls']}})
        finally:agent.close()
except Exception as exc:
    result.update({'ok':False,'error_type':type(exc).__name__})
result['elapsed_seconds']=round(time.monotonic()-started,1)
(output/(category+'-summary.json')).write_text(json.dumps(result,ensure_ascii=False,default=str))
print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
