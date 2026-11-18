"""Telegram inline buttons: screen map, owner-checked callback dispatch and human-readable views.

Navigation EDITS one panel message in place (Telegram's own recommendation); opportunity cards and
profile proposals stay separate messages whose buttons change only that message. Navigation state
lives in callback_data (no server memory), so old panels keep working after a Gateway restart.
Pure functions only (no Telegram SDK). callback_data is ASCII and always <= 64 bytes.

Every render returns {'text', 'markup', 'mode', 'toast', 'alert'}; mode is 'edit' (replace the
pressed message), 'send' (new message), 'keyboard' (replace only the buttons) or None (toast only).
"""
from __future__ import annotations

import math
import re

from .core import CATEGORIES, CONTACT_FIELDS, RESUME_SECTIONS
from .core import resume_entry_digest as entry_digest

PREFIX = 'oh:'
MENU_DATA = 'oh:m:menu'
_ITEM_ID = re.compile(r'[0-9a-f]{32}')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{20,60}')
_ENTRY_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_PAGE = re.compile(r'[0-9]{1,2}')
_DIGEST = re.compile(r'[0-9a-f]{6}')
_CTX = re.compile(r'[snr][0-9]{1,2}|-|c')  # where an item was opened: list+page, panel without list, card
PAGE_SIZE = 5
RESUME_PAGE_SIZE = 8
MAX_LIST = 100 * PAGE_SIZE  # page numbers stay two digits
FREQUENCIES = (6, 12, 24, 72, 168)
ACTIONS = ('save', 'unsave', 'hide', 'unhide', 'recheck')
SCREENS = ('menu', 'profile', 'crit', 'resume', 'pend', 'cats', 'health', 'help')
# Buttons of messages sent before v2 keep working.
LEGACY = {'saved': ('l', ['s', '0']), 'recent': ('l', ['n', '0']), 'rejected': ('l', ['r', '0']),
          'categories': ('m', ['cats'])}

MENU = [
    [('📋 Профиль', 'oh:m:profile'), ('⭐ Избранное', 'oh:l:s:0')],
    [('🆕 Новое', 'oh:l:n:0'), ('🙈 Отсеянное', 'oh:l:r:0')],
    [('⚙️ Категории', 'oh:m:cats'), ('🩺 Состояние', 'oh:m:health')],
    [('❓ Помощь', 'oh:m:help')],
]
LISTS = {'s': '⭐ Избранное', 'n': '🆕 Новое за 3 дня', 'r': '🙈 Отсеянное'}
EMPTY_LIST = {'s': 'Пока ничего не сохранено. Жми «⭐ Сохранить» под карточкой.',
              'n': 'За 3 дня новых предложений нет.',
              'r': 'Отсеянных предложений нет.'}
HELP = ('Я ищу для тебя стипендии, гранты, стажировки, хакатоны, бесплатные программы и торговые '
        'конкурсы, проверяю условия по первоисточникам и присылаю подходящее.\n\n'
        '• Пиши обычным текстом: «найди оплачиваемые Python-стажировки», «почему подходит #ID?», '
        '«добавь в профиль, что я знаю Docker».\n'
        '• Всё остальное — кнопками: /oh_menu открывает меню, «⬅️ Назад» и «🏠 Меню» есть на каждом экране.\n'
        '• Под каждой карточкой: сохранить, скрыть, перепроверить.\n'
        '• Правки профиля я сначала показываю, а применяю только после твоего «✅ Подтвердить».\n'
        '• Заявки я не отправляю — только готовлю и подсказываю.')
CRITERIA_LABELS = {
    'city': 'Город', 'year': 'Курс', 'university': 'Вуз', 'campus': 'Кампус', 'degree': 'Уровень',
    'major': 'Направление', 'study_start': 'Начало учёбы', 'study_end': 'Выпуск', 'track': 'Трек',
    'citizenship': 'Гражданство', 'english_level': 'Английский', 'weekly_hours': 'Часов в неделю',
    'full_time': 'Полный день', 'relocation': 'Готов к переезду', 'min_salary': 'Мин. оплата',
    'available_from': 'Могу выйти с', 'skills': 'Навыки', 'interests': 'Интересы',
}
SECTION_TITLES = {'education': 'Образование', 'projects': 'Проекты', 'achievements': 'Достижения',
                  'skills': 'Навыки (с доказательствами)', 'languages': 'Языки', 'experience': 'Опыт работы',
                  'pinned_rules': 'Закреплённые правила'}
SECTION_CODES = {'education': 'e', 'projects': 'p', 'achievements': 'a', 'skills': 's', 'languages': 'l',
                 'experience': 'x', 'pinned_rules': 'r'}
CODE_SECTIONS = {code: section for section, code in SECTION_CODES.items()}


# ── building blocks ─────────────────────────────────────────────────────────────────────────────
def keyboard(rows):
    return {'inline_keyboard': [[{'text': text, 'callback_data': data} for text, data in row] for row in rows]}


def menu_markup():
    return keyboard(MENU)


def _screen(text, rows, mode='edit', toast=None, alert=False):
    return {'text': text[:4000], 'markup': keyboard(rows) if rows else None, 'mode': mode,
            'toast': toast, 'alert': alert}


def _notice(toast, alert=True, mode=None, markup=None):
    return {'text': None, 'markup': markup, 'mode': mode, 'toast': toast, 'alert': alert}


def nav(back=None, back_label='⬅️ Назад'):
    """Bottom row of every screen: back to the parent (when it is not the menu itself) + menu."""
    if back is None or back == MENU_DATA:
        return [('🏠 Меню', MENU_DATA)]
    return [(back_label, back), ('🏠 Меню', MENU_DATA)]


def _pager(prefix, page, pages):
    if pages <= 1:
        return []
    row = [('◀️', f'{prefix}:{page - 1}')] if page > 0 else []
    row.append((f'{page + 1} / {pages}', 'oh:z'))
    if page < pages - 1:
        row.append(('▶️', f'{prefix}:{page + 1}'))
    return [row]


def _page(entries, page, size):
    pages = max(1, math.ceil(len(entries) / size))
    page = min(max(page, 0), pages - 1)  # a stale page (items removed meanwhile) shows the last one
    return entries[page * size:(page + 1) * size], page, pages


def _short(text, limit):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _value(value):
    if value in (None, '', []):
        return '—'
    if value is True:
        return 'да'
    if value is False:
        return 'нет'
    if isinstance(value, list):
        return ', '.join(map(str, value))
    return str(value)


def _label(entry):
    return (entry.get('name') or entry.get('title') or entry.get('institution') or entry.get('language')
            or entry.get('company') or entry.get('text') or entry.get('id'))


def _fields(entry):
    """Every non-empty field, source included: the owner must see exactly what he approves."""
    return ''.join(f'\n   {key}: {_value(value)}' for key, value in entry.items()
                   if key not in ('id', 'confirmed') and value not in (None, '', []))


def _freq(hours):
    return {6: '6 ч', 12: '12 ч', 24: '24 ч', 72: '3 дня', 168: '7 дней'}.get(hours) or f'{hours:g} ч'


# ── standalone messages: cards and proposals ────────────────────────────────────────────────────
def _card_rows(item_id, bookmarked=False, hidden=False, ctx='c'):
    if not _ITEM_ID.fullmatch(str(item_id)) or not _CTX.fullmatch(str(ctx)):
        raise ValueError('Unexpected opportunity id')
    save = ('☆ Убрать из избранного', 'unsave') if bookmarked else ('⭐ Сохранить', 'save')
    hide = ('↩️ Вернуть', 'unhide') if hidden else ('🙈 Скрыть', 'hide')
    return [[(save[0], f'oh:a:{save[1]}:{item_id}:{ctx}'), (hide[0], f'oh:a:{hide[1]}:{item_id}:{ctx}')],
            [('🔄 Перепроверить', f'oh:a:recheck:{item_id}:{ctx}')]]


def card_markup(item_id, bookmarked=False, hidden=False):
    """Buttons under a notification card; pressing them changes only this card's buttons."""
    return keyboard(_card_rows(item_id, bookmarked, hidden, 'c'))


def confirm_markup(token, in_panel=False):
    if not _TOKEN.fullmatch(str(token)):
        raise ValueError('Unexpected proposal token')
    suffix = ':p' if in_panel else ''
    return keyboard([[('✅ Подтвердить', f'oh:c:{token}{suffix}'), ('✖️ Отмена', f'oh:x:{token}{suffix}')]])


def format_profile(criteria, resume):
    """Both profile layers as plain text; contact values are never printed, only that they exist."""
    lines = ['📋 Профиль', '', 'Критерии отбора:']
    lines += [f'• {label}: {_value(criteria.get(key))}' for key, label in CRITERIA_LABELS.items()]
    contacts = [key for key in CONTACT_FIELDS if resume.get('basics', {}).get(key)]
    lines += ['', 'Контакты для резюме: ' + (', '.join(contacts) + ' — сохранены' if contacts else 'не заданы')]
    for section in RESUME_SECTIONS:
        entries = resume.get(section) or []
        if not entries:
            continue
        lines += ['', SECTION_TITLES[section] + ':']
        for entry in entries[:15]:
            lines.append(('✅ ' if entry.get('confirmed') else '❔ ') + str(_label(entry))[:150])
    lines += ['', '✅ подтверждено · ❔ ждёт твоего подтверждения (в резюме не попадёт)']
    return '\n'.join(lines)[:4000]


MAX_PROPOSAL_TEXT = 3900  # one Telegram message (4096) with margin; larger proposals are refused, never cut


def _describe_op(op, current=None):
    kind = op.get('op')
    if kind == 'set_basics':
        return f"✏️ {op.get('field')}: {_value(op.get('value'))}"
    section = SECTION_TITLES.get(op.get('section'), op.get('section'))
    existing = {e.get('id'): e for e in (current or {}).get(op.get('section'), [])}
    if kind in ('add', 'replace'):
        item = op.get('item') if isinstance(op.get('item'), dict) else {}
        head = ('➕ Добавить' if kind == 'add' else '♻️ Заменить') + f" в «{section}»: {_label(item)} (id {item.get('id')})"
        text = head + _fields(item)
        if kind == 'replace' and item.get('id') in existing:
            text += '\n   было:' + _fields(existing[item['id']]).replace('\n   ', '\n      ')
        return text
    verb = '➖ Удалить' if kind == 'remove' else '✅ Подтвердить'
    entry = existing.get(op.get('id'))
    return f"{verb} в «{section}»: " + (f"{_label(entry)} (id {op.get('id')})" + _fields(entry) if entry else str(op.get('id')))


def format_proposal(proposal, current_resume=None):
    """Full, untruncated description; raises ValueError when it cannot be shown in one message."""
    if proposal['scope'] == 'resume':
        body = '\n'.join(_describe_op(op, current_resume) for op in proposal['patch'])
    else:
        body = '\n'.join(f'✏️ {CRITERIA_LABELS.get(k, k)}: {_value(v)}' for k, v in proposal['patch'].items())
    text = 'Предлагаю изменить профиль:\n\n' + body + '\n\nПрименю только после «✅ Подтвердить» (30 минут).'
    if len(text) > MAX_PROPOSAL_TEXT:
        raise ValueError('Proposal is too large to show the owner in full; split it into smaller proposals')
    return text


# ── panel screens ───────────────────────────────────────────────────────────────────────────────
def screen_menu(runtime):
    text = '🏠 Меню\n\nЧто сделать? Или просто напиши запрос обычным текстом.'
    pending = len(runtime.store.pending_proposals())
    if pending:
        text += f'\n\n⏳ Ждут твоего подтверждения правок профиля: {pending} (📋 Профиль)'
    return _screen(text, MENU)


def screen_help(runtime):
    return _screen('Меню › Помощь\n\n' + HELP, [nav()])


def screen_health(runtime):
    return _screen('Меню › Состояние\n\n' + runtime.health_text(),
                   [[('🔄 Обновить', 'oh:m:health')], nav()], toast='Обновлено')


def _list_items(store, code):
    if code == 's':
        items = store.list(bookmarked=True, limit=1000)
    elif code == 'n':
        items = store.list(recent_days=3, limit=1000)
    else:
        items = store.rejected(limit=1000)
    return items[:MAX_LIST]


def screen_list(runtime, code, page=0):
    from .runtime import FIT
    entries, page, pages = _page(_list_items(runtime.store, code), page, PAGE_SIZE)
    head = f'Меню › {LISTS[code]}' + (f' · стр. {page + 1}/{pages}' if pages > 1 else '')
    if not entries:
        return _screen(head + '\n\n' + EMPTY_LIST[code], [nav()])
    lines, rows = [], []
    for number, item in enumerate(entries, start=page * PAGE_SIZE + 1):
        reasons = item['evaluation'].get('reasons') or ['—']
        note = reasons[0] if code == 'r' else FIT[item['evaluation']['eligibility']]
        mark = '⭐ ' if item.get('bookmarked') and code != 's' else ''
        lines.append(f"{number}. {mark}{_short(item['title'], 110)}\n    {_short(note, 160)}")
        rows.append([(f"{number}. {_short(item['title'], 34)}", f"oh:i:{item['id']}:{code}{page}")])
    return _screen(head + '\n\nНажми на номер, чтобы открыть:\n\n' + '\n\n'.join(lines),
                   rows + _pager(f'oh:l:{code}', page, pages) + [nav()])


def screen_item(runtime, item_id, ctx, toast=None):
    from .runtime import format_card
    item = runtime.store.get(item_id)
    ctx = '-' if ctx == 'c' else ctx
    if ctx == '-':
        head, back = 'Меню › Карточка', None
    else:
        head, back = f'Меню › {LISTS[ctx[0]]} › карточка', f'oh:l:{ctx[0]}:{ctx[1:]}'
    marks = ' · '.join(m for m, on in (('⭐ в избранном', item.get('bookmarked')), ('🙈 скрыто', item.get('hidden'))) if on)
    text = head + ('\n' + marks if marks else '') + '\n\n' + format_card(item)[:3700]
    return _screen(text, _card_rows(item_id, item.get('bookmarked'), item.get('hidden'), ctx)
                   + [nav(back, '⬅️ К списку')], toast=toast)


def do_action(runtime, action, item_id, ctx):
    if action == 'recheck':
        runtime.store.action(item_id, 'recheck')
        return _notice('Отметил для перепроверки. Чтобы проверить прямо сейчас, напиши мне: '
                       '«перепроверь #' + item_id + '»')
    item = runtime.store.action(item_id, action)
    toast = {'save': 'Сохранено ⭐', 'unsave': 'Убрано из избранного', 'hide': 'Скрыто 🙈', 'unhide': 'Вернул ↩️'}[action]
    if ctx == 'c':
        return _notice(toast, alert=False, mode='keyboard',
                       markup=card_markup(item_id, item.get('bookmarked'), item.get('hidden')))
    return screen_item(runtime, item_id, ctx, toast=toast)


def screen_categories(runtime, toast=None):
    from .runtime import CATEGORY_NAMES
    prefs = runtime.store.preferences()
    lines, rows = [], []
    for code, pref in prefs.items():
        mark = '✅' if pref['enabled'] else '⏸'
        state = 'проверка раз в ' + _freq(pref['interval_hours']) if pref['enabled'] else 'на паузе'
        lines.append(f'{mark} {code} — {CATEGORY_NAMES[code]}: {state}')
        rows.append([(f"{mark} {code} · {_short(CATEGORY_NAMES[code], 26)}", f'oh:k:{code}')])
    return _screen('Меню › Категории\n\n' + '\n'.join(lines) +
                   '\n\nНажми на категорию: пауза/включение и частота проверки.', rows + [nav()], toast=toast)


def screen_category(runtime, code, toast=None):
    from .runtime import CATEGORY_NAMES
    pref = runtime.store.preferences(code)
    toggle = ('⏸ Поставить на паузу', f'oh:k:{code}:off') if pref['enabled'] else ('▶️ Включить', f'oh:k:{code}:on')
    freq_row = [(('• ' if pref['interval_hours'] == hours else '') + _freq(hours), f'oh:k:{code}:{hours}')
                for hours in FREQUENCIES]
    text = (f"Меню › Категории › {code}\n\n{CATEGORY_NAMES[code]}\n"
            f"Статус: {'включена' if pref['enabled'] else 'на паузе'}\n"
            f"Проверка: раз в {_freq(pref['interval_hours'])}\n\nКак часто проверять:")
    return _screen(text, [[toggle], freq_row, nav('oh:m:cats')], toast=toast)


def set_category(runtime, code, value):
    if value in ('on', 'off'):
        runtime.store.preferences(code, enabled=value == 'on')
        toast = 'Включено ▶️' if value == 'on' else 'На паузе ⏸'
    else:
        runtime.store.preferences(code, interval_hours=int(value))
        toast = 'Раз в ' + _freq(int(value))
    return screen_category(runtime, code, toast=toast)


def screen_profile(runtime):
    store = runtime.store
    criteria, resume = store.profile(), store.resume_profile(redact=False)
    facts = [entry for section in RESUME_SECTIONS for entry in resume.get(section) or []]
    confirmed = sum(1 for entry in facts if entry.get('confirmed'))
    contacts = [key for key in CONTACT_FIELDS if resume.get('basics', {}).get(key)]
    pending = len(store.pending_proposals())
    text = ('Меню › Профиль\n\n'
            + '\n'.join(f'{CRITERIA_LABELS[key]}: {_value(criteria.get(key))}'
                        for key in ('city', 'university', 'year', 'english_level'))
            + f'\n\nРезюме: подтверждено {confirmed} из {len(facts)} фактов'
            + '\nКонтакты для резюме: ' + (', '.join(contacts) + ' — сохранены' if contacts else 'не заданы')
            + '\n\nИзменить: напиши мне обычным текстом, например «добавь, что я знаю Docker». '
              'Я покажу правку и применю её только после «✅ Подтвердить».')
    rows = [[('🎯 Критерии отбора', 'oh:m:crit'), ('🧾 Резюме', 'oh:m:resume')]]
    if pending:
        rows.append([(f'⏳ Ждут подтверждения ({pending})', 'oh:m:pend')])
    return _screen(text, rows + [nav()])


def screen_criteria(runtime):
    criteria = runtime.store.profile()
    text = 'Меню › Профиль › Критерии отбора\n\n' + '\n'.join(
        f'• {label}: {_value(criteria.get(key))}' for key, label in CRITERIA_LABELS.items())
    return _screen(text + '\n\nПо этим полям я отсеиваю неподходящее. Поменять — напиши мне обычным текстом.',
                   [nav('oh:m:profile')])


def screen_resume(runtime):
    resume = runtime.store.resume_profile(redact=False)
    rows = []
    for section in RESUME_SECTIONS:
        entries = resume.get(section) or []
        if entries:
            done = sum(1 for entry in entries if entry.get('confirmed'))
            rows.append([(f'{SECTION_TITLES[section]} · ✅ {done}/{len(entries)}', f'oh:rs:{SECTION_CODES[section]}:0')])
    text = ('Меню › Профиль › Резюме\n\n✅ подтверждено · ❔ ждёт подтверждения (в резюме не попадёт)\n\n'
            + ('Выбери раздел:' if rows else 'Резюме-профиль пока пуст.'))
    return _screen(text, rows + [nav('oh:m:profile')])


def _fits(data):
    return len(data.encode()) <= 64


def screen_section(runtime, code, page=0):
    section = CODE_SECTIONS[code]
    entries = runtime.store.resume_profile(redact=False).get(section) or []
    chunk, page, pages = _page(entries, page, RESUME_PAGE_SIZE)
    head = f'Меню › Резюме › {SECTION_TITLES[section]}' + (f' · стр. {page + 1}/{pages}' if pages > 1 else '')
    rows, without_button = [], []
    for entry in chunk:
        label = ('✅ ' if entry.get('confirmed') else '❔ ') + _short(_label(entry), 40)
        data = f"oh:re:{code}:{entry['id']}"
        if _fits(data):
            rows.append([(label, data)])
        else:
            without_button.append(label)
    text = head + '\n\n' + ('Нажми на запись, чтобы посмотреть её целиком и подтвердить.' if chunk else 'Раздел пуст.')
    if without_button:
        text += '\n\nБез кнопки (слишком длинный id, проверь через чат):\n' + '\n'.join(without_button)
    return _screen(text, rows + _pager(f'oh:rs:{code}', page, pages) + [nav('oh:m:resume')])


ENTRY_TEXT_LIMIT = 3900


def screen_entry(runtime, code, entry_id, toast=None):
    section = CODE_SECTIONS[code]
    entries = runtime.store.resume_profile(redact=False).get(section) or []
    index = next((i for i, entry in enumerate(entries) if entry['id'] == entry_id), None)
    if index is None:
        raise KeyError('Resume entry not found')
    entry = entries[index]
    status = '✅ подтверждено' if entry.get('confirmed') else '❔ ещё не подтверждено — в резюме не попадёт'
    text = f'Меню › Резюме › {SECTION_TITLES[section]}\n\n{_label(entry)}\n{status}\n{_fields(entry)}'
    confirm = f'oh:rc:{code}:{entry_id}:{entry_digest(entry)}'
    rows = []
    if not entry.get('confirmed'):
        if len(text) > ENTRY_TEXT_LIMIT:  # never confirm what the owner could not see in full
            text = text[:ENTRY_TEXT_LIMIT - 150] + '\n…\n\nЗапись не помещается в одно сообщение — подтвердить кнопкой нельзя. Попроси меня её сократить.'
        elif not _fits(confirm):
            text += '\n\nУ записи слишком длинный id для кнопки — подтверди через чат.'
        else:
            text += '\n\nВсё верно — жми «✅ Всё верно». Что-то не так — напиши мне, что исправить.'
            rows.append([('✅ Всё верно', confirm)])
    return _screen(text, rows + [nav(f'oh:rs:{code}:{index // RESUME_PAGE_SIZE}', '⬅️ К разделу')], toast=toast)


def confirm_entry(runtime, code, entry_id, digest):
    try:
        changed = runtime.store.confirm_resume_entry(CODE_SECTIONS[code], entry_id, digest)
    except ValueError:
        return {**screen_entry(runtime, code, entry_id), 'toast': 'Запись изменилась — проверь новую версию', 'alert': True}
    return screen_entry(runtime, code, entry_id, toast='Подтверждено ✅' if changed else 'Уже подтверждено')


def _proposal_title(proposal):
    if proposal['scope'] == 'resume':
        return f"резюме: {len(proposal['patch'])} изм."
    return 'критерии: ' + ', '.join(CRITERIA_LABELS.get(key, key) for key in proposal['patch'])


def screen_pending(runtime, toast=None):
    proposals = runtime.store.pending_proposals()
    if not proposals:
        return _screen('Меню › Профиль › Ждут подтверждения\n\nОжидающих правок нет.', [nav('oh:m:profile')], toast=toast)
    lines = [f'{n}. {_short(_proposal_title(p), 120)}' for n, p in enumerate(proposals, start=1)]
    rows = [[(f'{n}. Посмотреть', f"oh:p:{p['token']}")] for n, p in enumerate(proposals, start=1)]
    return _screen('Меню › Профиль › Ждут подтверждения\n\n' + '\n'.join(lines), rows + [nav('oh:m:profile')], toast=toast)


def screen_proposal(runtime, token):
    proposal = runtime.store.proposal(token)
    if proposal is None:
        return screen_pending(runtime, toast='Эта правка уже неактуальна')
    try:
        text = format_proposal(proposal, runtime.store.resume_profile(redact=False))
    except ValueError:  # e.g. a replace whose «было:» grew too long: refuse rather than cut
        runtime.store.cancel_proposal(token)
        return {**screen_pending(runtime), 'toast': 'Правка слишком большая, чтобы показать целиком — отменил её. '
                                                    'Попроси меня разбить её на части.', 'alert': True}
    rows = confirm_markup(token, in_panel=True)['inline_keyboard']
    return {**_screen('Меню › Профиль › Правка\n\n' + text, [nav('oh:m:pend')]),
            'markup': {'inline_keyboard': rows + keyboard([nav('oh:m:pend')])['inline_keyboard']}}


def resolve_proposal(runtime, kind, token, in_panel):
    """✅/✖️: the pressed message turns into the result, so the same button cannot be pressed twice."""
    after = [[('⏳ К правкам', 'oh:m:pend')], nav()] if in_panel else []
    if kind == 'x':
        if not runtime.store.cancel_proposal(token):
            return _notice('Эта правка уже неактуальна', mode='keyboard', markup=keyboard(after) if after else None)
        return _screen('✖️ Правка отменена.', after, toast='Отменено')
    proposal = runtime.store.proposal(token)
    try:
        runtime.store.confirm_profile(token)
    except ValueError:
        return _notice('Правка устарела или уже применена', mode='keyboard', markup=keyboard(after) if after else None)
    what = 'резюме-профиль' if proposal and proposal['scope'] == 'resume' else 'критерии'
    return _screen(f'✅ Применено: {what}. Посмотреть: /oh_menu → 📋 Профиль.', after, toast='Готово')


# ── routing ─────────────────────────────────────────────────────────────────────────────────────
def parse(data):
    """Split callback_data into (kind, args) or return None for anything not ours/malformed."""
    if not isinstance(data, str) or not data.startswith(PREFIX) or len(data.encode()) > 64:
        return None
    parts = data.split(':')
    kind, args = parts[1] if len(parts) > 1 else '', parts[2:]
    if kind == 'z' and not args:
        return kind, args
    if kind == 'm' and len(args) == 1:
        if args[0] in SCREENS:
            return kind, args
        return LEGACY.get(args[0])
    if kind == 'l' and len(args) == 2 and args[0] in LISTS and _PAGE.fullmatch(args[1]):
        return kind, args
    if kind == 'i' and len(args) == 2 and _ITEM_ID.fullmatch(args[0]) and _CTX.fullmatch(args[1]):
        return kind, args
    if kind == 'a':
        if len(args) == 2:
            args = args + ['c']  # card buttons sent before v2 had no context
        if len(args) == 3 and _ITEM_ID.fullmatch(args[1]) and _CTX.fullmatch(args[2]):
            if args[0] == 'details':
                return 'i', [args[1], '-']
            if args[0] in ACTIONS:
                return kind, args
        return None
    if kind == 'k' and 1 <= len(args) <= 2 and args[0] in CATEGORIES and \
            (len(args) == 1 or args[1] in ('on', 'off', *map(str, FREQUENCIES))):
        return kind, args
    if kind in ('rs', 're') and len(args) == 2 and args[0] in CODE_SECTIONS and \
            (_PAGE if kind == 'rs' else _ENTRY_ID).fullmatch(args[1]):
        return kind, args
    if kind == 'rc' and len(args) == 3 and args[0] in CODE_SECTIONS and _ENTRY_ID.fullmatch(args[1]) \
            and _DIGEST.fullmatch(args[2]):
        return kind, args
    if kind == 'p' and len(args) == 1 and _TOKEN.fullmatch(args[0]):
        return kind, args
    if kind in ('c', 'x') and len(args) in (1, 2) and _TOKEN.fullmatch(args[0]) and args[1:] in ([], ['p']):
        return kind, args
    return None


def render(runtime, kind, args):
    """Owner already verified. Builds the screen for parsed callback_data."""
    if kind == 'z':
        return _notice(None, alert=False)
    if kind == 'm':
        return {'menu': screen_menu, 'profile': screen_profile, 'crit': screen_criteria, 'resume': screen_resume,
                'pend': screen_pending, 'cats': screen_categories, 'health': screen_health,
                'help': screen_help}[args[0]](runtime)
    if kind == 'l':
        return screen_list(runtime, args[0], int(args[1]))
    if kind == 'i':
        return screen_item(runtime, args[0], args[1])
    if kind == 'a':
        return do_action(runtime, *args)
    if kind == 'k':
        return screen_category(runtime, args[0]) if len(args) == 1 else set_category(runtime, *args)
    if kind == 'rs':
        return screen_section(runtime, args[0], int(args[1]))
    if kind == 're':
        return screen_entry(runtime, *args)
    if kind == 'rc':
        return confirm_entry(runtime, *args)
    if kind == 'p':
        return screen_proposal(runtime, args[0])
    return resolve_proposal(runtime, kind, args[0], args[1:] == ['p'])


def dispatch(runtime, data, user_id, chat_id):
    """Handle one button press. None = not allowed (only the owner in the private chat may press,
    the same rule as the /oh_* commands); otherwise a render dict, stale buttons get an alert."""
    owner = runtime._owner()
    if not owner or str(user_id) != owner or str(chat_id) != owner:
        return None
    parsed = parse(data)
    if parsed is None:
        return _notice('Эта кнопка устарела. Открой меню: /oh_menu')
    try:
        return render(runtime, *parsed)
    except KeyError:
        return _notice('Запись не найдена — возможно, её уже удалили.')
