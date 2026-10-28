# Команды активации: выполнены 18.10.2026

По разрешению владельца выполнен один управляемый цикл Gateway через s6. Оба профиля подключены; все пять Cron остаются PAUSED. Повторно выполнять команды ниже сейчас не требуется. Результаты и ограничения: [отчёт запуска](../reports/ACTIVATION-LIVE.md). Далее сохранена исходная инструкция для согласованного окна обслуживания.

Выполняет оператор после отдельного разрешения на Gateway. Cron требует другого разрешения после живых тестов. Собственные credentials нового профиля проверены 18.10.2026; перед выполнением нужны свежая проверка и разрешение владельца.

Перед окном повторить проверки Telegram, собственный запрос модели с запретом чужих credentials, проверку PAUSED, ресурсов, свежести heartbeat и `active_agents == 0`, отсутствие claimed/running Cron. Сделать новый приватный snapshot, проверить SHA-256 и PLAN ONLY отката. Нулевой счётчик сейчас не гарантирует отсутствия новой задачи к моменту разрешения.

Следующие команды предназначены для SSH-сеанса на VPS. Используется установленный supervisor `/run/service/gateway-default`. Остановка и запуск через S6ServiceManager проверены по установленному исходному коду. Между ними обязательно дождаться фактического завершения процесса. `restart()` использует SIGTERM, поэтому явная последовательность stop/wait/start позволяет проверить остановку и не полагаться на автоматический повторный запуск после clean exit.

```bash
docker exec -u hermes -w /opt/hermes -e HOME=/opt/data -e HERMES_HOME=/opt/data hermes-agent /opt/hermes/.venv/bin/python -c "from hermes_cli.service_manager import S6ServiceManager; S6ServiceManager().stop('gateway-default')"
docker exec hermes-agent /command/s6-svwait -d -t 30000 /run/service/gateway-default
docker exec -u hermes -w /opt/hermes -e HOME=/opt/data -e HERMES_HOME=/opt/data hermes-agent /opt/hermes/.venv/bin/hermes config set gateway.multiplex_profiles true
docker exec -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python -c "from pathlib import Path; p=Path('/opt/data/profiles/opportunity-hunter/gateway.parked'); assert p.is_file() and not p.is_symlink(); p.unlink()"
docker exec -u hermes -w /opt/hermes -e HOME=/opt/data -e HERMES_HOME=/opt/data hermes-agent /opt/hermes/.venv/bin/python -c "from hermes_cli.service_manager import S6ServiceManager; S6ServiceManager().start('gateway-default')"
docker exec hermes-agent /command/s6-svwait -u -t 30000 /run/service/gateway-default
docker exec hermes-agent /command/s6-svstat /run/service/gateway-default
```

Выполнять по одной, прекращать при любой ошибке. После `config set` до снятия marker проверить чтением YAML, что единственная смысловая правка — `gateway.multiplex_profiles: true`; собственные настройки default не изменились. При ошибке оставить новый профиль parked и вернуть исходный config из свежего snapshot до старта default. Успех s6 означает запуск процесса; готовность Telegram и двух served_profiles проверяется отдельно по свежему runtime state и живым тестам.

Старый Hermes временно потеряет Telegram/API-соединение и запустится в новом общем Gateway с прежними профильными данными. Начатый в промежутке диалог может прерваться; перед остановкой требуется повторная проверка активности. Пять новых Cron останутся PAUSED. Остальные контейнеры, соседний сервис и compose не перезапускаются.

Полный откат: скрипт `rollback_gateway.py` рядом со свежим snapshot, сначала без `--apply`. Только после согласования окна — `--apply --expected-current-config-sha256 <проверенный хеш>`. Он сохраняет весь новый профиль вне обнаруживаемого `profiles/`, восстанавливает только default config и запускает default через s6. Текущая авторизация default, env, память и базы не заменяются устаревшими копиями. Исполнение отката с остановкой службы до разрешения не проверялось.

После успешного запуска: реальный поиск A/B/C/D, Telegram-доставка и ответы владельца, SQLite и повтор той же находки без дубля, проверка состояния соседнего проекта и совместных ресурсов. Регулярное расписание включается только после живых тестов и отдельного подтверждения владельца; paused-задачи не запускать `cronjob run`, который в этой версии может требовать возобновления.
