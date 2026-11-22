"""Small profile-scoped facade for native Hermes tools and trusted commands."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import buttons
from .core import Store
from .research import ResearchEngine

CATEGORY_NAMES = {'A': 'Стипендии, гранты и денежные конкурсы', 'B': 'Работа и стажировки',
                  'C': 'Бесплатные торговые соревнования', 'D': 'Карьерные и образовательные программы'}
FIT = {'eligible': 'условия допуска подтверждены', 'needs_clarification': 'требует уточнения',
       'ineligible': 'есть несоответствие условиям'}
STATE = {'active': 'активно', 'closed': 'завершено', 'unknown': 'актуальность требует проверки'}


def format_card(item):
    ev = item['evaluation']
    reward = item.get('reward') or {}
    reward_type = {'salary': 'зарплата', 'prize': 'денежный приз', 'project_grant': 'целевой грант',
                   'personal_scholarship': 'личная стипендия', 'career': 'карьерная возможность'}.get(
                       reward.get('type'), 'тип выплаты не подтверждён')
    reasons = '\n'.join('• ' + str(x)[:250] for x in ev.get('reasons', [])[:6])
    labels = {'year': 'курс', 'degree': 'образование', 'citizenship': 'гражданство',
              'residence': 'проживание', 'age': 'возраст', 'english_level': 'английский',
              'weekly_hours': 'часов в неделю', 'work_authorization': 'право на работу',
              'study_end': 'год выпуска', 'full_time': 'очная форма', 'gpa': 'успеваемость'}
    operators = {'eq': '=', 'in': 'допустимо', 'min': 'от', 'max': 'до'}
    conditions = '; '.join(f"{labels.get(r['field'], r['field'])} {operators[r['operator']]} {r['value']}"
                           for r in item.get('requirements', [])[:5]) or 'полные требования ещё не подтверждены'
    levels = {'low': 'низкие', 'medium': 'средние', 'high': 'высокие', 'unknown': 'неизвестны'}
    assessment = item.get('assessment') or {}
    effort = levels[assessment.get('registration_effort', 'unknown')]
    actions = item.get('next_actions') or ['Проверить условия и открыть официальную страницу для самостоятельной подачи.']
    if isinstance(actions, str):
        actions = [actions]
    instructions = '\n'.join('• ' + str(x)[:250] for x in actions[:4])
    result = (f"#{item['id']} · {str(item['title'])[:250]}\n"
              f"Организатор: {str(item.get('organizer', 'не установлен'))[:160]}\n"
              f"Категория: {item['category']} — {CATEGORY_NAMES[item['category']]}\n"
              f"Вознаграждение: {str(reward.get('amount') or 'не опубликовано / не подтверждено')[:200]} ({reward_type})\n"
              f"Дедлайн: {item.get('deadline') or 'не подтверждён'}\n"
              f"Актуальность: {STATE[ev['status']]}\n"
              f"Основные условия: {conditions[:500]}\n"
              f"Соответствие: {FIT[ev['eligibility']]}\n{reasons}\n"
              f"Трудозатраты на регистрацию (оценка агента): {effort}\n"
              f"Действия:\n{instructions}\n"
              f"Предложение: {item['url']}\nУсловия: {item.get('official_url') or 'первоисточник не найден'}\n"
              f"Проверено: {item.get('verified_at') or 'не подтверждено'}")
    return result[:4000]


def telegram_post(token, payload):
    # The address is fixed; no recipient or URL supplied by web content/model is used.
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                     data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(64000))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read(64000))
        except Exception:
            raise TimeoutError('Telegram returned an ambiguous error') from None
        return data
    except Exception:
        # Never include an exception string here: urllib errors can expose the bot token in URLs.
        raise TimeoutError('Telegram delivery outcome is unknown') from None


class Runtime:
    def __init__(self, home, native_call, secret_get, session_get, telegram_transport=None):
        self.home = Path(home).resolve()
        if self.home.name != 'opportunity-hunter':
            raise PermissionError('Opportunity Hunter must run only inside its own named profile')
        self.home.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.home / 'opportunities.db')
        self.secret_get, self.session_get = secret_get, session_get
        self.engine = ResearchEngine(self.store, native_call)
        seeds = self.home / 'sources-seed.json'
        if seeds.is_file() and not self.store.sources():
            for source in json.loads(seeds.read_text(encoding='utf-8')):
                self.store.add_source(source)
        self.telegram_transport = telegram_transport or telegram_post

    def _owner(self):
        owner = str(self.secret_get('OH_OWNER_ID') or '')
        if not re.fullmatch(r'[1-9][0-9]{0,19}', owner):
            return None
        return owner

    def _trusted_human(self):
        owner = self._owner()
        if not owner or self.session_get('HERMES_SESSION_PLATFORM') != 'telegram' or \
           str(self.session_get('HERMES_SESSION_USER_ID') or '') != owner or \
           str(self.session_get('HERMES_SESSION_CHAT_ID') or '') != owner or \
           self.session_get('HERMES_CRON_SESSION') == '1':
            raise PermissionError('This command requires the owner in the private Telegram chat')

    def handle(self, args):
        action = args.get('action')
        if action == 'due':
            return {'due': self.store.is_due(args['category'])}
        if action == 'research':
            if self.session_get('HERMES_CRON_SESSION') == '1' and not self.store.is_due(args['category']):
                return {'skipped': True, 'reason': 'Category disabled or not due'}
            return self.engine.collect(args['category'], query=args.get('query'))
        if action == 'store':
            return self.engine.ingest(args['item'])
        if action == 'complete_pages':
            return self.engine.acknowledge(args['pages'], args['category'])
        if action == 'page_text':
            return self.engine.page_text(args['url'], args['category'], args.get('offset', 0))
        if action == 'list':
            return self.store.list(category=args.get('category'), bookmarked=args.get('bookmarked', False),
                                   days=args.get('days'), limit=min(int(args.get('limit', 10)), 30),
                                   status=args.get('status'), recent_days=args.get('recent_days'))
        if action in ('details', 'instructions', 'recheck'):
            item = self.store.get(args['id'])
            if action == 'recheck':
                self.store.action(args['id'], 'recheck')
                return self.engine.collect(item['category'], targets=[item['official_url']], force_review=True)
            return {'item': item, 'text': format_card(item)}
        if action in ('save', 'unsave', 'hide', 'unhide'):
            return self.store.action(args['id'], action)
        if action == 'preferences':
            return self.store.preferences(category=args.get('category'), enabled=args.get('enabled'),
                                          interval_hours=args.get('interval_hours'))
        if action == 'profile':
            return self.store.profile()
        if action == 'profile_propose':
            return self._offer(self.store.propose_profile(args['patch']))
        if action == 'resume_profile':
            return self.store.resume_profile()
        if action == 'resume_propose':
            return self._offer(self.store.propose_resume(args['ops']))
        if action == 'profile_missing':
            return self.store.profile_missing()
        if action == 'rejected':
            return [{'id': i['id'], 'title': i['title'], 'reasons': i['evaluation']['reasons']}
                    for i in self.store.rejected(limit=min(int(args.get('limit', 10)), 30))]
        if action == 'sources':
            return self.store.sources()
        if action == 'source_add':
            source = args['source']
            if source.get('trust') not in ('official', 'organizer_platform', 'aggregator', 'unknown', 'discovered'):
                raise ValueError('Unknown source reliability classification')
            return self.store.add_source(source)
        if action == 'health':
            with self.engine._db() as db:
                usage = [dict(row) for row in db.execute('SELECT * FROM research_usage ORDER BY day DESC LIMIT 8')]
            return {**self.store.stats(), 'research_usage': usage, 'database_bytes': Path(self.store.path).stat().st_size,
                    'telegram_configured': bool(self._owner() and self.secret_get('TELEGRAM_BOT_TOKEN'))}
        if action == 'notify':
            return self.notify(args.get('kind', 'new'), dry_run=bool(args.get('dry_run', False)))
        raise ValueError('Unknown or forbidden action')

    def _send(self, text, markup=None):
        """Trusted delivery to the owner only (fixed chat id from private config, never from content)."""
        token, owner = self.secret_get('TELEGRAM_BOT_TOKEN'), self._owner()
        if not token or not owner:
            return False
        payload = {'chat_id': owner, 'text': text, 'disable_web_page_preview': True}
        if markup:
            payload['reply_markup'] = markup
        try:
            return bool(self.telegram_transport(token, payload).get('ok'))
        except Exception:
            return False

    def _offer(self, proposal):
        """Send the owner the full diff with ✅/✖️ buttons. The model never receives the token."""
        try:
            text = buttons.format_proposal(proposal, self.store.resume_profile(redact=False))
        except ValueError:
            self.store.cancel_proposal(proposal['token'])
            raise
        sent = self._send(text, buttons.confirm_markup(proposal['token']))
        return {'scope': proposal['scope'], 'confirmation_sent_to_owner': sent,
                'instruction': ('The owner received the full change with ✅/✖️ buttons. Do not claim it is applied.'
                                if sent else 'Delivery failed. Ask the owner to send /oh_pending to see and confirm it.')}

    def pending(self):
        """Re-send every pending proposal to the owner from the database (trusted path)."""
        proposals = self.store.pending_proposals()
        current = self.store.resume_profile(redact=False)
        delivered = refused = 0
        for proposal in proposals:
            try:
                text = buttons.format_proposal(proposal, current)
            except ValueError:  # cannot be shown in full any more: cancel instead of cutting
                self.store.cancel_proposal(proposal['token'])
                refused += 1
                continue
            delivered += self._send(text, buttons.confirm_markup(proposal['token']))
        if not proposals:
            return 'Ожидающих правок профиля нет.'
        note = f' Отменено как слишком большие: {refused}.' if refused else ''
        return f'Ожидающих правок: {len(proposals)}, отправлено с кнопками: {delivered}.{note}'

    def open_panel(self, data, fallback):
        """Send a button panel as a NEW message at the bottom of the chat (trusted transport).

        Further navigation edits that message in place; on delivery failure the text fallback is returned."""
        screen = buttons.render(self, *buttons.parse(data))
        if self._send(screen['text'], screen['markup']):
            return 'Панель выше ⬆️'
        return fallback() if callable(fallback) else fallback

    def menu(self):
        return self.open_panel(buttons.MENU_DATA, buttons.HELP)

    def health_text(self):
        health = self.handle({'action': 'health'})
        lines = ['Opportunity Hunter работает.', f"Предложений: {health['items']}",
                 f"Источников: {health['sources']}", f"Записей истории: {health['history']}",
                 f"Отправлено уведомлений: {health['notification_states'].get('sent', 0)}",
                 'Telegram: ' + ('настроен' if health['telegram_configured'] else 'не настроен'),
                 f"Размер SQLite: {health['database_bytes'] // 1024} КБ"]
        if health['latest_runs']:
            latest = health['latest_runs'][0]
            lines.append('Последняя проверка: ' + latest['category'] + ' · ' + latest['started_at'])
        if health['latest_errors']:
            lines.append('Есть неполные проверки источников. Данные без подтверждения не считаются подходящими.')
        lines += self.cron_lines()
        return '\n'.join(lines)

    def cron_lines(self):
        """Per-job state of this profile's native Cron: is it firing and how did the last run end."""
        cron = self.home / 'cron/jobs.json'
        if not cron.is_file():
            return ['Расписание: заданий нет (cron/jobs.json не найден)']
        try:
            jobs = json.loads(cron.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            return [f'Расписание: не удалось прочитать cron/jobs.json ({type(error).__name__})']
        jobs = jobs.get('jobs', []) if isinstance(jobs, dict) else jobs
        paused = sum(j.get('state') == 'paused' or j.get('enabled') is False for j in jobs)
        lines = [f'Расписание: {len(jobs)} заданий, на паузе {paused}']
        for job in jobs:
            line = f"• {job.get('name', '?')}: последний {job.get('last_run_at') or 'не запускалось'}"
            if job.get('last_status'):
                line += f" · {job['last_status']}"
            if job.get('failure_streak'):
                line += f" · сбоев подряд {job['failure_streak']}"
            line += f" · следующий {job.get('next_run_at') or '—'}"
            if job.get('last_error'):
                line += '\n  ошибка: ' + ' '.join(str(job['last_error']).split())[:200]
            lines.append(line)
        return lines

    def command(self, raw):
        self._trusted_human()
        return self.run_command(raw)

    def run_command(self, raw):
        """Owner already verified: by session (command) or by the Telegram update (button press)."""
        parts = raw.strip().split()
        if not parts or parts[0].lower() in ('menu', 'start'):
            return self.menu()
        command = parts[0].lower()
        if command == 'help':
            return buttons.HELP
        if command == 'confirm' and len(parts) == 2:
            result = self.store.confirm_profile(parts[1])
            return '✅ Применено: ' + ('резюме-профиль' if result['scope'] == 'resume' else 'критерии') + '. /oh_menu → 📋 Профиль.'
        if command == 'pending':
            return self.pending()
        if command == 'cancel' and len(parts) == 2:
            return '✖️ Правка отменена.' if self.store.cancel_proposal(parts[1]) else 'Предложение уже неактуально.'
        panels = {'rejected': 'oh:l:r:0', 'recent': 'oh:l:n:0', 'categories': 'oh:m:cats', 'profile': 'oh:m:profile'}
        if command == 'saved' and len(parts) == 1:
            panels['saved'] = 'oh:l:s:0'
        if command in panels:
            return self.open_panel(panels[command], lambda: buttons.render(self, *buttons.parse(panels[command]))['text'])
        if command in ('save', 'unsave', 'hide', 'unhide') and len(parts) == 2:
            self.store.action(parts[1], command)
            return 'Готово: ' + command + ' #' + parts[1]
        if command in ('details', 'instructions') and len(parts) == 2:
            return format_card(self.store.get(parts[1]))
        if command == 'recheck' and len(parts) == 2:
            self.store.action(parts[1], 'recheck')
            return 'Отмечено для проверки. Напишите: «Повторно проверь предложение #' + parts[1] + '».'
        if command in ('saved', 'list'):
            items = self.store.list(category=parts[1].upper() if len(parts) == 2 else None,
                                    bookmarked=command == 'saved', limit=10)
            return '\n\n'.join(f"#{i['id']} {i['title']}\n{i['url']}" for i in items) or 'Пока нет сохранённых предложений.'
        if command == 'category' and len(parts) == 3 and parts[2] in ('on', 'off'):
            return json.dumps(self.store.preferences(parts[1].upper(), enabled=parts[2] == 'on'), ensure_ascii=False)
        if command == 'frequency' and len(parts) == 3:
            return json.dumps(self.store.preferences(parts[1].upper(), interval_hours=float(parts[2])), ensure_ascii=False)
        if command == 'health':
            return self.health_text()
        return 'Неизвестная команда. /oh_menu — меню с кнопками. Или напиши запрос обычным языком.'

    def notify(self, kind='new', dry_run=False):
        if kind not in ('new', 'urgent', 'digest'):
            raise ValueError('Unknown notification kind')
        candidates = self.store.notification_candidates(kind=kind)
        items = candidates if kind == 'digest' else candidates[:6]
        if dry_run:
            return {'state': 'preview', 'messages': [format_card(i) for i in items]}
        token, owner = self.secret_get('TELEGRAM_BOT_TOKEN'), self._owner()
        if not token or not owner:
            return {'state': 'not_configured', 'pending_candidates': len(items)}
        outcomes = []
        if kind == 'digest':
            for start in range(0, len(items), 10):
                claimed = []
                for item in items[start:start + 10]:
                    event = self.store.queue_notification(item['id'], kind=kind)
                    if self.store.claim_notification(event['id']):
                        claimed.append((event['item'], event))
                if not claimed:
                    continue
                text = 'Сводка новых и изменившихся возможностей\n\n' + '\n\n'.join(
                    f"{i['category']} · {i['title'][:100]}\n{FIT[i['evaluation']['eligibility']]}\n/oh_details {i['id']}"
                    for i, _ in claimed)
                try:
                    response = self.telegram_transport(token, {'chat_id': owner, 'text': text,
                                                              'disable_web_page_preview': True})
                    state = 'sent' if response.get('ok') and response.get('result', {}).get('message_id') else \
                            'failed' if response.get('ok') is False else 'unknown'
                    message_id = response.get('result', {}).get('message_id')
                except Exception:
                    state, message_id = 'unknown', None
                for item, event in claimed:
                    self.store.mark_notification(event['id'], state, message_id)
                    outcomes.append({'id': item['id'], 'state': state})
                if state != 'sent':
                    break
            return {'state': 'done', 'outcomes': outcomes}
        for item in items:
            event = self.store.queue_notification(item['id'], kind=kind)
            if not self.store.claim_notification(event['id']):
                continue
            item = event['item']
            # Atomic claim BEFORE network I/O. A crash in 'sending' cannot cause blind resending.
            payload = {'chat_id': owner, 'text': ('СРОЧНО: близкий дедлайн\n' if kind == 'urgent' else '') + format_card(item),
                       'disable_web_page_preview': True,
                       'reply_markup': buttons.card_markup(item['id'], item.get('bookmarked'), item.get('hidden'))}
            try:
                response = self.telegram_transport(token, payload)
                if response.get('ok') and response.get('result', {}).get('message_id'):
                    self.store.mark_notification(event['id'], 'sent', response['result']['message_id'])
                    outcomes.append({'id': item['id'], 'state': 'sent'})
                elif response.get('ok') is False:
                    self.store.mark_notification(event['id'], 'failed')
                    outcomes.append({'id': item['id'], 'state': 'failed', 'code': response.get('error_code')})
                    break  # Avoid hammering a throttled or disabled Telegram endpoint.
                else:
                    outcomes.append({'id': item['id'], 'state': 'unknown'})
            except Exception:
                outcomes.append({'id': item['id'], 'state': 'unknown'})
                break
        return {'state': 'done', 'outcomes': outcomes}
