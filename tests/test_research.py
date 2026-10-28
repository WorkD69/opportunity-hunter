import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opportunity_hunter.core import Store
from opportunity_hunter.research import ResearchEngine, FetchResult, PublicFetcher, _Text, validate_url


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'test.db')
        self.calls = []

    def native(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'web_search':
            return {'success': True, 'data': {'web': [
                {'url': 'https://new-source.example/intern', 'title': 'Paid Python intern'}]}}
        return {'results': [{'url': arguments['urls'][0], 'content':
                             'Official current internship. Paid Python work for students.'}]}

    def fetch(self, url, previous=None):
        return FetchResult(url, 'h1', False, 'text/html', 'etag1', '')

    def test_new_source_discovery_and_unchanged_page_skip_extraction(self):
        engine = ResearchEngine(self.store, self.native, fetcher=self.fetch)
        first = engine.collect('B', query='Python internship', max_queries=1, max_pages=1)
        self.assertEqual(len(first['pages']), 1)
        engine.acknowledge(first['pages'], 'B')
        self.assertTrue(any('new-source.example' in s['url'] for s in self.store.sources()))
        self.calls.clear()
        again = engine.collect('B', query='different query', max_queries=1, max_pages=1)
        self.assertEqual(again['pages'], [])
        self.assertEqual(len([x for x in self.calls if x[0] == 'web_extract']), 0)

    def test_interrupted_interpretation_reuses_cached_text_without_losing_page(self):
        engine = ResearchEngine(self.store, self.native, fetcher=self.fetch)
        first = engine.collect('B', query='Python internship', max_queries=1, max_pages=1)
        self.calls.clear()
        again = engine.collect('B', query='another search', max_queries=1, max_pages=1)
        self.assertEqual(again['pages'][0]['text'], first['pages'][0]['text'])
        self.assertTrue(again['pages'][0]['cached'])
        self.assertFalse(any(x[0] == 'web_extract' for x in self.calls))

    def test_daily_budget_is_shared_across_engine_instances(self):
        first = ResearchEngine(self.store, self.native, fetcher=self.fetch, query_budget=1)
        first.collect('B', query='first query', max_queries=1, max_pages=1)
        self.calls.clear()
        second = ResearchEngine(self.store, self.native, fetcher=self.fetch, query_budget=1)
        result = second.collect('A', query='new query', max_queries=1, max_pages=1)
        self.assertFalse(result['complete'])
        self.assertFalse(any(x[0] == 'web_search' for x in self.calls))

    def test_recheck_reads_exact_source_without_search_or_extraction_when_cached(self):
        engine = ResearchEngine(self.store, self.native, fetcher=self.fetch)
        first = engine.collect('B', query='Python internship', max_queries=1, max_pages=1)
        engine.acknowledge(first['pages'], 'B')
        self.calls.clear()
        again = engine.collect('B', targets=['https://new-source.example/intern'], force_review=True)
        self.assertEqual(again['pages'][0]['url'], 'https://new-source.example/intern')
        self.assertEqual(self.calls, [])

    def test_readable_live_html_avoids_paid_or_stale_provider_extraction(self):
        text = 'Applications close 18 October 2026. This is a current paid internship.'
        def live(url, previous=None):
            return FetchResult(url, 'live-hash', plain_text=text)
        engine = ResearchEngine(self.store, self.native, fetcher=live)
        bundle = engine.collect('B', targets=['https://employer.example/job'])
        self.assertEqual(bundle['pages'][0]['text'], text)
        self.assertEqual(self.calls, [])

    def test_long_page_cannot_be_acknowledged_before_remaining_text_is_read(self):
        text = 'Current official programs. ' * 700
        def live(url, previous=None):
            return FetchResult(url, 'long-hash', plain_text=text)
        engine = ResearchEngine(self.store, self.native, fetcher=live)
        bundle = engine.collect('A', targets=['https://university.example/programs'])
        page = bundle['pages'][0]
        self.assertEqual(page['next_offset'], 12000)
        with self.assertRaises(ValueError):
            engine.acknowledge(bundle['pages'], 'A')
        tail = engine.page_text(page['url'], 'A', offset=12000)
        self.assertIsNone(tail['next_offset'])
        engine.acknowledge(bundle['pages'], 'A')

    def test_failure_is_partial_not_no_matches(self):
        def broken(name, args):
            return {'success': False, 'error': 'quota exhausted'}
        result = ResearchEngine(self.store, broken, fetcher=self.fetch).collect('C', max_queries=1)
        self.assertFalse(result['complete'])
        self.assertTrue(result['errors'])

    def test_private_and_credential_urls_rejected(self):
        for url in ('http://127.0.0.1/a', 'http://169.254.169.254/', 'https://localhost/',
                    'https://user:pass@example.com/', 'file:///etc/passwd',
                    'https://example.com/?token=secret', 'https://example.com:8443/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_visible_links_participate_in_content_hash_input(self):
        parser = _Text('https://university.example/')
        parser.feed('<a href="/programs/2026">Apply</a>')
        self.assertIn('https://university.example/programs/2026', ' '.join(parser.parts))

    def test_robots_multihop_redirect_does_not_recursively_reload_robots(self):
        paths = []
        class Response:
            def __init__(self, path):
                self.path = path
                self.status = 302 if path in ('/robots.txt', '/robots') else 200
            def getheaders(self):
                return [('Location', '/robots' if self.path == '/robots.txt' else '/rules.txt')] if self.status == 302 else []
            def read(self, n):
                return b'User-agent: *\nAllow: /\n'
        class Connection:
            def __init__(self, *a, **kw): pass
            def request(self, method, path, **kwargs):
                paths.append(path)
                if len(paths) > 8:
                    raise RuntimeError('recursive robots fetch')
                self.path = path
            def getresponse(self): return Response(self.path)
            def close(self): pass
        with patch('opportunity_hunter.research.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]), \
             patch('opportunity_hunter.research.http.client.HTTPSConnection', Connection):
            PublicFetcher(delay=0)._check_robots('https://university.example/program')
        self.assertEqual(paths, ['/robots.txt', '/robots', '/rules.txt'])

    def test_store_requires_quotes_from_actual_extraction(self):
        engine = ResearchEngine(self.store, self.native, fetcher=self.fetch)
        engine.collect('B', query='internship', max_queries=1, max_pages=1)
        item = {'category': 'B', 'title': 'Intern', 'organizer': 'Example',
                'url': 'https://new-source.example/intern',
                'official_url': 'https://new-source.example/intern',
                'status': 'active', 'evidence': [{'url': 'https://new-source.example/intern',
                'quote': 'Invented salary of a million dollars'}]}
        with self.assertRaises(ValueError):
            engine.ingest(item)


if __name__ == '__main__':
    unittest.main()
