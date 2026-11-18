import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opportunity_hunter.core import PROFILE_SEED, Store, evaluate, canonical_url


def opportunity(**updates):
    item = {
        'category': 'A', 'title': 'Student scholarship', 'organizer': 'Foundation',
        'url': 'https://example.org/program?utm_source=feed',
        'official_url': 'https://example.org/program', 'round': '2027',
        'deadline': '2027-12-31', 'status': 'active',
        'verified_at': '2026-10-17T10:00:00Z',
        'reward': {'type': 'personal_scholarship', 'amount': None},
        'requirements': [{'field': 'year', 'operator': 'min', 'value': 2,
                          'evidence_url': 'https://example.org/program', 'quote': 'Students from year two may apply.'}],
        'evidence': [{'url': 'https://example.org/program', 'quote': 'Applications open for 2027. No entry fee, no deposit, no real trading and no paid subscription.'}],
        'cost_flags': {key: False for key in ('entry_fee', 'deposit', 'real_trading', 'paid_subscription')},
        'next_actions': ['Read application'],
    }
    item.update(updates)
    return item


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'hunter.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_profile_preserves_unknown_facts(self):
        profile = self.store.profile()
        self.assertIsNone(profile['year'])
        self.assertIsNone(profile['citizenship'])
        self.assertIsNone(profile['residence'])
        self.assertIsNone(profile['age'])
        self.assertIsNone(profile['quant_experience'])

    def test_profile_confirmation_is_explicit_single_use_and_versioned(self):
        first = self.store.propose_profile({'citizenship': 'RU'})
        stale = self.store.propose_profile({'age': 21})
        self.assertIsNone(self.store.profile()['citizenship'])
        self.assertEqual(self.store.confirm_profile(first['token'])['citizenship'], 'RU')
        with self.assertRaises(ValueError):
            self.store.confirm_profile(stale['token'])
        with self.assertRaises(ValueError):
            self.store.confirm_profile(first['token'])
        for patch in ({'admin': True}, {'education.year': 4}, {'year': -1}, {'citizenship': {'trusted': True}}):
            with self.assertRaises(ValueError):
                self.store.propose_profile(patch)

    def test_known_mismatch_wins_over_missing_criteria(self):
        item = opportunity(requirements=[
            {'field': 'citizenship', 'operator': 'eq', 'value': 'RU', 'quote': 'Russian citizens only', 'evidence_url': 'https://example.org/program'},
            {'field': 'year', 'operator': 'max', 'value': 2, 'quote': 'Years one or two', 'evidence_url': 'https://example.org/program'},
        ])
        self.assertEqual(evaluate(item, {**self.store.profile(), 'year': 3})['eligibility'], 'ineligible')

    def test_unknown_geography_and_unquoted_claims_need_clarification(self):
        item = opportunity(requirements=[{'field': 'citizenship', 'operator': 'in', 'value': ['RU', 'BY'],
                                            'quote': 'RU and BY citizens', 'evidence_url': 'https://example.org/program'}])
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'needs_clarification')
        item['requirements'] = [{'field': 'year', 'operator': 'min', 'value': 2}]
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'needs_clarification')
        item['official_url'] = None
        item['requirements'][0]['quote'] = 'Year two and above'
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'needs_clarification')

    def test_supplemental_unknown_requirements_are_preserved_for_clarification(self):
        criterion = {'field': 'team_size', 'operator': 'min', 'value': 3,
                     'evidence_url': 'https://example.org/program', 'quote': 'Teams of at least three people.'}
        item = self.store.upsert(opportunity(requirements=[criterion]))
        self.assertEqual(item['requirements'][0], criterion)
        self.assertEqual(item['evaluation']['eligibility'], 'needs_clarification')
        self.assertIsNone(self.store.profile().get('team_size'))
        with self.assertRaises(ValueError):
            self.store.propose_profile({'team_size': 4})
        for invalid in ({'field': 'profile.admin', 'operator': 'eq', 'value': True},
                        {'field': 'arbitrary', 'operator': 'eq', 'value': {'nested': 'object'}},
                        {'field': 'arbitrary', 'operator': 'min', 'value': True}):
            with self.assertRaises(ValueError):
                self.store.upsert(opportunity(requirements=[invalid]))

    def test_additional_profile_constraints_are_unknown_until_typed_confirmation(self):
        for field in ('english_level', 'work_authorization', 'weekly_hours', 'full_time', 'relocation', 'team_available', 'kyc_willingness'):
            self.assertIn(field, self.store.profile())
            self.assertIsNone(self.store.profile()[field])
        proposal = self.store.propose_profile({'weekly_hours': 20, 'full_time': False, 'team_available': True})
        profile = self.store.confirm_profile(proposal['token'])
        self.assertEqual(profile['weekly_hours'], 20)
        self.assertIs(profile['full_time'], False)
        for patch in ({'weekly_hours': 169}, {'weekly_hours': True}, {'full_time': 'yes'}, {'relocation': 1}):
            with self.assertRaises(ValueError):
                self.store.propose_profile(patch)

    def test_expired_and_date_only_moscow_deadlines(self):
        item = opportunity(deadline='2026-10-17')
        self.assertEqual(evaluate(item, self.store.profile(), today='2026-10-17T20:59:59Z')['status'], 'active')
        self.assertEqual(evaluate(item, self.store.profile(), today='2026-10-17T21:00:00Z')['status'], 'closed')
        item['deadline'] = None
        self.assertEqual(evaluate(item, self.store.profile())['status'], 'active')

    def test_crypto_cost_must_be_explicit_and_supported(self):
        item = opportunity(category='C')
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'free')
        item['cost_flags']['deposit'] = None
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'unverified')
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'needs_clarification')
        item['cost_flags']['deposit'] = True
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'not_free')
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'ineligible')
        item = opportunity(category='C', evidence=[])
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'unverified')

    def test_cost_claims_require_cost_specific_official_quotes(self):
        item = opportunity(category='C', evidence=[{'url': 'https://example.org/program', 'quote': 'Applications open'}])
        result = evaluate(item, self.store.profile())
        self.assertEqual(result['free_status'], 'unverified')
        self.assertEqual(result['eligibility'], 'needs_clarification')
        item['evidence'] = [{'url': 'https://example.org/program', 'quote': 'This is free.', 'field': key} for key in ('entry_fee', 'deposit', 'real_trading', 'paid_subscription')]
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'unverified')

    def test_crypto_without_known_geographic_criteria_needs_clarification(self):
        self.assertEqual(evaluate(opportunity(category='C'), self.store.profile())['eligibility'], 'needs_clarification')

    def test_expired_profile_proposal_cannot_be_confirmed(self):
        import sqlite3
        proposal = self.store.propose_profile({'age': 21})
        with sqlite3.connect(self.store.path) as db:
            db.execute('UPDATE profile_proposals SET expires_at=? WHERE token=?', ('2000-01-01T00:00:00Z', proposal['token']))
        db.close()
        with self.assertRaises(ValueError):
            self.store.confirm_profile(proposal['token'])

    def test_malformed_hostnames_are_rejected(self):
        for url in ('https://127.1/x', 'https://2130706433/x', 'https://0x7f000001/x', 'https://bad_host.example/x', 'https://example.org\\@evil.org/x'):
            with self.assertRaises(ValueError, msg=url):
                canonical_url(url)

    def test_canonical_urls_preserve_job_ids_and_reject_private_targets(self):
        self.assertEqual(canonical_url('https://EXAMPLE.org/jobs?id=17&utm_source=feed#x'), 'https://example.org/jobs?id=17')
        for url in ('http://localhost/a', 'http://127.0.0.1/x', 'http://[::1]/x', 'http://10.2.3.4/x', 'file:///etc/passwd', 'https://user:pass@example.org'):
            with self.assertRaises(ValueError):
                self.store.upsert(opportunity(url=url))

    def test_duplicate_alias_versions_and_bookmark_preservation(self):
        first = self.store.upsert(opportunity())
        self.store.action(first['id'], 'save')
        self.store.action(first['id'], 'hide')
        same = self.store.upsert(opportunity(url='https://example.org/program?utm_campaign=x', verified_at='2026-10-18T12:00:00Z'))
        self.assertEqual(first['id'], same['id'])
        self.assertEqual(first['version'], same['version'])
        self.assertTrue(same['bookmarked'])
        self.assertTrue(same['hidden'])
        changed = self.store.upsert(opportunity(deadline='2027-11-30'))
        self.assertEqual(changed['version'], first['version'] + 1)
        self.assertEqual(len(self.store.list(bookmarked=True, include_hidden=True)), 1)
        self.assertEqual(self.store.list(), [])
        self.store.action(first['id'], 'unhide')
        self.assertEqual(len(self.store.list()), 1)
        separate = self.store.upsert(opportunity(round='2028'))
        self.assertNotEqual(first['id'], separate['id'])

    def test_organizer_external_id_alias_does_not_merge_title_only(self):
        first = self.store.upsert(opportunity(external_id='job-42'))
        alias = self.store.upsert(opportunity(external_id='job-42', url='https://jobs.example.org/42', official_url='https://jobs.example.org/42'))
        self.assertEqual(first['id'], alias['id'])
        other = self.store.upsert(opportunity(url='https://example.org/other', official_url='https://example.org/other'))
        self.assertNotEqual(first['id'], other['id'])

    def test_shared_official_rules_url_does_not_merge_distinct_postings(self):
        first = self.store.upsert(opportunity(url='https://example.org/posting/1', official_url='https://example.org/rules'))
        second = self.store.upsert(opportunity(url='https://example.org/posting/2', official_url='https://example.org/rules'))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.stats()['items'], 2)

    def test_explicit_official_posting_identity_deduplicates_aggregators(self):
        first = self.store.upsert(opportunity(url='https://aggregator.example.org/post/1', identity_url='https://example.org/posting/1'))
        second = self.store.upsert(opportunity(url='https://other.example.org/listing/10', identity_url='https://example.org/posting/1'))
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['version'], second['version'])
        with self.assertRaises(ValueError):
            self.store.upsert(opportunity(identity_url='http://127.0.0.1/identity'))

    def test_explicit_posting_identity_dominates_shared_discovery_page(self):
        board = 'https://board.example.org/employer'
        first = self.store.upsert(opportunity(url=board, identity_url='https://example.org/jobs/1'))
        second = self.store.upsert(opportunity(url=board, identity_url='https://example.org/jobs/2'))
        self.assertNotEqual(first['id'], second['id'])
        first_with_id = self.store.upsert(opportunity(url=board, identity_url='https://example.org/jobs/3', external_id='3'))
        second_with_id = self.store.upsert(opportunity(url=board, identity_url='https://example.org/jobs/4', external_id='4'))
        self.assertNotEqual(first_with_id['id'], second_with_id['id'])

    def test_distinct_external_ids_never_merge_through_shared_board_page(self):
        board = 'https://board.example.org/employer'
        first = self.store.upsert(opportunity(url=board, external_id='posting-one'))
        second = self.store.upsert(opportunity(url=board, external_id='posting-two'))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.get(first['id'])['external_id'], 'posting-one')
        self.assertEqual(self.store.get(second['id'])['external_id'], 'posting-two')

    def test_same_posting_rejects_conflicting_external_identity(self):
        first = self.store.upsert(opportunity(identity_url='https://example.org/jobs/1', external_id='one'))
        with self.assertRaises(ValueError):
            self.store.upsert(opportunity(identity_url='https://example.org/jobs/1', external_id='two'))
        self.assertEqual(self.store.get(first['id'])['external_id'], 'one')
        again = self.store.upsert(opportunity(url='https://aggregator.example.org/job',
                                            identity_url='https://example.org/jobs/1'))
        self.assertEqual(first['id'], again['id'])

    def test_identity_enrichment_preserves_record_and_delivery_history(self):
        first = self.store.upsert(opportunity())
        event = self.store.queue_notification(first['id'])
        self.store.mark_notification(event['id'], 'sent', 1)
        enriched = self.store.upsert(opportunity(external_id='one', identity_url='https://example.org/jobs/1'))
        self.assertEqual(first['id'], enriched['id'])
        self.assertEqual(first['version'], enriched['version'])
        self.assertEqual(self.store.notification_candidates(), [])
        second = self.store.upsert(opportunity(external_id='two', identity_url='https://example.org/jobs/2'))
        self.assertNotEqual(first['id'], second['id'])

    def test_date_only_deadline_remains_urgent_until_end_of_moscow_day(self):
        now = datetime.now(timezone.utc)
        today_moscow = now.astimezone(timezone(timedelta(hours=3))).date().isoformat()
        item = self.store.upsert(opportunity(deadline=today_moscow))
        self.assertIn(item['id'], [i['id'] for i in self.store.notification_candidates(kind='urgent', now=now)])

    def test_free_education_does_not_require_irrelevant_trading_assertions(self):
        item = self.store.upsert(opportunity(category='D', reward={'type': 'career', 'amount': None},
            cost_flags={'entry_fee': False}, evidence=[{'url': 'https://example.org/program',
            'quote': 'Обучение бесплатное. Проезд участники оплачивают самостоятельно.', 'field': 'cost_flags.entry_fee'}]))
        self.assertEqual(item['evaluation']['free_status'], 'free')
        self.assertIn(item['id'], [i['id'] for i in self.store.notification_candidates()])
        no_charge = self.store.upsert(opportunity(category='D', cost_flags={'entry_fee': False},
            evidence=[{'url': 'https://example.org/program', 'quote': 'Плата за обучение на программе не взимается.'}]))
        self.assertEqual(no_charge['evaluation']['free_status'], 'free')

    def test_recent_items_filter_is_distinct_from_upcoming_deadlines(self):
        item = self.store.upsert(opportunity())
        with self.store._db(write=True) as db:
            db.execute('UPDATE items SET created_at=? WHERE id=?', ('2020-01-01T00:00:00Z', item['id']))
        self.store.upsert(opportunity(verified_at='2026-10-17T12:00:00Z'))
        self.assertEqual(self.store.list(recent_days=3), [])
        fresh = self.store.upsert(opportunity(url='https://example.org/new', external_id='new'))
        self.assertEqual([i['id'] for i in self.store.list(recent_days=3)], [fresh['id']])

    def test_frequency_bounds_are_enforced_below_tool_schema(self):
        for value in (1, 721, float('nan')):
            with self.assertRaises(ValueError):
                self.store.preferences('B', interval_hours=value)

    def test_qualitative_value_and_effort_rank_without_selection_odds(self):
        base = self.store.upsert(opportunity(assessment={'benefit': 'high', 'registration_effort': 'low',
            'workload': 'unknown', 'study_compatibility': 'unknown', 'long_term_value': 'high',
            'rationale': 'Agent estimate based on the published task; personal availability is unknown.'}))
        costly = self.store.upsert(opportunity(url='https://example.org/long',
            assessment={'benefit': 'low', 'registration_effort': 'high'}))
        self.assertGreater(base['evaluation']['rank'], costly['evaluation']['rank'])
        self.assertEqual(base['assessment']['study_compatibility'], 'unknown')
        with self.assertRaises(ValueError):
            self.store.upsert(opportunity(assessment={'win_probability': 0.7}))

    def test_health_stats_include_recent_run_failures(self):
        run = self.store.begin_run('A')
        self.store.end_run(run, error='Source quota exhausted')
        stats = self.store.stats()
        self.assertEqual(stats['latest_runs'][0]['id'], run)
        self.assertEqual(stats['latest_errors'][0]['error'], 'Source quota exhausted')

    def test_scan_run_lease_serializes_and_releases(self):
        run = self.store.begin_run('A')
        with self.assertRaises(RuntimeError):
            Store(self.store.path).begin_run('B')
        self.store.end_run(run)
        self.store.end_run(self.store.begin_run('B'), error='network unavailable')

    def test_preferences_sources_and_persisted_query_rotation(self):
        self.assertTrue(self.store.preferences('A')['enabled'])
        self.assertFalse(self.store.preferences('A', enabled=False)['enabled'])
        with self.assertRaises(ValueError):
            self.store.preferences('A', interval_hours=0)
        self.store.add_source({'url': 'https://example.org/calls?utm_source=x', 'category': 'A', 'title': 'Official calls'})
        self.assertEqual(len(self.store.due_sources('A')), 1)
        self.store.record_source('https://example.org/calls', 'abc')
        self.assertEqual(self.store.due_sources('A'), [])
        one = self.store.plan_queries('B', limit=2)
        two = Store(self.store.path).plan_queries('B', limit=2)
        self.assertNotEqual(one, two)
        self.assertTrue(any('hh.ru' in q or 'Habr' in q for q in one + two))
        self.assertEqual(self.store.plan_queries('A', manual_query='custom query'), ['custom query'])

    def test_public_seed_contains_no_personal_values(self):
        self.assertTrue(all(value is None for value in PROFILE_SEED.values()))

    def test_university_query_only_after_confirmed_profile_facts(self):
        queries = self.store.plan_queries('A', limit=20)
        self.assertFalse(any('None' in q or '{' in q for q in queries))
        self.assertFalse(any('стипендии студентам' in q for q in queries))
        proposal = self.store.propose_profile({'university': 'Example University', 'campus': 'Example City'})
        self.store.confirm_profile(proposal['token'])
        queries = Store(self.store.path).plan_queries('A', limit=20)
        self.assertTrue(any('Example University Example City' in q for q in queries))

    def test_query_categories_and_confirmed_skills_match_user_profile(self):
        profile = self.store.profile()
        self.assertIsNone(profile['skills'])
        self.assertIsNone(profile['commercial_experience'])
        self.assertIsNone(profile['english_level'])
        self.assertTrue(any('хакатон' in q for q in self.store.plan_queries('A', limit=20)))
        self.assertTrue(any('paper' in q or 'demo' in q for q in self.store.plan_queries('C', limit=20)))
        self.assertTrue(any('бесплат' in q or 'free' in q for q in self.store.plan_queries('D', limit=20)))

    def test_nested_cost_field_annotation_and_bool_numeric_validation(self):
        item = opportunity(category='C', evidence=[{'url': 'https://example.org/program', 'quote': 'No ' + key.replace('_', ' ') + ' required.', 'field': 'cost_flags.' + key} for key in ('entry_fee', 'deposit', 'real_trading', 'paid_subscription')])
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'free')
        for field in ('age', 'gpa', 'year', 'work_experience'):
            with self.assertRaises(ValueError):
                self.store.propose_profile({field: True})

    def test_timezone_is_required_and_unknown_deadline_preserved(self):
        with self.assertRaises(ValueError):
            self.store.upsert(opportunity(deadline='2027-10-01T15:00:00'))
        item = self.store.upsert(opportunity(deadline=None))
        self.assertIsNone(item['deadline'])

    def test_delivery_unknown_never_retries_and_failed_can_retry_same_event(self):
        item = self.store.upsert(opportunity())
        event = self.store.queue_notification(item['id'])
        self.assertEqual(event['id'], self.store.queue_notification(item['id'])['id'])
        self.store.mark_notification(event['id'], 'unknown')
        self.assertEqual(self.store.notification_candidates(), [])
        event = self.store.queue_notification(item['id'])
        self.assertEqual(event['state'], 'unknown')
        changed = self.store.upsert(opportunity(deadline='2027-11-30'))
        self.assertEqual(len(self.store.notification_candidates()), 1)
        next_event = self.store.queue_notification(changed['id'])
        self.assertNotEqual(event['id'], next_event['id'])
        self.store.mark_notification(next_event['id'], 'failed')
        self.assertEqual(len(self.store.notification_candidates()), 1)
        self.assertEqual(self.store.queue_notification(item['id'])['id'], next_event['id'])
        self.store.mark_notification(next_event['id'], 'sent', message_id='123')
        self.assertEqual(self.store.notification_candidates(), [])
        self.assertEqual(self.store.stats()['items'], 1)

    def test_salary_unknown_not_invented_and_interest_not_experience(self):
        item = opportunity(category='B', reward={'type': 'salary', 'amount': None}, requirements=[
            {'field': 'quant_experience', 'operator': 'min', 'value': 1, 'quote': 'One year quant experience', 'evidence_url': 'https://example.org/program'}])
        result = self.store.upsert(item)
        self.assertIsNone(result['reward']['amount'])
        self.assertEqual(result['evaluation']['eligibility'], 'needs_clarification')
        self.assertIsInstance(result['evaluation']['rank'], (float, int))
        self.assertIn('utility', result['evaluation']['rank_description'])

    def test_d_requires_free_program_and_b_requires_paid_employment(self):
        paid_course = opportunity(category='D')
        paid_course['cost_flags']['paid_subscription'] = True
        self.assertEqual(evaluate(paid_course, self.store.profile())['eligibility'], 'ineligible')
        unknown_course = opportunity(category='D', cost_flags={})
        self.assertEqual(evaluate(unknown_course, self.store.profile())['eligibility'], 'needs_clarification')
        career = self.store.upsert(opportunity(category='B', reward={'type': 'career', 'amount': None}))
        self.assertEqual(career['evaluation']['eligibility'], 'needs_clarification')
        self.assertNotIn(career['id'], [i['id'] for i in self.store.notification_candidates()])
        unpaid = opportunity(category='B', reward={'type': 'salary', 'amount': 'unpaid'})
        self.assertEqual(evaluate(unpaid, self.store.profile())['eligibility'], 'ineligible')

    def test_uncited_mismatch_cannot_reject_user(self):
        item = opportunity(requirements=[{'field': 'year', 'operator': 'max', 'value': 1}])
        result = evaluate(item, self.store.profile())
        self.assertEqual(result['eligibility'], 'needs_clarification')
        item['requirements'][0].update(quote='Only first year students.', evidence_url='https://unofficial.example.org/rules')
        self.assertEqual(evaluate(item, self.store.profile())['eligibility'], 'needs_clarification')

    def test_cost_tag_cannot_override_contradictory_quote(self):
        item = opportunity(category='C')
        item['evidence'] = [{'url': 'https://example.org/program', 'field': 'cost_flags.' + key,
                             'quote': 'Participation is free but a deposit is required.'} for key in ('entry_fee', 'deposit', 'real_trading', 'paid_subscription')]
        self.assertEqual(evaluate(item, self.store.profile())['free_status'], 'unverified')

    def test_source_associations_preserve_multiple_categories(self):
        self.store.add_source({'url': 'https://example.org/shared', 'category': 'A'})
        self.store.add_source({'url': 'https://example.org/shared', 'category': 'D'})
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(set(self.store.sources()[0]['categories']), {'A', 'D'})
        self.assertEqual(len(self.store.due_sources('A')), 1)
        self.assertEqual(len(self.store.due_sources('D')), 1)

    def test_discovery_alias_change_is_not_meaningful_content_change(self):
        item = self.store.upsert(opportunity(external_id='42'))
        event = self.store.queue_notification(item['id'])
        self.store.mark_notification(event['id'], 'sent')
        alias = self.store.upsert(opportunity(external_id='42', url='https://example.org/alias', official_url='https://jobs.example.org/42'))
        self.assertEqual(item['version'], alias['version'])
        self.assertEqual(self.store.notification_candidates(), [])
        self.assertEqual(event['id'], self.store.queue_notification(item['id'])['id'])

    def test_alias_citation_and_quote_refresh_keeps_semantic_fingerprint(self):
        item = self.store.upsert(opportunity(external_id='stable-42'))
        event = self.store.queue_notification(item['id'])
        self.store.mark_notification(event['id'], 'sent')
        changed = opportunity(external_id='stable-42', url='https://jobs.example.org/new', official_url='https://jobs.example.org/new')
        changed['evidence'] = [{'url': 'https://jobs.example.org/new', 'quote': 'Updated official wording: no entry fee, no deposit, no real trading, no paid subscription.'}]
        changed['requirements'][0].update(evidence_url='https://jobs.example.org/new', quote='Students in the second year or later are eligible.')
        refreshed = self.store.upsert(changed)
        self.assertEqual(item['version'], refreshed['version'])
        self.assertEqual(self.store.notification_candidates(), [])
        changed['requirements'][0]['value'] = 4
        self.assertEqual(self.store.upsert(changed)['version'], item['version'] + 1)

    def test_shared_source_check_is_per_category_with_error_backoff(self):
        url = 'https://example.org/shared'
        for category in ('A', 'D'):
            self.store.add_source({'url': url, 'category': category})
        self.store.record_source(url, 'content-hash', category='A')
        self.assertEqual(self.store.due_sources('A'), [])
        self.assertEqual(len(self.store.due_sources('D')), 1)
        self.store.record_source(url, 'content-hash', category='D', error='extract failed')
        self.assertEqual(self.store.due_sources('D'), [])
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        self.store.record_source(url, 'content-hash', checked_at=old, category='D', error='extract failed')
        self.assertEqual(len(self.store.due_sources('D')), 1)
        self.assertEqual(self.store.due_sources('A'), [])

    def test_notification_claim_is_atomic_and_success_is_recorded_sent(self):
        from concurrent.futures import ThreadPoolExecutor
        item = self.store.upsert(opportunity())
        event = self.store.queue_notification(item['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: Store(self.store.path).claim_notification(event['id']), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.queue_notification(item['id'])['state'], 'sending')
        self.assertEqual(self.store.notification_candidates(), [])
        self.store.mark_notification(event['id'], 'sent', 'delivered-123')
        self.assertEqual(self.store.queue_notification(item['id'])['state'], 'sent')
        self.assertEqual(self.store.queue_notification(item['id'])['message_id'], 'delivered-123')

    def test_queued_notification_returns_the_snapshot_used_for_its_fingerprint(self):
        from unittest.mock import patch
        item = self.store.upsert(opportunity(title='Original title'))
        original_hash = self.store._notification_hash

        def concurrent_update(snapshot, kind, now=None):
            fingerprint = original_hash(snapshot, kind, now)
            self.store.upsert(opportunity(title='Updated during queue operation'))
            return fingerprint

        with patch.object(self.store, '_notification_hash', side_effect=concurrent_update):
            event = self.store.queue_notification(item['id'])
        self.assertEqual(event['item']['title'], 'Original title')
        self.assertEqual(event['content_hash'], original_hash(event['item'], 'new'))
        self.assertEqual(self.store.get(item['id'])['title'], 'Updated during queue operation')
        self.assertTrue(self.store.claim_notification(event['id']))
        self.store.mark_notification(event['id'], 'sent', 'snapshot-message')
        # The concurrent update still needs its own delivery after the old snapshot.
        self.assertEqual(len(self.store.notification_candidates()), 1)

    def test_digest_skips_old_unchanged_items(self):
        import sqlite3
        item = self.store.upsert(opportunity())
        with sqlite3.connect(self.store.path) as db:
            db.execute('UPDATE history SET changed_at=?', ('2000-01-01T00:00:00Z',))
        db.close()
        self.store.upsert(opportunity(verified_at='2026-10-18T10:00:00Z'))
        self.assertEqual(self.store.notification_candidates('digest'), [])
        self.store.upsert(opportunity(deadline='2027-11-30'))
        self.assertEqual(len(self.store.notification_candidates('digest')), 1)

    def test_due_respects_defaults_disabled_preferences_and_failure_backoff(self):
        self.assertEqual({k: v['interval_hours'] for k, v in self.store.preferences().items()}, {'A': 24, 'B': 12, 'C': 24, 'D': 48})
        self.assertTrue(self.store.is_due('B'))
        run = self.store.begin_run('B')
        self.assertFalse(self.store.is_due('B'))
        self.store.end_run(run)
        now = datetime.now(timezone.utc)
        self.assertFalse(self.store.is_due('B', now=now + timedelta(hours=11)))
        self.assertTrue(self.store.is_due('B', now=now + timedelta(hours=13)))
        self.store.preferences('B', interval_hours=24)
        self.assertFalse(self.store.is_due('B', now=now + timedelta(hours=13)))
        self.store.preferences('B', enabled=False)
        self.assertFalse(self.store.is_due('B', now=now + timedelta(days=100)))
        self.store.end_run(self.store.begin_run('A'), error='network')
        self.assertFalse(self.store.is_due('A', now=now + timedelta(hours=3)))
        self.assertTrue(self.store.is_due('A', now=now + timedelta(hours=5)))


class CriteriaComparisonTests(unittest.TestCase):
    """Free-text values from sources must not cause false rejections."""
    OWNER = {**PROFILE_SEED, 'year': 3, 'citizenship': 'RU', 'english_level': 'B1–B2', 'gpa': 7.5}

    @staticmethod
    def verdict(field, operator, value, profile=None):
        item = opportunity(requirements=[{'field': field, 'operator': operator, 'value': value,
                                          'evidence_url': 'https://example.org/program', 'quote': 'Official rule.'}])
        return evaluate(item, profile or CriteriaComparisonTests.OWNER)['eligibility']

    def test_citizenship_spellings_compare_as_one_country(self):
        for value in ('РФ', 'Россия', 'Russian Federation', 'ru', ['Россия', 'Беларусь']):
            self.assertEqual(self.verdict('citizenship', 'in' if isinstance(value, list) else 'eq', value), 'eligible', value)
        self.assertEqual(self.verdict('citizenship', 'eq', 'Казахстан'), 'ineligible')
        self.assertEqual(self.verdict('citizenship', 'eq', 'RU', {**self.OWNER, 'citizenship': 'Россия'}), 'eligible')

    def test_english_uses_cefr_order_and_a_range_is_borderline_not_rejected(self):
        self.assertEqual(self.verdict('english_level', 'min', 'B1'), 'eligible')
        self.assertEqual(self.verdict('english_level', 'min', 'Intermediate'), 'eligible')
        self.assertEqual(self.verdict('english_level', 'min', 'B2'), 'needs_clarification')
        self.assertEqual(self.verdict('english_level', 'min', 'Upper-Intermediate'), 'needs_clarification')
        self.assertEqual(self.verdict('english_level', 'min', 'С1'), 'ineligible')  # Cyrillic С
        self.assertEqual(self.verdict('english_level', 'eq', 'fluent'), 'ineligible')
        self.assertEqual(self.verdict('english_level', 'min', 'good English'), 'needs_clarification')

    def test_gpa_is_soft_and_scale_aware(self):
        self.assertEqual(self.verdict('gpa', 'min', 8), 'needs_clarification')    # below: never a reject
        self.assertEqual(self.verdict('gpa', 'min', 4.5), 'needs_clarification')  # 5-point scale: not comparable
        self.assertEqual(self.verdict('gpa', 'min', 6.5), 'eligible')
        self.assertEqual(self.verdict('year', 'min', 4), 'ineligible')            # hard criteria stay hard


if __name__ == '__main__':
    unittest.main()
