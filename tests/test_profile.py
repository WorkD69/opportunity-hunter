"""Stage 2: criteria fields, the city filter, resume_profile, proposals and the private import."""
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from opportunity_hunter import core
from opportunity_hunter.core import Store, evaluate, normalize_city, validate_resume

OFFICIAL = 'https://example.org/job'


def job(**location):
    return {'category': 'B', 'title': 'Python intern', 'organizer': 'Example', 'url': OFFICIAL,
            'official_url': OFFICIAL, 'status': 'active', 'deadline': '2099-12-31',
            'reward': {'type': 'salary', 'amount': '100000 RUB'},
            'requirements': [{'field': 'year', 'operator': 'min', 'value': 2,
                              'evidence_url': OFFICIAL, 'quote': 'Students from year two.'}],
            'evidence': [{'url': OFFICIAL, 'quote': 'Paid internship.', 'field': 'reward.type'}],
            'location': location}


PROFILE = {'year': 3, 'city': 'Нижний Новгород'}


def project(pid='prj-a', **extra):
    return {'id': pid, 'name': 'Project ' + pid, 'stack': ['Python'], 'start_year': 2026, 'source': 'chat', **extra}


class CityFilterTests(unittest.TestCase):
    def verdict(self, item, profile=PROFILE):
        return evaluate(item, profile)

    def test_same_city_passes_with_spelling_variants(self):
        self.assertEqual(normalize_city('Нижний  Новгород'), normalize_city('Nizhny Novgorod'))
        self.assertEqual(normalize_city('г. Москва'), normalize_city('Moscow'))
        result = self.verdict(job(cities=['Nizhny Novgorod'], remote=False))
        self.assertEqual(result['eligibility'], 'eligible')

    def test_other_city_without_remote_is_hidden_with_reason(self):
        result = self.verdict(job(cities=['Москва'], remote=False))
        self.assertEqual(result['eligibility'], 'ineligible')
        self.assertIn('other_city', result['flags'])
        self.assertTrue(any('Другой город' in r for r in result['reasons']))

    def test_other_city_remote_passes_but_hybrid_does_not(self):
        self.assertEqual(self.verdict(job(cities=['Москва'], remote=True))['eligibility'], 'eligible')
        hybrid = self.verdict(job(cities=['Москва'], remote=True, hybrid=True))
        self.assertEqual(hybrid['eligibility'], 'ineligible')

    def test_paid_relocation_needs_official_evidence(self):
        unproven = self.verdict(job(cities=['Москва'], remote=False, relocation_support=True))
        self.assertEqual(unproven['eligibility'], 'ineligible')
        item = job(cities=['Москва'], remote=False, relocation_support=True)
        item['evidence'].append({'url': OFFICIAL, 'quote': 'We pay for relocation and housing.',
                                 'field': 'location.relocation_support'})
        proven = self.verdict(item)
        self.assertEqual(proven['eligibility'], 'eligible')
        self.assertIn('relocation_support', proven['flags'])

    def test_unknown_remote_is_clarification_not_hidden(self):
        result = self.verdict(job(cities=['Москва']))
        self.assertEqual(result['eligibility'], 'needs_clarification')

    def test_no_city_in_profile_or_item_means_no_filter(self):
        self.assertEqual(self.verdict(job(cities=['Москва'], remote=False), {'year': 3})['eligibility'], 'eligible')
        self.assertEqual(self.verdict(job())['eligibility'], 'eligible')

    def test_rejected_listing_shows_hidden_items_with_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            store.confirm_profile(store.propose_profile({'year': 3, 'city': 'Нижний Новгород'})['token'])
            store.upsert(job(cities=['Москва'], remote=False))
            store.upsert({**job(cities=['Нижний Новгород'], remote=False), 'url': 'https://example.org/job2',
                          'title': 'Local intern'})
            rejected = store.rejected()
            self.assertEqual([i['title'] for i in rejected], ['Python intern'])
            self.assertTrue(rejected[0]['evaluation']['reasons'])

    def test_location_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            for bad in ({'cities': 'Москва'}, {'remote': 'yes'}, {'unknown': 1}, {'cities': ['']}):
                with self.assertRaises(ValueError):
                    store.upsert(job(**bad))


class ProfileMigrationTests(unittest.TestCase):
    def test_old_database_gains_new_fields_resume_table_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.sqlite'
            db = sqlite3.connect(path)
            try:
                db.executescript('''
                    CREATE TABLE profile(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
                    CREATE TABLE profile_proposals(token TEXT PRIMARY KEY, patch TEXT NOT NULL, base_version INTEGER NOT NULL, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);''')
                db.execute('INSERT INTO profile VALUES(1,4,?)', (json.dumps({'year': 3, 'skills': ['Python']}),))
                db.commit()
            finally:
                db.close()
            store = Store(path)
            profile = store.profile()
            self.assertEqual((profile['year'], profile['skills'], profile['version']), (3, ['Python'], 4))
            self.assertIsNone(profile['city'])
            self.assertEqual(store.resume_profile()['projects'], [])
            store.confirm_profile(store.propose_profile({'city': 'Нижний Новгород'})['token'])
            self.assertEqual(store.profile()['year'], 3)

    def test_new_criteria_fields_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            store.propose_profile({'available_from': '2027-06-01', 'min_salary': 50000, 'city': 'Казань'})
            for bad in ({'available_from': 'июнь'}, {'min_salary': '50к'}, {'city': 5}):
                with self.assertRaises(ValueError):
                    store.propose_profile(bad)


class ResumeValidationTests(unittest.TestCase):
    def test_valid_document_and_defaults(self):
        data = validate_resume({'projects': [project()], 'skills': [{'id': 'sk-py', 'name': 'Python', 'evidence': ['prj-a']}]})
        self.assertFalse(data['projects'][0]['confirmed'])
        self.assertEqual(data['education'], [])

    def test_rejects_string_years_unknown_fields_duplicates_and_dangling_evidence(self):
        for bad in ({'projects': [project(start_year='2025')]},
                    {'projects': [project(salary='1')]},
                    {'projects': [project(), project()]},
                    {'projects': [project(url='http://insecure.example')]},
                    {'skills': [{'id': 'sk', 'name': 'Python', 'evidence': ['prj-missing']}]},
                    {'projects': [project('Bad ID')]},
                    {'unknown_section': []}):
            with self.assertRaises(ValueError, msg=bad):
                validate_resume(bad)


class ResumeProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')
        self.store.import_master({'resume_profile': {
            'basics': {'name': 'Test Owner', 'email': 'owner@example.invalid', 'phone': '+7 000', 'github': 'https://github.com/x'},
            'projects': [project('prj-a'), project('prj-b')]}}, apply=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_replace_remove_confirm_and_owner_confirmation_marks_facts(self):
        token = self.store.propose_resume([
            {'op': 'add', 'section': 'projects', 'item': project('prj-c', confirmed=False)},
            {'op': 'replace', 'section': 'projects', 'item': project('prj-a', result='2nd place')},
            {'op': 'remove', 'section': 'projects', 'id': 'prj-b'}])['token']
        self.assertEqual(len(self.store.resume_profile()['projects']), 2)  # nothing applied before confirmation
        result = self.store.confirm_profile(token)
        projects = {p['id']: p for p in result['projects']}
        self.assertEqual(sorted(projects), ['prj-a', 'prj-c'])
        self.assertTrue(projects['prj-c']['confirmed'] and projects['prj-a']['confirmed'])
        self.assertEqual(result['scope'], 'resume')

    def test_invalid_operations_are_rejected_before_storing(self):
        for ops in ([{'op': 'remove', 'section': 'projects', 'id': 'prj-missing'}],
                    [{'op': 'add', 'section': 'projects', 'item': project('prj-a')}],
                    [{'op': 'replace', 'section': 'projects', 'item': project('prj-new')}],
                    [{'op': 'set_basics', 'field': 'phone', 'value': '+7 999'}],
                    [{'op': 'drop_table'}], []):
            with self.assertRaises(ValueError, msg=ops):
                self.store.propose_resume(ops)

    def test_confirmation_is_single_use_expiring_and_version_bound(self):
        first = self.store.propose_resume([{'op': 'confirm', 'section': 'projects', 'id': 'prj-a'}])['token']
        stale = self.store.propose_resume([{'op': 'confirm', 'section': 'projects', 'id': 'prj-b'}])['token']
        self.store.confirm_profile(first)
        for token in (first, stale):
            with self.assertRaises(ValueError):
                self.store.confirm_profile(token)
        late = self.store.propose_resume([{'op': 'confirm', 'section': 'projects', 'id': 'prj-b'}])['token']
        with patch.object(core, '_now', return_value=core._now() + timedelta(minutes=31)):
            with self.assertRaises(ValueError):
                self.store.confirm_profile(late)

    def test_layers_version_independently(self):
        criteria = self.store.propose_profile({'city': 'Нижний Новгород'})['token']
        self.store.confirm_profile(self.store.propose_resume([{'op': 'confirm', 'section': 'projects', 'id': 'prj-a'}])['token'])
        result = self.store.confirm_profile(criteria)  # a resume change does not stale a criteria proposal
        self.assertEqual((result['scope'], result['city']), ('criteria', 'Нижний Новгород'))
        self.assertNotIn('projects', result)

    def test_model_view_never_contains_contacts(self):
        view = json.dumps(self.store.resume_profile(), ensure_ascii=False)
        for secret in ('owner@example.invalid', '+7 000', 'Test Owner'):
            self.assertNotIn(secret, view)
        self.assertEqual(self.store.resume_profile()['basics']['github'], 'https://github.com/x')
        confirm = self.store.confirm_profile(self.store.propose_resume(
            [{'op': 'confirm', 'section': 'projects', 'id': 'prj-a'}])['token'])
        self.assertNotIn('owner@example.invalid', json.dumps(confirm, ensure_ascii=False))
        self.assertEqual(self.store.resume_profile(redact=False)['basics']['email'], 'owner@example.invalid')

    def test_profile_missing_lists_unknown_criteria_and_unconfirmed_facts(self):
        missing = self.store.profile_missing()
        self.assertIn('city', missing['criteria_unknown'])
        self.assertEqual({e['id'] for e in missing['resume_unconfirmed']}, {'prj-a', 'prj-b'})
        self.assertEqual(sorted(missing['contacts_on_file']), ['email', 'name', 'phone'])


class ImportTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        master = {'criteria': {'year': 3, 'city': 'Нижний Новгород', 'location_rules': {'ignored': True}},
                  'resume_profile': {'projects': [project()]},
                  'extra_sources': [{'category': 'A', 'url': 'https://example.edu/grants', 'title': 'Grants', 'label': 'official'}]}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            dry = store.import_master(master)
            self.assertEqual((dry['criteria_changes'], dry['resume_changed'], dry['sources_new']), (['city', 'year'], True, 1))
            self.assertIsNone(store.profile()['city'])
            self.assertEqual(store.sources(), [])
            store.import_master(master, apply=True)
            self.assertEqual(store.profile()['city'], 'Нижний Новгород')
            self.assertEqual(len(store.sources()), 1)
            again = store.import_master(master, apply=True)
            self.assertEqual((again['criteria_changes'], again['resume_changed'], again['sources_new']), ([], False, 0))

    def test_invalid_master_is_rejected_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            with self.assertRaises(ValueError):
                store.import_master({'criteria': {'city': 'Нижний Новгород'},
                                     'resume_profile': {'projects': [project(start_year='2025')]}}, apply=True)
            self.assertIsNone(store.profile()['city'])


if __name__ == '__main__':
    unittest.main()
