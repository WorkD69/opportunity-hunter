"""Offline integration check using captured live responses, not another live search.

Run from the project root: python -X utf8 reports/replay_evidence.py
The reports were captured read-only on the VPS. Windows public-DNS validation
rejects this environment's private proxy answers, so networking is not weakened.
Telegram transport is a stub; no real messages or applications are sent.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opportunity_hunter.research import FetchResult
from opportunity_hunter.runtime import Runtime, format_card


def main():
    captured = json.loads((ROOT / 'reports/live-fetch-probe.json').read_text(encoding='utf-8'))
    native = json.loads((ROOT / 'reports/native-web-probe.json').read_text(encoding='utf-8'))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    home = ROOT / 'runtime' / ('evidence-replay-' + stamp) / 'opportunity-hunter'
    sent = []

    def dispatch(name, arguments):
        if name == 'web_search':
            return next(s['response'] for s in native['searches'] if s['query'] == arguments['query'])
        raise ValueError('No extraction replay exists; no external call was attempted')

    pages = {p['url']: p for p in captured['pages'] if 'plain_text' in p}

    def fetch(url, previous=None):
        if url not in pages:
            raise ValueError('Page not captured in the replay fixture')
        return FetchResult(**pages[url])

    runtime = Runtime(home, dispatch, {'OH_OWNER_ID': '123', 'TELEGRAM_BOT_TOKEN': 'offline-stub'}.get,
        {'HERMES_SESSION_PLATFORM': 'telegram', 'HERMES_SESSION_USER_ID': '123',
         'HERMES_SESSION_CHAT_ID': '123'}.get,
        telegram_transport=lambda token, payload: sent.append(payload) or
            {'ok': True, 'result': {'message_id': len(sent)}})
    runtime.engine.fetcher = fetch
    discovered = []
    for search in native['searches']:
        result = runtime.engine.collect(search['category'], query=search['query'], max_queries=1, max_pages=1)
        discovered.append({'category': search['category'], 'new_sources': len(result['discovered']),
                           'complete': result['complete'], 'errors': result['errors']})

    stored = []
    for category, url in [('A', 'https://yandex.ru/profi/'),
                          ('D', next(u for u in pages if 'sirius' in u))]:
        result = runtime.engine.collect(category, targets=[url])
        assert result['complete'], result['errors']
        page = result['pages'][0]
        while page['next_offset'] is not None:
            page = runtime.engine.page_text(url, category, page['next_offset'])
        if category == 'A':
            title = 'Я — профессионал, X сезон'
            deadline = '2026-11-10'
            evidence = [{'url': url, 'quote': 'Регистрация 3 сентября — 10 ноября 2026', 'field': 'deadline'},
                        {'url': url, 'quote': 'Премии предусмотрены для сильнейших участников — медалистов.'}]
            requirements = [{'field': 'full_rules_review', 'operator': 'eq', 'value': True,
                'evidence_url': url, 'quote': 'Всероссийская олимпиада для студентов разных направлений подготовки'}]
            reward = {'type': 'prize', 'amount': None}
            costs = {}
            actions = ['Проверить полное положение текущего сезона, выбрать направление; тесты проходить лично.']
        else:
            title = 'Сириус — Анализ и защита данных в финтехе'
            deadline = '2026-10-18'
            evidence = [{'url': url, 'quote': 'Срок подачи заявки: 18 октября 2026', 'field': 'deadline'},
                {'url': url, 'quote': 'Плата за обучение на программе не взимается.', 'field': 'cost_flags.entry_fee'}]
            requirements = [
                {'field': 'citizenship', 'operator': 'eq', 'value': 'RU', 'evidence_url': url,
                 'quote': 'К участию в программе допускаются только граждане РФ.'},
                {'field': 'year', 'operator': 'in', 'value': [3, 4], 'evidence_url': url,
                 'quote': 'К участию приглашаются студенты бакалавриата 3-го или 4-го курсов'},
                {'field': 'age', 'operator': 'min', 'value': 18, 'evidence_url': url,
                 'quote': 'возрастом 18 лет и старше'},
                {'field': 'full_time', 'operator': 'eq', 'value': True, 'evidence_url': url,
                 'quote': 'участник является студентом очного обучения'},
                {'field': 'prior_funded_sirius_2026', 'operator': 'eq', 'value': False, 'evidence_url': url,
                 'quote': 'На программу не допускаются студенты, принимавшие ранее участие в других дополнительных образовательных программах'}]
            reward = {'type': 'career', 'amount': None}
            costs = {'entry_fee': False}
            actions = ['Уточнить гражданство, возраст, очную форму и прошлое участие. Проезд самостоятельно.',
                       'Сверить очные даты с учёбой и лично пройти вступительные испытания.']
        item = runtime.engine.ingest({'category': category, 'title': title, 'organizer': title.split(' — ')[0],
            'url': url, 'identity_url': url, 'official_url': url, 'round': '2026', 'status': 'active',
            'deadline': deadline, 'reward': reward, 'requirements': requirements, 'evidence': evidence,
            'cost_flags': costs, 'next_actions': actions})
        runtime.store.action(item['id'], 'save')
        runtime.engine.acknowledge(result['pages'], category)
        repeated = runtime.engine.collect(category, targets=[url])
        assert repeated['unchanged'] == [url] and not repeated['pages']
        stored.append(item)

    first = runtime.notify('new')
    second = runtime.notify('new')
    assert len(sent) == 2 and not second['outcomes']
    assert len(runtime.store.list(bookmarked=True)) == 2
    summary = {'timestamp': datetime.now(timezone.utc).isoformat(), 'mode': 'offline replay of captured live responses',
        'database': str(home / 'opportunities.db'), 'discovery': discovered,
        'items': [{'title': i['title'], 'category': i['category'], 'eligibility': i['evaluation']['eligibility'],
                   'free_status': i['evaluation']['free_status'], 'deadline': i['deadline']} for i in stored],
        'saved': 2, 'stub_telegram_messages': len(sent), 'duplicate_messages': len(second['outcomes']),
        'stats': runtime.store.stats(), 'database_bytes': (home / 'opportunities.db').stat().st_size,
        'live_gateway_tested': False, 'live_telegram_tested': False, 'live_cron_tested': False}
    (ROOT / 'reports/evidence-replay.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'reports/card-preview.txt').write_text('\n\n'.join(format_card(i) for i in stored), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'stats'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
