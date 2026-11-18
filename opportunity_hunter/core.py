"""SQLite persistence and conservative opportunity evaluation, using only stdlib.

Profile confirmation is deliberately a separate method: trusted human adapters may
call it, but search content and model-facing tools must never expose it.
"""

import hashlib
import ipaddress
import json
import math
import re
import secrets
import sqlite3
import string
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))
CATEGORIES = ('A', 'B', 'C', 'D')
DEFAULT_INTERVALS = {'A': 24, 'B': 12, 'C': 24, 'D': 48}
COST_KEYS = ('entry_fee', 'deposit', 'real_trading', 'paid_subscription')
# Public, neutral template: every fact is unknown until the owner confirms it
# (/oh_confirm) or a private profile import provides it. Never put real
# personal values here: this file is published.
PROFILE_SEED = {
    'year': None, 'university': None, 'campus': None,
    'degree': None, 'major': None, 'study_start': None,
    'study_end': None, 'track': None, 'citizenship': None,
    'residence': None, 'age': None, 'gpa': None, 'income': None,
    'employment': None, 'languages': None, 'english_level': None,
    'work_authorization': None, 'weekly_hours': None, 'full_time': None,
    'relocation': None, 'team_available': None, 'kyc_willingness': None,
    'skills': None, 'quant_experience': None, 'work_experience': None,
    'commercial_experience': None, 'interests': None,
    'city': None, 'min_salary': None, 'available_from': None,
}
# Spelling variants of the same city compare equal (ё/е, transliteration, short forms).
CITY_ALIASES = {
    'нижний новгород': 'nizhny novgorod', 'н. новгород': 'nizhny novgorod', 'nizhniy novgorod': 'nizhny novgorod',
    'nizhni novgorod': 'nizhny novgorod', 'москва': 'moscow', 'санкт-петербург': 'saint petersburg',
    'спб': 'saint petersburg', 'питер': 'saint petersburg', 'st. petersburg': 'saint petersburg',
    'st petersburg': 'saint petersburg', 'petersburg': 'saint petersburg',
}
RESUME_SECTIONS = ('education', 'projects', 'achievements', 'skills', 'languages', 'experience', 'pinned_rules')
RESUME_SEED = {'basics': {key: None for key in ('name', 'email', 'phone', 'telegram', 'github', 'linkedin',
                                                'city', 'headline')},
               **{section: [] for section in RESUME_SECTIONS}}
CONTACT_FIELDS = ('name', 'email', 'phone', 'telegram', 'linkedin')
QUERY_TEMPLATES = {
    'A': [
        'именная стипендия студенты бакалавриат информатика конкурс {year}',
        'undergraduate computer science scholarship international students {year}',
        'стипендии студентам {university} {campus} {year} официальный конкурс',
        'AI student personal scholarship open applications {year}',
        'конкурс стипендий Сбер искусственный интеллект студенты {year}',
        'scholarship foundation computer science bachelor current call {year}',
        'хакатон олимпиада программирование денежные призы студенты {year}',
        'AI research student project grant competition cash prize {year}',
    ],
    'B': [
        'hh.ru стажер quant машинное обучение Python студент зарплата {year}',
        'Habr Карьера оплачиваемая стажировка AI quant Python студент {year}',
        'quant researcher paid internship undergraduate remote salary {year}',
        'AI internship crypto jobs board paid remote student {year}',
        'стажировка Сбер AI исследователь студент оплата {year}',
        'junior quantitative developer internship official careers {year}',
    ],
    'C': [
        'crypto quant paper trading tournament free entry no deposit {year} official rules',
        'бесплатный конкурс трейдинг демо без депозита призы {year}',
        'algorithmic trading simulated tournament free participation prizes {year}',
        'crypto boards paper trading competition no purchase necessary {year}',
        'quant demo trading competition official rules no real money no deposit {year}',
        'крипто алгоритмический турнир демо счет без взноса без реальной торговли {year}',
    ],
    'D': [
        'бесплатная практическая программа AI ML студенты проекты менторы {year}',
        'free practical career development program computer science students {year}',
        'бесплатная школа Python quant финтех студенты отбор {year}',
        'free machine learning summer school student project mentoring {year}',
        'Сбер AI бесплатная образовательная программа практические проекты {year}',
        'free coding fellowship mentorship career program undergraduate {year}',
    ],
}


def _now():
    return datetime.now(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _datetime(value, deadline=False):
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date) or (isinstance(value, str) and len(value) == 10):
        day = date.fromisoformat(str(value))
        parsed = datetime.combine(day, time.max if deadline else time.min, MSK)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ValueError('Timestamp must be ISO 8601 or YYYY-MM-DD')
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return parsed.astimezone(UTC)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _meaningful_content(item):
    fields = {'category', 'title', 'organizer', 'round', 'reward',
              'deadline', 'status', 'cost_flags', 'next_actions', 'location'}
    content = {key: value for key, value in item.items() if key in fields}
    # Evidence wording and citation aliases remain in stored data for review,
    # while versioning follows the actual criteria rather than their phrasing.
    content['requirements'] = sorted(
        [{key: requirement.get(key) for key in ('field', 'operator', 'value')}
         for requirement in item.get('requirements', [])], key=_json)
    # Identity enrichment and discovery aliases do not change the offer's
    # terms. They must not generate another "new" notification for this row.
    return content


def canonical_url(url):
    """Normalize public HTTP URLs; keep query values other than tracking tags."""
    if not isinstance(url, str) or '\\' in url or any(ord(c) < 32 for c in url):
        raise ValueError('URL must be a public HTTP(S) URL')
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or '').lower().rstrip('.')
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Invalid URL') from exc
    if parsed.scheme.lower() not in ('https', 'http') or not host or parsed.username or parsed.password:
        raise ValueError('URL must be public HTTP(S) without credentials')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid')):
        raise ValueError('Private URL is not permitted')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host or any(c.isspace() for c in host) or '\\' in host or '%' in host:
            raise ValueError('URL needs a public hostname')
        try:
            host = host.encode('idna').decode('ascii')
        except UnicodeError as exc:
            raise ValueError('Invalid hostname') from exc
        labels = host.split('.')
        if (not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                or not re.search(r'[a-z]', labels[-1])):
            raise ValueError('Invalid public hostname')
    else:
        if not address.is_global:
            raise ValueError('Private address is not permitted')
        if address.version == 6:
            host = '[' + host + ']'
    scheme = parsed.scheme.lower()
    authority = host + (':' + str(port) if port and (scheme, port) not in (('https', 443), ('http', 80)) else '')
    tracking = {'gclid', 'fbclid', 'yclid', 'msclkid', '_ga', '_gl', 'mc_cid', 'mc_eid'}
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in tracking]
    return urlunsplit((scheme, authority, parsed.path or '/', urlencode(sorted(query)), ''))


def _official_evidence(url, quote, official):
    if not official or not isinstance(quote, str) or not quote.strip() or not url:
        return False
    try:
        return urlsplit(canonical_url(url)).netloc == urlsplit(canonical_url(official)).netloc
    except ValueError:
        return False


def _cost_supported(item, key):
    terms = {
        'entry_fee': r'entry fee|participation fee|registration fee|tuition fee|взнос|плат[аы] за (?:участ\w*|обучени\w*)',
        'deposit': r'deposit|депозит',
        'real_trading': r'real trading|live trading|реальн\w* торгов|торговл\w* реальн',
        'paid_subscription': r'paid subscription|платн\w* подпис',
    }
    for evidence in item.get('evidence', []):
        quote = evidence.get('quote', '').casefold()
        if not _official_evidence(evidence.get('url'), quote, item.get('official_url')):
            continue
        if key == 'entry_fee' and re.search(
                r'(?:обучени\w*|участи\w*|регистраци\w*)[^.;]{0,35}бесплат\w*|'
                r'бесплат\w*[^.;]{0,35}(?:обучени\w*|участи\w*|регистраци\w*)|'
                r'free (?:tuition|participation|registration)|tuition[- ]free', quote):
            return True
        # Bind the negation to the cost phrase to avoid reading "no fee, deposit required"
        # as proof that a deposit is not required.
        if re.search(r'(?:no |without |без |не треб\w*\s+)(?:(?:any|mandatory|initial|required)\s+)?(?:' + terms[key] + ')', quote):
            return True
        if re.search(r'(?:' + terms[key] + r')[^.,;]{0,30}(?:not required|not charged|не треб\w*|не взима\w*|отсутств\w*)', quote):
            return True
    return False


def normalize_city(value):
    if not isinstance(value, str):
        return ''
    city = ' '.join(value.casefold().replace('ё', 'е').replace('г.', ' ').split())
    return CITY_ALIASES.get(city, city)


def _location_verdict(item, profile):
    """City is the owner's only hard filter: other city -> remote, or paid relocation/housing (🏠).

    Returns (mismatch, uncertain, reasons, flags). No location data -> no opinion (online
    scholarships and contests are city-agnostic)."""
    home = normalize_city(profile.get('city'))
    location = item.get('location') or {}
    cities = [normalize_city(c) for c in location.get('cities') or []]
    if not home or not cities or home in cities:
        return False, False, [], []
    # Hybrid in another city still requires presence there, so it does not count as remote.
    if location.get('remote') is True and location.get('hybrid') is not True:
        return False, False, ['Другой город, но работа удалённая'], ['remote']
    relocation = location.get('relocation_support') is True and any(
        evidence.get('field') == 'location.relocation_support'
        and _official_evidence(evidence.get('url'), evidence.get('quote'), item.get('official_url'))
        for evidence in item.get('evidence', []))
    if relocation:
        return False, False, ['Другой город, но компания оплачивает переезд или жильё'], ['relocation_support']
    if location.get('remote') is False or location.get('hybrid') is True:
        return True, False, ['Другой город, без удалённого формата'], ['other_city']
    return False, True, ['Другой город: удалённый формат или помощь с переездом не подтверждены'], []


# Country names written by the model or sources compare as ISO codes («РФ» == «Россия» == «RU»).
COUNTRY_ALIASES = {'ru': 'RU', 'rus': 'RU', 'рф': 'RU', 'россия': 'RU', 'российская федерация': 'RU',
                   'russia': 'RU', 'russian federation': 'RU', 'гражданин рф': 'RU', 'гражданство рф': 'RU'}
_CEFR = ('a1', 'a2', 'b1', 'b2', 'c1', 'c2')
_CEFR_WORDS = {'beginner': 'a1', 'elementary': 'a2', 'pre-intermediate': 'a2', 'pre intermediate': 'a2',
               'intermediate': 'b1', 'upper-intermediate': 'b2', 'upper intermediate': 'b2',
               'advanced': 'c1', 'fluent': 'c1', 'proficient': 'c2', 'native': 'c2'}
# Criteria that may only lower confidence, never reject (the owner: GPA changes over time).
SOFT_CRITERIA = ('gpa',)


def normalize_country(value):
    if isinstance(value, list):
        return [normalize_country(v) for v in value]
    if not isinstance(value, str):
        return value
    text = ' '.join(value.casefold().replace('ё', 'е').replace('.', ' ').split())
    return COUNTRY_ALIASES.get(text, text.upper() if len(text) == 2 else text)


def cefr_range(value):
    """(low, high) CEFR indexes from 'B1–B2', 'B2+', 'Upper-Intermediate', ['B2','C1'] …; None if unknown."""
    values = value if isinstance(value, list) else [value]
    found = []
    for raw in values:
        if not isinstance(raw, str):
            return None
        # Cyrillic look-alikes (В2, С1) are common in Russian texts.
        text = raw.casefold().translate(str.maketrans({'а': 'a', 'в': 'b', 'с': 'c'}))
        levels = re.findall(r'(?<![a-z])([abc][12])(?![0-9])', text)
        if not levels:
            levels = [level for word, level in sorted(_CEFR_WORDS.items(), key=lambda kv: -len(kv[0])) if word in text][:1]
        found += [_CEFR.index(level) for level in levels]
    return (min(found), max(found)) if found else None


def _english_verdict(operator, expected, actual):
    """'match' | 'borderline' | 'mismatch' | None (cannot compare). A range profile (B1–B2) is borderline
    against a requirement inside the range instead of a hard reject."""
    have, need = cefr_range(actual), cefr_range(expected)
    if have is None or need is None:
        return None
    if operator == 'max':
        return 'match' if have[1] <= need[1] else 'borderline' if have[0] <= need[1] else 'mismatch'
    required = need[0]  # eq / in / min: the lowest accepted level
    if have[0] >= required:
        return 'match'
    return 'borderline' if have[1] >= required else 'mismatch'


def evaluate(item, profile, today=None):
    """Assess observed constraints; rank is utility, never selection probability."""
    now = _datetime(today) if today is not None else _now()
    reasons, flags = [], []
    mismatch = False
    uncertain = False
    status = item.get('status', 'unknown')
    deadline = _datetime(item.get('deadline'), deadline=True)
    if deadline and deadline < now:
        status = 'closed'
        reasons.append('Application deadline has passed')
        flags.append('expired')
    if status not in ('active', 'closed', 'unknown'):
        status = 'unknown'
    official = item.get('official_url')
    supported = any(_official_evidence(e.get('url'), e.get('quote'), official) for e in item.get('evidence', []))
    if not supported:
        uncertain = True
        reasons.append('Official supporting quotation is missing')
        flags.append('unverified_evidence')
    requirements = item.get('requirements') or []
    if not requirements:
        uncertain = True
        reasons.append('Eligibility criteria have not been verified')
    for req in requirements:
        field, operator, expected = req.get('field'), req.get('operator'), req.get('value')
        actual = profile.get(field)
        evidence_ok = _official_evidence(req.get('evidence_url'), req.get('quote'), official)
        if not evidence_ok:
            uncertain = True
            reasons.append(f'{field}: official criterion quotation is missing')
            continue
        if actual is None:
            uncertain = True
            reasons.append(f'{field}: user confirmation needed')
            continue
        if field in ('citizenship', 'residence'):
            actual, expected = normalize_country(actual), normalize_country(expected)
        if field == 'gpa' and type(actual) in (int, float) and type(expected) in (int, float) \
                and (actual <= 5) != (expected <= 5):
            uncertain = True  # 10-point profile vs 5-point threshold (or vice versa): not comparable
            reasons.append('gpa: threshold uses a different grading scale')
            continue
        if field == 'english_level':
            verdict = _english_verdict(operator, expected, actual)
            if verdict == 'match':
                continue
            uncertain = True if verdict in ('borderline', None) else uncertain
            mismatch = mismatch or verdict == 'mismatch'
            reasons.append({'borderline': 'english_level: profile range is on the border of the requirement',
                            None: 'english_level: criterion cannot be compared reliably',
                            'mismatch': 'english_level: confirmed profile does not meet criterion'}[verdict])
            continue
        try:
            if operator == 'eq':
                matched = actual == expected
            elif operator == 'in':
                matched = actual in expected if not isinstance(actual, list) else any(v in expected for v in actual)
            elif operator == 'min':
                matched = actual >= expected
            elif operator == 'max':
                matched = actual <= expected
            else:
                raise TypeError('Unknown criterion operator')
        except (TypeError, ValueError):
            uncertain = True
            reasons.append(f'{field}: criterion cannot be compared reliably')
            continue
        if not matched and field in SOFT_CRITERIA:
            uncertain = True
            reasons.append(f'{field}: below the stated threshold (soft criterion, the owner decides)')
        elif not matched:
            mismatch = True
            reasons.append(f'{field}: confirmed profile does not meet criterion')
    costs = item.get('cost_flags') or {}
    if any(costs.get(key) is True for key in COST_KEYS):
        free_status = 'not_free'
        flags.append('financial_cost')
    elif item.get('category') == 'D' and costs.get('entry_fee') is False and _cost_supported(item, 'entry_fee'):
        # Tuition/participation must be explicitly free. A school need not
        # publish assertions about unrelated real trading or subscriptions.
        free_status = 'free'
    elif all(costs.get(key) is False and _cost_supported(item, key) for key in COST_KEYS):
        free_status = 'free'
    else:
        free_status = 'unverified'
    if item.get('category') in ('C', 'D'):
        if item.get('category') == 'C' and not any(req.get('field') in ('citizenship', 'residence') for req in requirements):
            uncertain = True
            reasons.append('Geographic participation restrictions have not been established')
        if free_status == 'not_free':
            mismatch = True
            reasons.append('Free opportunity requires a payment, deposit, trading, or subscription')
        elif free_status != 'free':
            uncertain = True
            reasons.append('Participation costs require official confirmation')
    paid = False
    if item.get('category') == 'B':
        # The owner wants unpaid and pay-unstated internships too: pay only affects ranking, never eligibility.
        reward = item.get('reward') or {}
        amount = reward.get('amount')
        if isinstance(amount, str) and re.search(r'\bunpaid\b|без оплат|неоплач|^\s*0(?:[.,]0+)?\s*(?:руб|₽|RUB|USD|EUR|\$|€)?\s*$', amount, re.I):
            flags.append('unpaid')
            reasons.append('Unpaid role (shown by owner preference)')
        elif reward.get('type') == 'salary':
            paid = True
        else:
            flags.append('pay_unknown')
            reasons.append('Pay is not stated')
    if status == 'unknown':
        uncertain = True
        reasons.append('Application availability is unverified')
    location_mismatch, location_uncertain, location_reasons, location_flags = _location_verdict(item, profile)
    mismatch = mismatch or location_mismatch
    uncertain = uncertain or location_uncertain
    reasons.extend(location_reasons)
    flags.extend(location_flags)
    eligibility = 'ineligible' if mismatch else 'needs_clarification' if uncertain else 'eligible'
    rank = (50 if eligibility == 'eligible' else 20 if eligibility == 'needs_clarification' else 0)
    rank += 20 if supported else 0
    rank += 10 if free_status == 'free' else 0
    rank += 8 if paid else 0
    rank += {'A': 6, 'B': 10, 'C': 4, 'D': 0}.get(item.get('category'), 0)
    assessment = item.get('assessment') or {}
    for field, preferred, weight in [('benefit', 'high', 8), ('registration_effort', 'low', 6),
                                     ('workload', 'low', 4), ('study_compatibility', 'high', 4),
                                     ('long_term_value', 'high', 6)]:
        level = assessment.get(field, 'unknown')
        rank += weight if level == preferred else weight // 2 if level == 'medium' else 0
    if status == 'closed':
        rank = 0
    elif deadline and timedelta(0) <= deadline - now <= timedelta(days=14):
        rank += 10
        flags.append('deadline_soon')
    return {'eligibility': eligibility, 'status': status, 'reasons': reasons,
            'rank': rank, 'rank_description': 'Evidence and action utility score; not selection probability',
            'free_status': free_status, 'flags': flags}



# ── Master profile, layer 2: resume content ─────────────────────────────────────────────────────
# Every fact carries id/source/confirmed. Only confirmed facts may reach a generated resume
# (stage 3). Years are integers: RenderCV turns the string '2025' into an invented month.
_ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_COMMON = {'id': 'id', 'source': 'str?', 'confirmed': 'bool', 'confirmed_note': 'str?', 'open_questions': 'list'}
RESUME_SCHEMA = {
    'education': {'institution': 'str', 'degree': 'str?', 'area': 'str?', 'start_year': 'year?',
                  'end_year': 'year?', 'gpa': 'scalar?', 'notes': 'list'},
    'projects': {'name': 'str', 'url': 'url?', 'role': 'str?', 'team': 'bool?', 'start_year': 'year?',
                 'end_year': 'year?', 'result': 'str?', 'stack': 'list', 'summary_ru': 'str?',
                 'summary_en': 'str?', 'highlights_ru': 'list', 'highlights_en': 'list', 'do_not_link': 'bool?'},
    'achievements': {'title': 'str', 'year': 'year?'},
    'skills': {'name': 'str', 'level': 'str?', 'evidence': 'list'},
    'languages': {'language': 'str', 'level': 'str?'},
    'experience': {'company': 'str', 'position': 'str?', 'location': 'str?', 'start_year': 'year?',
                   'end_year': 'year?', 'highlights_ru': 'list', 'highlights_en': 'list'},
    'pinned_rules': {'text': 'str', 'applies_to': 'str?'},
}
RESUME_OPS = ('add', 'replace', 'remove', 'confirm', 'set_basics')
# Contacts never come from model proposals; they arrive only through the owner's private import.
PROPOSABLE_BASICS = ('github', 'city', 'headline')


def _check_value(kind, value, where):
    optional = kind.endswith('?')
    kind = kind.rstrip('?')
    if value is None:
        if optional or kind == 'list':
            return [] if kind == 'list' else None
        raise ValueError(where + ' is required')
    if kind == 'id':
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise ValueError(where + ': id must be lowercase letters, digits and hyphens')
    elif kind == 'str':
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(where + ': nonempty string up to 2000 characters required')
        value = value.strip()
    elif kind == 'url':
        if not isinstance(value, str) or not value.startswith('https://') or len(value) > 500:
            raise ValueError(where + ': https URL required')
    elif kind == 'year':
        if type(value) is not int or not 1990 <= value <= 2100:
            raise ValueError(where + ': year must be an integer 1990–2100, not a string')
    elif kind == 'bool':
        if type(value) is not bool:
            raise ValueError(where + ': boolean required')
    elif kind == 'scalar':
        if not ((type(value) in (int, float) and math.isfinite(value)) or (isinstance(value, str) and len(value) <= 50)):
            raise ValueError(where + ': short number or string required')
    elif kind == 'list':
        if (not isinstance(value, list) or len(value) > 50
                or not all(isinstance(v, str) and v.strip() and len(v) <= 500 for v in value)):
            raise ValueError(where + ': list of up to 50 short strings required')
        value = [v.strip() for v in value]
    return value


def _validate_resume_entry(section, entry):
    if section not in RESUME_SCHEMA:
        raise ValueError('Unknown resume section: ' + str(section))
    if not isinstance(entry, dict):
        raise ValueError(section + ': entry must be an object')
    entry = {'confirmed': False, **entry}
    schema = {**_COMMON, **RESUME_SCHEMA[section]}
    unknown = set(entry) - set(schema)
    if unknown:
        raise ValueError(section + ': unknown fields ' + ', '.join(sorted(unknown)))
    result = {key: _check_value(kind, entry.get(key), f'{section}.{entry.get("id", "?")}.{key}')
              for key, kind in schema.items() if key in entry or not kind.endswith('?')}
    return result


def validate_resume(data):
    """Normalize a complete resume_profile document or raise ValueError."""
    if not isinstance(data, dict) or set(data) - {'basics', *RESUME_SECTIONS}:
        raise ValueError('Resume profile accepts only basics and the known sections')
    basics = data.get('basics') or {}
    if not isinstance(basics, dict) or set(basics) - set(RESUME_SEED['basics']):
        raise ValueError('Unknown basics field')
    result = {'basics': {key: _check_value('str?', basics.get(key), 'basics.' + key) if basics.get(key) is not None
                         else None for key in RESUME_SEED['basics']}}
    for section in RESUME_SECTIONS:
        entries = data.get(section) or []
        if not isinstance(entries, list) or len(entries) > 60:
            raise ValueError(section + ' must be a list of up to 60 entries')
        result[section] = [_validate_resume_entry(section, entry) for entry in entries]
        ids = [entry['id'] for entry in result[section]]
        if len(ids) != len(set(ids)):
            raise ValueError(section + ': duplicate ids')
    evidence_targets = {entry['id'] for section in ('projects', 'experience') for entry in result[section]}
    for skill in result['skills']:
        missing = [ref for ref in skill.get('evidence', []) if ref not in evidence_targets]
        if missing:
            raise ValueError(f"skills.{skill['id']}: evidence refers to unknown project/experience {missing}")
    return result


def apply_resume_ops(data, ops, *, owner_confirmed=False):
    """Apply proposal operations to a copy; with owner_confirmed, every added/replaced fact is confirmed."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= 50:
        raise ValueError('Resume proposal needs 1–50 operations')
    result = json.loads(_json(data))
    for op in ops:
        if not isinstance(op, dict) or op.get('op') not in RESUME_OPS:
            raise ValueError('Unknown resume operation')
        kind = op['op']
        if kind == 'set_basics':
            if set(op) - {'op', 'field', 'value'} or op.get('field') not in PROPOSABLE_BASICS:
                raise ValueError('Only github, city and headline can be proposed; contacts come from the private import')
            kind_check = 'url?' if op['field'] == 'github' else 'str?'
            result['basics'][op['field']] = _check_value(kind_check, op.get('value'), 'basics.' + op['field'])
            continue
        section = op.get('section')
        if section not in RESUME_SECTIONS:
            raise ValueError('Unknown resume section: ' + str(section))
        entries = result[section]
        if kind in ('add', 'replace'):
            if set(op) - {'op', 'section', 'item'}:
                raise ValueError('add/replace take only section and item')
            if not isinstance(op.get('item'), dict):
                raise ValueError('add/replace need an item object')
            item = dict(op['item'])
            if owner_confirmed:
                item['confirmed'] = True
            item = _validate_resume_entry(section, item)
            index = next((i for i, e in enumerate(entries) if e['id'] == item['id']), None)
            if kind == 'add' and index is not None:
                raise ValueError(f"{section}.{item['id']} already exists; use replace")
            if kind == 'replace' and index is None:
                raise ValueError(f"{section}.{item['id']} does not exist; use add")
            if index is None:
                entries.append(item)
            else:
                entries[index] = item
        else:
            if set(op) - {'op', 'section', 'id'}:
                raise ValueError('remove/confirm take only section and id')
            index = next((i for i, e in enumerate(entries) if e['id'] == op.get('id')), None)
            if index is None:
                raise ValueError(f"{section}.{op.get('id')} does not exist")
            if kind == 'remove':
                entries.pop(index)
            else:
                entries[index]['confirmed'] = True
    return validate_resume(result)


def resume_entry_digest(entry):
    """Short content hash of a resume entry (without its confirmed flag): a confirmation button
    carries it, so the owner confirms exactly the version he was shown."""
    content = {key: value for key, value in entry.items() if key != 'confirmed'}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:6]


def redact_resume(data):
    """Model-facing view: contact values are never shown, only whether they are on file."""
    view = json.loads(_json(data))
    for key in CONTACT_FIELDS:
        if view['basics'].get(key):
            view['basics'][key] = '[скрыто]'
    return view


class Store:
    """Persistent store. Every operation opens a short-lived SQLite connection."""

    def __init__(self, path):
        self.path = str(path)
        if self.path == ':memory:':
            raise ValueError('Use a filesystem path for the persistent store')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profile_proposals(token TEXT PRIMARY KEY, patch TEXT NOT NULL, base_version INTEGER NOT NULL, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS resume_profile(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, data TEXT NOT NULL, fingerprint TEXT NOT NULL, version INTEGER NOT NULL, bookmarked INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id));
                CREATE TABLE IF NOT EXISTS history(item_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, changed_at TEXT NOT NULL, PRIMARY KEY(item_id,version));
                CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences(category TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, interval_hours REAL NOT NULL DEFAULT 24, query_cursor INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY, data TEXT NOT NULL, category TEXT NOT NULL, content_hash TEXT, checked_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS source_categories(url TEXT NOT NULL REFERENCES sources(url), category TEXT NOT NULL, checked_at TEXT, error TEXT, content_hash TEXT, PRIMARY KEY(url,category));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, category TEXT NOT NULL, started_at TEXT NOT NULL, lease_until TEXT NOT NULL, ended_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, kind TEXT NOT NULL, content_hash TEXT NOT NULL, state TEXT NOT NULL, message_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(item_id,kind,content_hash));
            ''')
            db.execute('INSERT OR IGNORE INTO profile VALUES(1,1,?)', (_json(PROFILE_SEED),))
            db.execute('INSERT OR IGNORE INTO resume_profile VALUES(1,1,?)', (_json(RESUME_SEED),))
            proposal_columns = {row['name'] for row in db.execute('PRAGMA table_info(profile_proposals)')}
            if 'scope' not in proposal_columns:
                db.execute("ALTER TABLE profile_proposals ADD COLUMN scope TEXT NOT NULL DEFAULT 'criteria'")
            db.executemany('INSERT OR IGNORE INTO preferences(category,interval_hours) VALUES(?,?)', list(DEFAULT_INTERVALS.items()))
            existing_columns = {row['name'] for row in db.execute('PRAGMA table_info(source_categories)')}
            for column in ('checked_at', 'error', 'content_hash'):
                if column not in existing_columns:
                    db.execute(f'ALTER TABLE source_categories ADD COLUMN {column} TEXT')
            db.execute('INSERT OR IGNORE INTO source_categories(url,category) SELECT url,category FROM sources')

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _category(category):
        if category not in CATEGORIES:
            raise ValueError('Category must be A, B, C, or D')
        return category

    def profile(self):
        # Merge onto the current seed so databases created before new fields existed stay valid.
        with self._db() as db:
            row = db.execute('SELECT version,data FROM profile WHERE id=1').fetchone()
            return {**PROFILE_SEED, **json.loads(row['data']), 'version': row['version']}

    @staticmethod
    def _validate_profile_patch(patch):
        if not isinstance(patch, dict) or not patch:
            raise ValueError('Profile patch must be a nonempty object')
        numeric = {'year', 'study_start', 'study_end', 'age', 'gpa', 'income', 'quant_experience', 'work_experience',
                   'commercial_experience', 'weekly_hours', 'min_salary'}
        boolean = {'full_time', 'relocation', 'team_available', 'kyc_willingness'}
        list_fields = {'languages', 'skills', 'interests'}
        for field, value in patch.items():
            if field not in PROFILE_SEED:
                raise ValueError('Unknown profile field: ' + field)
            if value is None:
                continue
            if field in numeric:
                if type(value) not in (int, float) or value < 0 or value > 1000000000:
                    raise ValueError('Invalid numeric profile field: ' + field)
                if field in {'year', 'study_start', 'study_end', 'age'} and type(value) is not int:
                    raise ValueError('Integer required: ' + field)
                if field == 'year' and not 1 <= value <= 12:
                    raise ValueError('Invalid study year')
                if field == 'age' and not 0 <= value <= 130:
                    raise ValueError('Invalid age')
                if field == 'weekly_hours' and not 0 <= value <= 168:
                    raise ValueError('Weekly hours must be between 0 and 168')
            elif field in boolean:
                if type(value) is not bool:
                    raise ValueError('Boolean required: ' + field)
            elif field in list_fields:
                if not isinstance(value, list) or not all(isinstance(v, str) and len(v) <= 500 for v in value) or len(value) > 100:
                    raise ValueError('List of short strings required: ' + field)
            elif field == 'available_from':
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError):
                    raise ValueError('available_from must be a YYYY-MM-DD date') from None
            elif not isinstance(value, str) or len(value) > 1000:
                raise ValueError('String required: ' + field)

    MAX_PENDING_PROPOSALS = 3

    @staticmethod
    def _live_proposals(db):
        """Unused, unexpired proposals whose base version is still current (any version bump —
        confirmation, import, a confirmed fact — makes older proposals of that layer stale)."""
        versions = {'criteria': db.execute('SELECT version FROM profile WHERE id=1').fetchone()[0],
                    'resume': db.execute('SELECT version FROM resume_profile WHERE id=1').fetchone()[0]}
        rows = db.execute('SELECT * FROM profile_proposals WHERE used=0 AND expires_at>? ORDER BY expires_at',
                          (_iso(_now()),)).fetchall()
        return [row for row in rows if row['base_version'] == versions.get(row['scope'])]

    def _propose(self, scope, patch, base_version):
        token = secrets.token_urlsafe(32)
        with self._db(write=True) as db:
            pending = len(self._live_proposals(db))
            if pending >= self.MAX_PENDING_PROPOSALS:
                raise ValueError('Too many pending profile proposals; the owner must confirm or cancel them first')
            db.execute('INSERT INTO profile_proposals(token,patch,base_version,expires_at,scope) VALUES(?,?,?,?,?)',
                       (token, _json(patch), base_version, _iso(_now() + timedelta(minutes=30)), scope))
        return {'token': token, 'scope': scope, 'patch': patch, 'base_version': base_version}

    def propose_profile(self, patch):
        self._validate_profile_patch(patch)
        return self._propose('criteria', patch, self.profile()['version'])

    def resume_profile(self, redact=True):
        with self._db() as db:
            row = db.execute('SELECT version,data FROM resume_profile WHERE id=1').fetchone()
        data = validate_resume(json.loads(row['data']))
        return {**(redact_resume(data) if redact else data), 'version': row['version']}

    def propose_resume(self, ops):
        if not isinstance(ops, list) or not 1 <= len(ops) <= 10:
            raise ValueError('A resume proposal takes 1–10 operations so the owner can review it')
        current = self.resume_profile(redact=False)
        version = current.pop('version')
        apply_resume_ops(current, ops, owner_confirmed=True)  # dry run: reject invalid proposals early
        return self._propose('resume', ops, version)

    def confirm_profile(self, token):
        """Owner-only (trusted adapters): apply one unexpired, unused proposal of either layer."""
        with self._db(write=True) as db:
            proposal = db.execute('SELECT * FROM profile_proposals WHERE token=?', (token,)).fetchone()
            if not proposal or proposal['used'] or _datetime(proposal['expires_at']) <= _now():
                raise ValueError('Profile proposal is expired, already used, or stale')
            table = 'resume_profile' if proposal['scope'] == 'resume' else 'profile'
            current = db.execute(f'SELECT * FROM {table} WHERE id=1').fetchone()
            if proposal['base_version'] != current['version']:
                raise ValueError('Profile proposal is expired, already used, or stale')
            if proposal['scope'] == 'resume':
                data = apply_resume_ops(validate_resume(json.loads(current['data'])), json.loads(proposal['patch']),
                                        owner_confirmed=True)
            else:
                data = {**PROFILE_SEED, **json.loads(current['data']), **json.loads(proposal['patch'])}
            version = current['version'] + 1
            db.execute(f'UPDATE {table} SET version=?,data=? WHERE id=1', (version, _json(data)))
            db.execute('UPDATE profile_proposals SET used=1 WHERE token=?', (token,))
        result = redact_resume(data) if proposal['scope'] == 'resume' else data
        return {**result, 'version': version, 'scope': proposal['scope']}

    def proposal(self, token):
        """Pending proposal for display (scope + patch), or None when used/expired/unknown."""
        row = next((r for r in self._live_rows() if r['token'] == token), None)
        if row is None:
            return None
        return {'token': token, 'scope': row['scope'], 'patch': json.loads(row['patch'])}

    def _live_rows(self):
        with self._db() as db:
            return self._live_proposals(db)

    def pending_proposals(self):
        return [{'token': r['token'], 'scope': r['scope'], 'patch': json.loads(r['patch'])} for r in self._live_rows()]

    def cancel_proposal(self, token):
        with self._db(write=True) as db:
            return db.execute('UPDATE profile_proposals SET used=1 WHERE token=? AND used=0', (token,)).rowcount == 1

    def confirm_resume_entry(self, section, entry_id, digest):
        """Owner-only (button press after seeing the whole entry): mark one stored fact confirmed.

        Never exposed as a model action. `digest` must match the entry's current content (checked in
        the same transaction), otherwise ValueError. Returns False when it was already confirmed."""
        if section not in RESUME_SECTIONS:
            raise ValueError('Unknown resume section: ' + str(section))
        with self._db(write=True) as db:
            row = db.execute('SELECT version,data FROM resume_profile WHERE id=1').fetchone()
            data = validate_resume(json.loads(row['data']))
            entry = next((e for e in data[section] if e['id'] == entry_id), None)
            if entry is None:
                raise KeyError('Resume entry not found')
            if resume_entry_digest(entry) != digest:
                raise ValueError('Resume entry changed since it was shown')
            if entry.get('confirmed'):
                return False
            data = apply_resume_ops(data, [{'op': 'confirm', 'section': section, 'id': entry_id}])
            db.execute('UPDATE resume_profile SET version=?,data=? WHERE id=1', (row['version'] + 1, _json(data)))
        return True

    def profile_missing(self):
        """What the bot should still ask about: empty criteria and facts not yet confirmed."""
        profile = self.profile()
        resume = self.resume_profile(redact=False)
        unconfirmed = [{'section': section, 'id': entry['id'],
                        'label': entry.get('name') or entry.get('title') or entry.get('institution')
                        or entry.get('language') or entry.get('company') or entry.get('text'),
                        'open_questions': entry.get('open_questions', [])}
                       for section in RESUME_SECTIONS for entry in resume[section] if not entry.get('confirmed')]
        return {'criteria_unknown': [key for key in PROFILE_SEED if profile.get(key) in (None, [], '')],
                'resume_unconfirmed': unconfirmed,
                'contacts_on_file': [key for key in CONTACT_FIELDS if resume['basics'].get(key)]}

    def import_master(self, master, apply=False):
        """Trusted operator import of the owner-reviewed private profile (never exposed to the model).

        Dry run by default: returns what would change. Idempotent: the same file twice changes nothing."""
        if not isinstance(master, dict):
            raise ValueError('Master profile must be an object')
        criteria = {key: value for key, value in (master.get('criteria') or {}).items() if key in PROFILE_SEED}
        if criteria:
            self._validate_profile_patch(criteria)
        resume = validate_resume(master['resume_profile']) if master.get('resume_profile') is not None else None
        sources = master.get('extra_sources') or []
        if not isinstance(sources, list) or len(sources) > 50:
            raise ValueError('extra_sources must be a short list')
        profile = self.profile()
        current_resume = self.resume_profile(redact=False)
        criteria_changes = sorted(key for key, value in criteria.items() if profile.get(key) != value)
        resume_changed = resume is not None and resume != {k: v for k, v in current_resume.items() if k != 'version'}
        for source in sources:
            if not isinstance(source, dict) or source.get('category') not in CATEGORIES:
                raise ValueError('Every extra source needs a category A–D')
            canonical_url(source.get('url'))
        known = {source['url'] for source in self.sources()}
        new_sources = [source for source in sources if canonical_url(source.get('url')) not in known]
        summary = {'apply': bool(apply), 'criteria_changes': criteria_changes, 'resume_changed': resume_changed,
                   'resume_counts': {s: len(resume[s]) for s in RESUME_SECTIONS} if resume else None,
                   'resume_confirmed': sum(e.get('confirmed', False) for s in RESUME_SECTIONS for e in resume[s]) if resume else None,
                   'sources_new': len(new_sources)}
        if not apply:
            return summary
        with self._db(write=True) as db:
            if criteria_changes:
                row = db.execute('SELECT version,data FROM profile WHERE id=1').fetchone()
                db.execute('UPDATE profile SET version=?,data=? WHERE id=1',
                           (row['version'] + 1, _json({**PROFILE_SEED, **json.loads(row['data']), **criteria})))
            if resume_changed:
                row = db.execute('SELECT version FROM resume_profile WHERE id=1').fetchone()
                db.execute('UPDATE resume_profile SET version=?,data=? WHERE id=1', (row['version'] + 1, _json(resume)))
        for source in new_sources:
            self.add_source({'url': source['url'], 'category': source['category'],
                             'title': source.get('title', ''), 'trust': source.get('label', 'unknown')})
        return summary

    def _validate_item(self, raw):
        if not isinstance(raw, dict):
            raise ValueError('Item must be an object')
        fields = {'category', 'title', 'organizer', 'url', 'official_url', 'identity_url', 'round', 'external_id', 'reward', 'deadline', 'verified_at', 'status', 'requirements', 'evidence', 'cost_flags', 'next_actions', 'assessment', 'location'}
        data = {k: v for k, v in raw.items() if k in fields}
        self._category(data.get('category'))
        for field in ('title', 'organizer'):
            if not isinstance(data.get(field), str) or not data[field].strip() or len(data[field]) > 2000:
                raise ValueError(field + ' must be a nonempty string')
        data['url'] = canonical_url(data.get('url'))
        data['official_url'] = canonical_url(data['official_url']) if data.get('official_url') else None
        data['identity_url'] = canonical_url(data['identity_url']) if data.get('identity_url') else None
        for field in ('round', 'external_id'):
            if data.get(field) is not None and not isinstance(data[field], (str, int)):
                raise ValueError(field + ' must be a string or number')
            data[field] = str(data[field]) if data.get(field) is not None else ''
        data['deadline'] = _iso(_datetime(data['deadline'], deadline=True)) if data.get('deadline') else None
        data['verified_at'] = _iso(_datetime(data['verified_at'])) if data.get('verified_at') else None
        data.setdefault('status', 'unknown')
        if data['status'] not in ('active', 'closed', 'unknown'):
            raise ValueError('Invalid opportunity status')
        reward = data.setdefault('reward', {'type': 'career', 'amount': None})
        if not isinstance(reward, dict) or reward.get('type') not in ('personal_scholarship', 'prize', 'project_grant', 'salary', 'career'):
            raise ValueError('Invalid reward type')
        if reward.get('amount') is not None and not isinstance(reward['amount'], str):
            raise ValueError('Reward amount must be an evidenced string or null')
        data['reward'] = {'type': reward['type'], 'amount': reward.get('amount')}
        for field in ('requirements', 'evidence'):
            data.setdefault(field, [])
            if not isinstance(data[field], list) or len(data[field]) > 100:
                raise ValueError(field + ' must be a list')
            for entry in data[field]:
                if not isinstance(entry, dict):
                    raise ValueError('Evidence and criteria must be objects')
                urlkey = 'evidence_url' if field == 'requirements' else 'url'
                if entry.get(urlkey):
                    entry[urlkey] = canonical_url(entry[urlkey])
                if entry.get('quote') is not None and not isinstance(entry['quote'], str):
                    raise ValueError('Quotation must be a string')
                if field == 'requirements':
                    if (not isinstance(entry.get('field'), str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', entry['field'])
                            or entry.get('operator') not in ('eq', 'in', 'min', 'max')):
                        raise ValueError('Invalid profile criterion')
                    if 'value' not in entry:
                        raise ValueError('Criterion value is required')
                    value = entry['value']
                    def valid_scalar(value):
                        return (value is None or type(value) is bool
                                or (isinstance(value, str) and len(value) <= 2000)
                                or (type(value) in (int, float) and abs(value) <= 10**12 and math.isfinite(value)))
                    if entry['operator'] == 'in':
                        if not isinstance(value, list) or not 1 <= len(value) <= 100 or not all(valid_scalar(v) for v in value):
                            raise ValueError('Membership criterion requires a bounded scalar list')
                    elif not valid_scalar(value):
                        raise ValueError('Criterion requires a scalar value')
                    if entry['operator'] in ('min', 'max') and type(value) not in (int, float):
                        raise ValueError('Range criterion requires a numeric value')
        costs = data.setdefault('cost_flags', {})
        if not isinstance(costs, dict) or any(k not in COST_KEYS or type(v) not in (bool, type(None)) for k, v in costs.items()):
            raise ValueError('Invalid cost flags')
        data['cost_flags'] = {key: costs.get(key) for key in COST_KEYS}
        assessment = data.setdefault('assessment', {})
        assessment_fields = ('benefit', 'registration_effort', 'workload', 'study_compatibility', 'long_term_value')
        if not isinstance(assessment, dict) or set(assessment) - {*assessment_fields, 'rationale'}:
            raise ValueError('Only qualitative benefit/effort assessments are accepted; no selection probabilities')
        if any(assessment.get(key, 'unknown') not in ('low', 'medium', 'high', 'unknown') for key in assessment_fields):
            raise ValueError('Assessment levels must be low, medium, high or unknown')
        if not isinstance(assessment.get('rationale', ''), str) or len(assessment.get('rationale', '')) > 1500:
            raise ValueError('Assessment rationale must be a bounded string')
        data['assessment'] = {**{key: assessment.get(key, 'unknown') for key in assessment_fields},
                              'rationale': assessment.get('rationale', '')}
        data.setdefault('next_actions', [])
        if not isinstance(data['next_actions'], list) or not all(isinstance(v, str) for v in data['next_actions']):
            raise ValueError('Next actions must be strings')
        location = data.get('location')
        if location is not None:
            if not isinstance(location, dict) or set(location) - {'cities', 'remote', 'hybrid', 'relocation_support'}:
                raise ValueError('Location accepts only cities, remote, hybrid and relocation_support')
            cities = location.get('cities') or []
            if (not isinstance(cities, list) or len(cities) > 30
                    or not all(isinstance(c, str) and c.strip() and len(c) <= 100 for c in cities)):
                raise ValueError('Location cities must be a short list of city names')
            if any(location.get(k) not in (True, False, None) for k in ('remote', 'hybrid', 'relocation_support')):
                raise ValueError('Location flags must be true, false or null')
            data['location'] = {'cities': [c.strip() for c in cities],
                                **{k: location.get(k) for k in ('remote', 'hybrid', 'relocation_support')}}
        _json(data)
        return data

    @staticmethod
    def _aliases(data):
        # Prefer explicit identifiers over discovery links: an employer board
        # can list many postings and must not overrule their distinct identities.
        aliases = []
        if data.get('identity_url'):
            aliases.append(_json(['url', data['identity_url'], data['round']]))
        if data.get('external_id'):
            aliases.append(_json(['external', data['organizer'].strip().casefold(), data['external_id'], data['round']]))
        return aliases or [_json(['url', data['url'], data['round']])]

    def _item(self, row, profile=None):
        if row is None:
            raise KeyError('Opportunity not found')
        data = json.loads(row['data'])
        data.update(id=row['id'], version=row['version'], bookmarked=bool(row['bookmarked']), hidden=bool(row['hidden']), created_at=row['created_at'], updated_at=row['updated_at'])
        data['evaluation'] = evaluate(data, profile or self.profile())
        return data

    def upsert(self, item):
        data = self._validate_item(json.loads(_json(item)))
        fingerprint = _hash(_meaningful_content(data))
        aliases = self._aliases(data)
        now = _iso(_now())
        with self._db(write=True) as db:
            ids = {r['item_id'] for alias in aliases for r in db.execute('SELECT item_id FROM aliases WHERE alias=?', (alias,))}
            if len(ids) > 1:
                raise ValueError('Conflicting opportunity identities; manual review needed')
            if not ids and (data.get('identity_url') or data.get('external_id')):
                # Promote a previously URL-only lead, without allowing a
                # generic board to merge two already identified postings.
                fallback = _json(['url', data['url'], data['round']])
                candidate = db.execute('SELECT i.* FROM items i JOIN aliases a ON i.id=a.item_id WHERE a.alias=?', (fallback,)).fetchone()
                if candidate:
                    previous = json.loads(candidate['data'])
                    if not previous.get('identity_url') and not previous.get('external_id'):
                        ids.add(candidate['id'])
            item_id = next(iter(ids)) if ids else uuid.uuid4().hex
            old = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if old:
                previous = json.loads(old['data'])
                if previous.get('external_id') and data.get('external_id') and previous['external_id'] != data['external_id']:
                    raise ValueError('Conflicting external posting identities; manual review needed')
                if previous.get('external_id') and not data.get('external_id'):
                    data['external_id'] = previous['external_id']
                    fingerprint = _hash(_meaningful_content(data))
            version = old['version'] + int(old['fingerprint'] != fingerprint) if old else 1
            changed = old is None or old['fingerprint'] != fingerprint
            if old:
                db.execute('UPDATE items SET data=?,fingerprint=?,version=?,updated_at=? WHERE id=?', (_json(data), fingerprint, version, now, item_id))
            else:
                db.execute('INSERT INTO items(id,data,fingerprint,version,created_at,updated_at) VALUES(?,?,?,?,?,?)', (item_id, _json(data), fingerprint, version, now, now))
            for alias in aliases:
                db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (alias, item_id))
            if changed:
                db.execute('INSERT INTO history VALUES(?,?,?,?)', (item_id, version, _json(data), now))
                db.execute('INSERT INTO changes VALUES(?,?,?,?,?)', (uuid.uuid4().hex, item_id, version, 'new' if old is None else 'changed', now))
        return self.get(item_id)

    def get(self, item_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        return self._item(row)

    def list(self, category=None, bookmarked=False, days=None, limit=20, include_hidden=False, status=None, recent_days=None):
        if category is not None:
            self._category(category)
        if status is not None and status not in ('active', 'closed', 'unknown'):
            raise ValueError('Invalid status')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Limit must be 1..1000')
        if days is not None and (type(days) not in (int, float) or days < 0):
            raise ValueError('Days must be nonnegative')
        if recent_days is not None and (type(recent_days) not in (int, float) or not math.isfinite(recent_days) or recent_days < 0):
            raise ValueError('Recent days must be finite and nonnegative')
        profile = self.profile()
        with self._db() as db:
            rows = db.execute('SELECT * FROM items ORDER BY updated_at DESC,id').fetchall()
        result = []
        for row in rows:
            if (bookmarked and not row['bookmarked']) or (not include_hidden and row['hidden']):
                continue
            data = self._item(row, profile)
            if category is not None and data['category'] != category:
                continue
            if status is not None and data['evaluation']['status'] != status:
                continue
            if recent_days is not None and _datetime(data['created_at']) < _now() - timedelta(days=recent_days):
                continue
            if days is not None:
                deadline = _datetime(data['deadline'])
                if deadline is None or not _now() <= deadline <= _now() + timedelta(days=days):
                    continue
            result.append(data)
        result.sort(key=lambda item: item['evaluation']['rank'], reverse=True)
        return result[:limit]

    def rejected(self, limit=30):
        """Filtered-out offers with their reasons, so the owner can catch filter mistakes."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Limit must be 1..1000')
        items = self.list(limit=1000, include_hidden=True)
        return [item for item in items if item['evaluation']['eligibility'] == 'ineligible'][:limit]

    def action(self, item_id, action):
        changes = {'save': ('bookmarked', 1), 'unsave': ('bookmarked', 0), 'hide': ('hidden', 1), 'unhide': ('hidden', 0)}
        if action not in (*changes, 'recheck'):
            raise ValueError('Unsupported action')
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise KeyError('Opportunity not found')
            if action == 'recheck':
                data = json.loads(row['data'])
                urls = {data['url'], data.get('official_url')}
                for url in urls - {None}:
                    source = {'url': url, 'category': data['category'], 'title': data['title']}
                    db.execute('INSERT INTO sources(url,data,category) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET checked_at=NULL', (url, _json(source), data['category']))
                    db.execute('INSERT OR IGNORE INTO source_categories(url,category) VALUES(?,?)', (url, data['category']))
                    db.execute('UPDATE source_categories SET checked_at=NULL WHERE url=? AND category=?', (url, data['category']))
            else:
                column, value = changes[action]
                db.execute(f'UPDATE items SET {column}=? WHERE id=?', (value, item_id))
        return self.get(item_id)

    def preferences(self, category=None, enabled=None, interval_hours=None):
        if category is not None:
            self._category(category)
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('Enabled must be a boolean')
        if interval_hours is not None and (type(interval_hours) not in (int, float) or not 6 <= interval_hours <= 720):
            raise ValueError('Interval must be between 6 and 720 hours')
        with self._db(write=enabled is not None or interval_hours is not None) as db:
            for cat in [category] if category else CATEGORIES:
                if enabled is not None:
                    db.execute('UPDATE preferences SET enabled=? WHERE category=?', (int(enabled), cat))
                if interval_hours is not None:
                    db.execute('UPDATE preferences SET interval_hours=? WHERE category=?', (interval_hours, cat))
            rows = db.execute('SELECT category,enabled,interval_hours FROM preferences ORDER BY category').fetchall()
        data = {row['category']: {'category': row['category'], 'enabled': bool(row['enabled']), 'interval_hours': row['interval_hours']} for row in rows}
        return data[category] if category else data

    def sources(self):
        with self._db() as db:
            rows = db.execute('SELECT * FROM sources ORDER BY category,url').fetchall()
            associations = {}
            for association in db.execute('SELECT url,category FROM source_categories ORDER BY category'):
                associations.setdefault(association['url'], []).append(association['category'])
        return [{**json.loads(row['data']), 'category': row['category'],
                 'categories': associations.get(row['url'], [row['category']]),
                 'content_hash': row['content_hash'], 'checked_at': row['checked_at'], 'error': row['error']} for row in rows]

    def add_source(self, source):
        if not isinstance(source, dict):
            raise ValueError('Source must be an object')
        data = dict(source)
        data['url'] = canonical_url(data.get('url'))
        self._category(data.get('category'))
        with self._db(write=True) as db:
            db.execute('INSERT INTO sources(url,data,category) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET data=excluded.data', (data['url'], _json(data), data['category']))
            db.execute('INSERT OR IGNORE INTO source_categories(url,category) VALUES(?,?)', (data['url'], data['category']))
        return next(s for s in self.sources() if s['url'] == data['url'])

    def due_sources(self, category, limit=6):
        pref = self.preferences(self._category(category))
        with self._db() as db:
            checks = {row['url']: dict(row) for row in db.execute('SELECT * FROM source_categories WHERE category=?', (category,))}
        result = []
        for source in self.sources():
            check = checks.get(source['url'])
            if check is None:
                continue
            hours = 4 if check['error'] else pref['interval_hours']
            if check['checked_at'] is None or _datetime(check['checked_at']) <= _now() - timedelta(hours=hours):
                result.append({**source, 'category_checked_at': check['checked_at'], 'category_error': check['error']})
        return sorted(result, key=lambda source: source['category_checked_at'] or '')[:limit]

    def is_due(self, category, now=None):
        """Cheap scheduler gate; failed attempts back off for at least four hours."""
        preferences = self.preferences(self._category(category))
        if not preferences['enabled']:
            return False
        instant = _datetime(now) if now is not None else _now()
        with self._db() as db:
            run = db.execute('SELECT * FROM runs WHERE category=? ORDER BY started_at DESC LIMIT 1', (category,)).fetchone()
        if run is None:
            return True
        if run['ended_at'] is None and _datetime(run['lease_until']) > instant:
            return False
        failed = bool(run['error']) or run['ended_at'] is None
        delay = timedelta(hours=4 if failed else preferences['interval_hours'])
        reference = _datetime(run['ended_at'] or run['started_at'])
        return instant >= reference + delay

    def record_source(self, url, content_hash, checked_at=None, error=None, category=None):
        url = canonical_url(url)
        if category is not None:
            self._category(category)
        instant = _iso(_datetime(checked_at) if checked_at else _now())
        with self._db(write=True) as db:
            cursor = db.execute('UPDATE sources SET content_hash=?,checked_at=?,error=? WHERE url=?', (content_hash, instant, error, url))
            if cursor.rowcount != 1:
                raise KeyError('Source not found')
            if category is None:
                db.execute('UPDATE source_categories SET content_hash=?,checked_at=?,error=? WHERE url=?', (content_hash, instant, error, url))
            elif db.execute('UPDATE source_categories SET content_hash=?,checked_at=?,error=? WHERE url=? AND category=?', (content_hash, instant, error, url, category)).rowcount != 1:
                raise KeyError('Source category association not found')
        return next(s for s in self.sources() if s['url'] == url)

    def begin_run(self, category):
        self._category(category)
        now = _now()
        run_id = uuid.uuid4().hex
        with self._db(write=True) as db:
            if db.execute('SELECT id FROM runs WHERE ended_at IS NULL AND lease_until>?', (_iso(now),)).fetchone():
                raise RuntimeError('A scan is already running')
            db.execute('INSERT INTO runs(id,category,started_at,lease_until) VALUES(?,?,?,?)', (run_id, category, _iso(now), _iso(now + timedelta(minutes=30))))
        return run_id

    def end_run(self, run_id, error=None):
        with self._db(write=True) as db:
            if db.execute('UPDATE runs SET ended_at=?,error=? WHERE id=?', (_iso(_now()), error, run_id)).rowcount != 1:
                raise KeyError('Run not found')

    def plan_queries(self, category, manual_query=None, limit=4):
        self._category(category)
        if manual_query is not None:
            if not isinstance(manual_query, str) or not manual_query.strip() or len(manual_query) > 2000:
                raise ValueError('Manual query must be a nonempty short string')
            return [manual_query.strip()]
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Query limit must be 1..20')
        # Profile-specific templates (university/campus) are used only once the
        # owner has confirmed those facts; otherwise they are skipped.
        profile = self.profile()
        values = {'year': _now().year, 'university': profile.get('university'), 'campus': profile.get('campus')}
        templates = [template for template in QUERY_TEMPLATES[category]
                     if all(values.get(name) for _, name, _, _ in string.Formatter().parse(template) if name)]
        with self._db(write=True) as db:
            cursor = db.execute('SELECT query_cursor FROM preferences WHERE category=?', (category,)).fetchone()['query_cursor']
            count = min(limit, len(templates))
            queries = [templates[(cursor + index) % len(templates)].format(**values) for index in range(count)]
            db.execute('UPDATE preferences SET query_cursor=? WHERE category=?', ((cursor + count) % len(templates), category))
        return queries

    def _notification_hash(self, item, kind, now=None):
        payload = _meaningful_content(item)
        if kind == 'digest':
            payload['digest_date'] = (now or _now()).astimezone(MSK).date().isoformat()
        return _hash(payload)

    def notification_candidates(self, kind='new', now=None):
        if kind not in ('new', 'urgent', 'digest'):
            raise ValueError('Invalid notification kind')
        instant = _datetime(now) if now else _now()
        preferences = self.preferences()
        candidates = []
        for item in self.list(limit=1000):
            evaluation = evaluate(item, self.profile(), today=instant)
            if not preferences[item['category']]['enabled'] or evaluation['status'] == 'closed' or evaluation['eligibility'] == 'ineligible':
                continue
            if item['category'] in ('C', 'D') and evaluation['free_status'] != 'free':
                continue
            if item['category'] == 'B' and item.get('reward', {}).get('type') != 'salary':
                continue
            if kind == 'digest':
                day_start = datetime.combine(instant.astimezone(MSK).date(), time.min, MSK).astimezone(UTC)
                with self._db() as db:
                    latest = db.execute('SELECT MAX(changed_at) AS changed_at FROM history WHERE item_id=?', (item['id'],)).fetchone()['changed_at']
                if latest is None or _datetime(latest) < day_start:
                    continue
            if kind == 'urgent':
                deadline = _datetime(item['deadline'])
                if deadline is None or not timedelta(0) <= deadline - instant <= timedelta(hours=72):
                    continue
            fingerprint = self._notification_hash(item, kind, instant)
            with self._db() as db:
                event = db.execute('SELECT state FROM notifications WHERE item_id=? AND kind=? AND content_hash=?', (item['id'], kind, fingerprint)).fetchone()
            if event is None or event['state'] == 'failed':
                candidates.append(item)
        return candidates

    def queue_notification(self, item_id, kind='new'):
        if kind not in ('new', 'urgent', 'digest'):
            raise ValueError('Invalid notification kind')
        item = self.get(item_id)
        fingerprint = self._notification_hash(item, kind)
        now = _iso(_now())
        with self._db(write=True) as db:
            db.execute('INSERT OR IGNORE INTO notifications(id,item_id,kind,content_hash,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, item_id, kind, fingerprint, 'pending', now, now))
            row = db.execute('SELECT * FROM notifications WHERE item_id=? AND kind=? AND content_hash=?', (item_id, kind, fingerprint)).fetchone()
        # Return this exact detached snapshot, never reread after queuing: a
        # concurrent upsert must not turn an old message into a new-version ack.
        return {**dict(row), 'item': item}

    def mark_notification(self, event_id, state, message_id=None):
        if state not in ('sent', 'unknown', 'failed'):
            raise ValueError('Invalid notification state')
        with self._db(write=True) as db:
            row = db.execute('SELECT * FROM notifications WHERE id=?', (event_id,)).fetchone()
            if row is None:
                raise KeyError('Notification not found')
            if row['state'] in ('sent', 'unknown') and state != row['state']:
                raise ValueError('Terminal delivery state requires manual reconciliation')
            db.execute('UPDATE notifications SET state=?,message_id=?,updated_at=? WHERE id=?', (state, message_id, _iso(_now()), event_id))

    def claim_notification(self, event_id):
        """Claim once before network delivery. A crashed sending state never retries."""
        with self._db(write=True) as db:
            cursor = db.execute("UPDATE notifications SET state='sending',updated_at=? WHERE id=? AND state IN ('pending','failed')", (_iso(_now()), event_id))
            return cursor.rowcount == 1

    def stats(self):
        with self._db() as db:
            result = {table: db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in ('items', 'sources', 'history', 'changes', 'runs', 'notifications')}
            result['notification_states'] = {row['state']: row['n'] for row in db.execute('SELECT state,count(*) AS n FROM notifications GROUP BY state')}
            result['latest_runs'] = [dict(row) for row in db.execute('SELECT * FROM runs ORDER BY started_at DESC LIMIT 10')]
            result['latest_errors'] = [dict(row) for row in db.execute("SELECT * FROM runs WHERE error IS NOT NULL AND error != '' ORDER BY started_at DESC LIMIT 5")]
        return result
