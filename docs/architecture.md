# Архитектура

Читай перед изменением кода и перед деплоем. Подробности активации — `docs/ACTIVATION.md`,
восстановление — `docs/SETUP-RECOVERY.md`, scope MVP — `docs/IMPLEMENTATION.md`.

## Обзор
Отдельный профиль `opportunity-hunter` в существующей установке Hermes (многопрофильный Gateway рядом с
default-профилем). Поиск — родной `web_search` Hermes с ротацией русских и английских запросов, `web_extract`
только для изменившихся страниц. Отбор и проверку условий делает Hermes, SQLite хранит подтверждённые факты,
избранное и историю. Интерфейс — Telegram (естественный язык + детерминированные кнопки), расписание —
нативный Hermes Cron (вечерний дайджест в 22:00 по часовому поясу профиля).

Локальный CLI (`python -m opportunity_hunter.cli build|init|profile|health`) собирает припаркованный профиль
в `build/` и работает с данными в `runtime/`; он ничего не разворачивает и не шлёт сообщений.

## Интеграции и env
- Hermes Agent (профиль, плагин, skill, Cron), Telegram-бот профиля, OAuth-сессии Codex (общая квота).
- Данные хоста и операторские настройки — только в приватном файле вне репозитория
  (`OH_SETTINGS_FILE`, по умолчанию `~/Projects/hermes-platform/scripts/local.settings.ps1`).

## Грабли
- Gateway общий с default-профилем: перезапускать только профиль opportunity-hunter, default не трогать.
- В Git Bash переменные с путями `/opt/...` передавать с `MSYS_NO_PATHCONV=1`.
- Частичный или упавший поиск нельзя выдавать за «полное покрытие» или «возможностей нет».
- Повторный `build` поверх существующего каталога запрещён: для новой проверки — другой пустой `--output`.

## Запуск и деплой
- Локально: `python scripts/check.py` (или `python -m unittest discover -s tests -v`).
- **Деплой агентом — только через ProjectDeploy** (`check` → `deploy --commit <sha>`, см. `AGENTS.md`).
- Операторские скрипты в `scripts/` (`vps.py`, `backup_gateway.py`, `deploy_plugin.py`, `import_profile.py`,
  `telegram_setup.py`, откат и диагностика) запускает **владелец вручную**; агент их не вызывает. Они ходят
  на сервер только по приватному адресу, сначала проверяют подключение (`scripts/vps.py --check`), сверяют
  закреплённый ключ хоста и при ошибке останавливаются со стадией `PRIVATE_SSH_FAILED at …`.
  Ручной порядок заливки: бэкап (`backup_gateway.py`) → `deploy_plugin.py --env OH_BACKUP_DIR=…` →
  перезапуск только профиля opportunity-hunter → проверка (PID Gateway не изменился, default-профиль не тронут).
