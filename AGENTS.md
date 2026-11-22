# opportunity-hunter

## Project
Opportunity Hunter — профиль и плагин Hermes Agent: ищет возможности (стажировки, конкурсы и т.п.) под анкету
владельца, хранит подтверждённые факты в SQLite и шлёт уведомления и вечерний дайджест в Telegram.
Прикладной код — Python 3.12+, без внешних зависимостей.

Type: pet. **Репозиторий публичный.** Scope и решения MVP: `docs/IMPLEMENTATION.md`.

## Map
- `opportunity_hunter/` — пакет: `core.py` (SQLite, фильтр), `research.py` (поиск), `buttons.py` (Telegram),
  `runtime.py`, `packaging.py`, `cli.py`; `assets/` — то, что уходит в профиль Hermes (plugin, skill, config, cron).
- `tests/` — unittest.
- `scripts/` — операторские скрипты для сервера (запускает владелец, см. `docs/architecture.md`);
  `scripts/check.py` — единая проверка.
- `docs/architecture.md` — как система устроена, деплой, грабли (читай перед изменением кода и деплоем).
- `docs/` — активация, аудит, восстановление; `reports/` — отчёты живых проверок (сырые логи не коммитятся).
- `build/`, `runtime/`, `tmp/` — локальные, в git не попадают.

## Workflow
Phases: `grill` → `to-spec` → `to-tickets` (GitHub Issues) → `implement` → `review` → merge.
- Работа в feature-ветке, PR в `main`, merge после зелёного `scripts/check.py` и разобранного ревью.
- Пушить только явно указанную ветку (`git push origin <branch>`), никогда `--all` / `--mirror`.
- Мелкие конкретные правки идут без grill/spec, но через проверки.

## Checks
`python scripts/check.py` перед каждым коммитом и перед «готово»: `unittest discover -s tests` + gitleaks
по всему, что попадёт в коммит. Red = not done. Чинить код, а не конфиг проверок.

## Deploy
Только через ProjectDeploy, только по поручению владельца и с конкретным коммитом:
`check opportunity-hunter` → `deploy opportunity-hunter --commit <sha>` (команды — в `CLAUDE.local.md`).
Ошибка → остановись и сообщи этап. Никаких повторов и обходных путей.
Агент не ходит на сервер напрямую по SSH и не запускает операторские скрипты из `scripts/`.

## Boundaries
- Публичный репо: в git не попадают персональные данные (в том числе в тестовых примерах), адреса и имена
  хостов, пути сервера, секреты, `tmp/`, `runtime/`, мастер-профиль анкеты. Перед коммитом — поиск утечек
  по diff; нашёл — не коммить, пока не исправишь.
- Docs disagree with the code → the code is right: fix the doc or ask.
- Secrets come from env only and never appear in logs.
- Ask before: deleting data, changing the DB schema or plugin/CLI contracts, adding a paid service.
- Вне scope: подача заявок, регистрации, отправка резюме и почты, финансовые аккаунты.
