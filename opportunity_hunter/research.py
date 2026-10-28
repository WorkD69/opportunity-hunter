"""Bounded research orchestration; search/extraction stay owned by Hermes."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .core import Store, canonical_url

USER_AGENT = 'OpportunityHunter/0.1 (personal opportunity research; no form submission)'


def validate_url(url: str, resolve: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValueError('Invalid URL')
    p = urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Only public HTTP(S) URLs without credentials are allowed')
    if p.port not in (None, 80 if p.scheme == 'http' else 443):
        raise ValueError('Nonstandard port')
    host = p.hostname.lower().rstrip('.')
    if '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
        raise ValueError('Private hostname')
    if re.search(r'(?:^|&)(?:token|key|api_key|password|secret|access_token|auth|signature)=', p.query, re.I):
        raise ValueError('URL may contain a secret')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError('Private IP')
    except ValueError as exc:
        if str(exc) == 'Private IP':
            raise
    if resolve:
        addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Private or mixed DNS resolution')
    return canonical_url(url)


@dataclass
class FetchResult:
    url: str
    content_hash: str
    not_modified: bool = False
    content_type: str = ''
    etag: str = ''
    last_modified: str = ''
    plain_text: str = ''
    truncated: bool = False


class _Text(HTMLParser):
    def __init__(self, base_url=''):
        super().__init__()
        self.hidden = 0
        self.parts = []
        self.base_url = base_url

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'a' and not self.hidden:
            href = dict(attrs).get('href')
            if href:
                try:
                    self.parts.append(' [link: ' + validate_url(urljoin(self.base_url, href)) + '] ')
                except ValueError:
                    pass

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class PublicFetcher:
    """GET only; public IP pinning, redirect checks, robots and bounded reads."""
    def __init__(self, timeout=8, max_bytes=2_000_000, delay=1.0):
        self.timeout, self.max_bytes, self.delay = timeout, max_bytes, delay
        self.robots = {}
        self.last_request = {}

    def _get(self, url, headers=None, redirects=3, robots=False):
        url = validate_url(url, resolve=True)
        p = urlsplit(url)
        host, port = p.hostname, p.port or (443 if p.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Private DNS address')
        pinned_ip = addresses[0][4][0]
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_request[host] = time.monotonic()
        cls = http.client.HTTPSConnection if p.scheme == 'https' else http.client.HTTPConnection
        conn = cls(host, port, timeout=self.timeout)
        # Pin transport to the validated IP while HTTPS still verifies/SNIs hostname.
        conn._create_connection = lambda address, timeout, source_address=None: socket.create_connection(
            (pinned_ip, port), timeout, source_address)
        try:
            path = quote(p.path or '/', safe='/%:@!$&\'()*+,;=-._~')
            conn.request('GET', path + ('?' + quote(p.query, safe='=&%:@!$\'()*+,;/?-._~') if p.query else ''), headers={
                'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', **(headers or {})})
            response = conn.getresponse()
            h = {k.lower(): v for k, v in response.getheaders()}
            if response.status in (301, 302, 303, 307, 308):
                if not redirects or not h.get('location'):
                    raise ValueError('Redirect limit')
                target = validate_url(urljoin(url, h['location']), resolve=True)
                # Every new host/path gets its own robots decision, including redirects.
                if not robots:
                    self._check_robots(target)
                return self._get(target, None, redirects - 1, robots=robots)
            length = h.get('content-length', '')
            if length.isdigit() and int(length) > self.max_bytes:
                raise ValueError('Page exceeds size limit')
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise ValueError('Page exceeds size limit')
            return url, response.status, h, body
        finally:
            conn.close()

    def _check_robots(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        cached = self.robots.get(origin)
        if not cached or time.monotonic() - cached[0] > 86400:
            robots_url = origin + '/robots.txt'
            _, status, _, body = self._get(robots_url, redirects=2, robots=True)
            parser = RobotFileParser(robots_url)
            if status in (401, 403):
                parser.disallow_all = True
            elif status == 404:
                parser.allow_all = True
            elif 200 <= status < 300:
                parser.parse(body.decode('utf-8', errors='replace').splitlines())
            else:
                raise ValueError(f'robots.txt unavailable ({status}); defer site')
            self.robots[origin] = (time.monotonic(), parser)
        parser = self.robots[origin][1]
        if not parser.can_fetch(USER_AGENT, url):
            raise ValueError('robots.txt disallows fetching')
        crawl_delay = parser.crawl_delay(USER_AGENT) or 0
        if crawl_delay > 10:
            raise ValueError('Site crawl delay exceeds research budget; defer site')
        self.delay = max(self.delay, crawl_delay)

    def __call__(self, url, previous=None):
        self._check_robots(url)
        headers = {}
        if previous:
            if previous.get('etag'):
                headers['If-None-Match'] = previous['etag']
            if previous.get('last_modified'):
                headers['If-Modified-Since'] = previous['last_modified']
        final, status, h, body = self._get(url, headers)
        if status == 304 and previous:
            return FetchResult(final, previous['content_hash'], True,
                               previous.get('content_type', ''), previous.get('etag', ''),
                               previous.get('last_modified', ''))
        if status != 200:
            raise ValueError(f'HTTP {status}; no bypass attempted')
        content_type = h.get('content-type', '').split(';')[0]
        if content_type not in ('text/html', 'text/plain', 'application/json', 'application/pdf', 'application/xhtml+xml'):
            raise ValueError('Unsupported content type')
        hashed = body
        plain_text = ''
        if content_type in ('text/html', 'application/xhtml+xml'):
            parser = _Text(final)
            charset = re.search(r'charset=([a-zA-Z0-9_-]+)', h.get('content-type', ''))
            encoding = charset.group(1) if charset else 'utf-8'
            try:
                decoded = body.decode(encoding, errors='replace')
            except LookupError:
                decoded = body.decode('utf-8', errors='replace')
            parser.feed(decoded)
            plain_text = ' '.join(' '.join(parser.parts).split())
            hashed = plain_text.encode()
        elif content_type == 'text/plain':
            plain_text = body.decode('utf-8', errors='replace')
        digest = hashlib.sha256(hashed).hexdigest()
        return FetchResult(final, digest, bool(previous and digest == previous['content_hash']),
                           content_type, h.get('etag', ''), h.get('last-modified', ''),
                           plain_text[:60000], len(plain_text) > 60000)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _norm(text):
    return ' '.join(str(text).split()).casefold()


class ResearchEngine:
    def __init__(self, store: Store, native_call, fetcher=None, db_path=None, query_budget=12, extract_budget=16):
        self.store, self.native_call = store, native_call
        self.fetcher = fetcher or PublicFetcher()
        self.query_budget, self.extract_budget = query_budget, extract_budget
        self.path = str(db_path or store.path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS page_cache (url TEXT PRIMARY KEY, content_hash TEXT, '
                       'text TEXT, content_type TEXT, etag TEXT, last_modified TEXT, checked_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS page_reviews (url TEXT, category TEXT, content_hash TEXT, '
                       'PRIMARY KEY(url,category))')
            db.execute('CREATE TABLE IF NOT EXISTS research_usage (day TEXT,kind TEXT,n INTEGER,PRIMARY KEY(day,kind))')
            db.execute('CREATE TABLE IF NOT EXISTS search_cache (query TEXT PRIMARY KEY, result TEXT, checked REAL)')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(page_cache)')}
            if 'truncated' not in columns:
                db.execute('ALTER TABLE page_cache ADD COLUMN truncated INTEGER DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS page_views (url TEXT,category TEXT,content_hash TEXT,'
                       'start_offset INTEGER,end_offset INTEGER,PRIMARY KEY(url,category,content_hash,start_offset))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _claim_budget(self, kind, limit):
        day = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO research_usage VALUES (?,?,0)', (day, kind))
            changed = db.execute('UPDATE research_usage SET n=n+1 WHERE day=? AND kind=? AND n<?',
                                 (day, kind, limit)).rowcount
        if not changed:
            raise ValueError('Daily ' + kind + ' budget exhausted; no paid fallback')

    def acknowledge(self, pages, category):
        if category not in 'ABCD' or len(category) != 1:
            raise ValueError('Invalid category')
        with self._db() as db:
            for page in pages:
                url = validate_url(page['url'])
                row = db.execute('SELECT content_hash,length(text) AS n,truncated FROM page_cache WHERE url=?', (url,)).fetchone()
                if not row or row['content_hash'] != page.get('content_hash'):
                    raise ValueError('Cannot acknowledge a missing or changed extraction')
                views = db.execute('SELECT start_offset,end_offset FROM page_views WHERE url=? AND category=? AND content_hash=? ORDER BY start_offset',
                                   (url, category, row['content_hash'])).fetchall()
                covered = 0
                for view in views:
                    if view['start_offset'] > covered:
                        break
                    covered = max(covered, view['end_offset'])
                if row['truncated'] or covered < row['n']:
                    raise ValueError('Page is incomplete: read remaining page_text chunks; oversized sources cannot be acknowledged')
                db.execute('INSERT OR REPLACE INTO page_reviews VALUES(?,?,?)', (url, category, row['content_hash']))
        return {'acknowledged': len(pages), 'category': category}

    def page_text(self, url, category, offset=0):
        url = validate_url(url)
        if category not in ('A', 'B', 'C', 'D') or type(offset) is not int or offset < 0:
            raise ValueError('Invalid category or page offset')
        with self._db() as db:
            row = db.execute('SELECT * FROM page_cache WHERE url=?', (url,)).fetchone()
            if not row or offset >= len(row['text']):
                raise ValueError('No cached text at this offset')
            end = min(offset + 12000, len(row['text']))
            db.execute('INSERT OR REPLACE INTO page_views VALUES(?,?,?,?,?)',
                       (url, category, row['content_hash'], offset, end))
        return {'url': url, 'category': category, 'content_hash': row['content_hash'],
                'text': row['text'][offset:end], 'offset': offset, 'total_chars': len(row['text']),
                'next_offset': end if end < len(row['text']) else None,
                'cache_truncated': bool(row['truncated']), 'untrusted_content': True}

    def collect(self, category, query=None, max_queries=2, max_pages=4, targets=None, force_review=False):
        max_queries, max_pages = max(1, min(3, max_queries)), max(1, min(6, max_pages))
        run_id = self.store.begin_run(category)
        started = time.monotonic()
        out = {'run_id': run_id, 'category': category, 'queries': [], 'discovered': [],
               'pages': [], 'unchanged': [], 'errors': [], 'complete': True}
        try:
            urls = []
            queries = [] if targets else self.store.plan_queries(category, manual_query=query, limit=max_queries)
            for q in queries:
                out['queries'].append(q)
                try:
                    with self._db() as db:
                        cached = db.execute('SELECT result,checked FROM search_cache WHERE query=?', (q,)).fetchone()
                    if cached and time.time() - cached['checked'] < 3600:
                        data = json.loads(cached['result'])
                    else:
                        self._claim_budget('search', self.query_budget)
                        data = _json(self.native_call('web_search', {'query': q, 'limit': 5}))
                        if data.get('success'):
                            with self._db() as db:
                                db.execute('INSERT OR REPLACE INTO search_cache VALUES(?,?,?)',
                                           (q, json.dumps(data), time.time()))
                    if not data.get('success'):
                        raise ValueError(data.get('error', 'Search failed'))
                    for result in data.get('data', {}).get('web', []):
                        try:
                            url = validate_url(result['url'])
                            previous_source = next((s for s in self.store.sources() if s['url'] == url), {})
                            self.store.add_source({'url': url, 'category': category,
                                                   'name': previous_source.get('name') or result.get('title', ''),
                                                   'trust': previous_source.get('trust', 'discovered')})
                            out['discovered'].append(url)
                            if url not in urls:
                                urls.append(url)
                        except (ValueError, KeyError):
                            continue
                except Exception as exc:
                    out['errors'].append({'operation': 'search', 'error': str(exc)[:300]})
            # Reserve half the page budget for discovered sources; known sources cannot starve discovery.
            known = [s['url'] for s in self.store.due_sources(category, limit=max_pages)]
            first = max(1, max_pages // 2)
            selected = list(dict.fromkeys(urls[:first] + known[:max_pages-first] + urls[first:]))[:max_pages]
            if targets:
                selected = [validate_url(u) for u in targets[:max_pages]]
                for url in selected:
                    previous_source = next((s for s in self.store.sources() if s['url'] == url), {})
                    self.store.add_source({**previous_source, 'url': url, 'category': category})
            for url in selected:
                if time.monotonic() - started > 150:
                    out['errors'].append({'operation': 'budget', 'error': 'Run time budget reached; remaining pages deferred'})
                    break
                try:
                    with self._db() as db:
                        row = db.execute('SELECT * FROM page_cache WHERE url=?', (url,)).fetchone()
                    old = dict(row) if row else None
                    fetched = self.fetcher(url, previous=old)
                    if old and old.get('text') and (fetched.not_modified or fetched.content_hash == old['content_hash']):
                        self.store.record_source(url, fetched.content_hash, category=category)
                        with self._db() as db:
                            reviewed = db.execute('SELECT content_hash FROM page_reviews WHERE url=? AND category=?',
                                                  (url, category)).fetchone()
                            db.execute('UPDATE page_cache SET checked_at=? WHERE url=?',
                                       (datetime.now(timezone.utc).isoformat(), url))
                        if reviewed and reviewed['content_hash'] == fetched.content_hash and not force_review:
                            out['unchanged'].append(url)
                        else:
                            out['pages'].append({**self.page_text(url, category), 'final_url': fetched.url,
                                'checked_at': datetime.now(timezone.utc).isoformat(), 'cached': True})
                        continue
                    text = fetched.plain_text if len(fetched.plain_text.strip()) >= 60 else ''
                    if not text:
                        self._claim_budget('extract', self.extract_budget)
                        data = _json(self.native_call('web_extract', {'urls': [url], 'char_limit': 60000}))
                        results = data.get('results', [])
                        for entry in results:
                            if entry.get('error'):
                                continue
                            text = entry.get('content') or entry.get('markdown') or entry.get('text') or ''
                            if text:
                                break
                    if not isinstance(text, str) or len(text.strip()) < 30:
                        raise ValueError('No extractable text; source remains unverified')
                    checked = datetime.now(timezone.utc).isoformat()
                    with self._db() as db:
                        db.execute('INSERT OR REPLACE INTO page_cache VALUES (?,?,?,?,?,?,?,?)',
                                   (url, fetched.content_hash, text[:60000], fetched.content_type,
                                    fetched.etag, fetched.last_modified, checked,
                                    int(fetched.truncated or len(text) > 60000)))
                    self.store.record_source(url, fetched.content_hash, category=category)
                    out['pages'].append({**self.page_text(url, category), 'final_url': fetched.url, 'checked_at': checked})
                except Exception as exc:
                    self.store.record_source(url, '', error=str(exc)[:300], category=category)
                    out['errors'].append({'url': url, 'operation': 'extract', 'error': str(exc)[:300]})
            out['complete'] = not out['errors']
            return out
        finally:
            self.store.end_run(run_id, error=json.dumps(out['errors'], ensure_ascii=False) if out['errors'] else None)

    def ingest(self, item):
        item = json.loads(json.dumps(item))
        official = validate_url(item.get('official_url', ''))
        evidence = item.get('evidence') or []
        if not evidence:
            raise ValueError('An extracted primary source and exact evidence quotes are required')
        for ev in evidence + item.get('requirements', []):
            url = validate_url(ev.get('evidence_url') or ev.get('url') or '')
            quote = ev.get('quote')
            with self._db() as db:
                row = db.execute('SELECT text,checked_at FROM page_cache WHERE url=?', (url,)).fetchone()
            if not row or not quote or len(quote) < 8 or _norm(quote) not in _norm(row['text']):
                raise ValueError('Evidence quote is absent from extracted page: ' + url)
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row['checked_at'])
            if age.days > 7:
                raise ValueError('Evidence is stale; recheck source first')
        if not any(canonical_url(ev['url']) == official for ev in evidence):
            raise ValueError('Primary source has not been extracted')
        # Important amounts/dates/cost assertions need a field-specific citation.
        fields = {e.get('field') for e in evidence if canonical_url(e['url']) == official}
        if (item.get('reward') or {}).get('amount') is not None and 'reward.amount' not in fields:
            raise ValueError('Published amount requires field-specific primary evidence')
        amount = (item.get('reward') or {}).get('amount')
        if amount is not None:
            quotes = ' '.join(e['quote'] for e in evidence if e.get('field') == 'reward.amount' and canonical_url(e['url']) == official)
            for number in re.findall(r'\d[\d\s\u00a0\u202f]*', str(amount)):
                normalized = re.sub(r'\s', '', number)
                if normalized not in re.sub(r'\s', '', quotes):
                    raise ValueError('Published amount contains unsupported digits')
        if item.get('category') == 'B' and (item.get('reward') or {}).get('type') == 'salary' and 'reward.type' not in fields:
            raise ValueError('Paid-work classification requires primary compensation evidence')
        if item.get('deadline') and 'deadline' not in fields:
            raise ValueError('Deadline requires field-specific primary evidence')
        if item.get('category') == 'C':
            for key, value in item.get('cost_flags', {}).items():
                if value is not None and 'cost_flags.' + key not in fields:
                    raise ValueError('Cost assertion requires primary evidence: ' + key)
        item['verified_at'] = datetime.now(timezone.utc).isoformat()
        return self.store.upsert(item)
