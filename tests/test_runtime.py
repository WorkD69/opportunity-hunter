import tempfile
import unittest
from pathlib import Path

from opportunity_hunter.runtime import Runtime, format_card


class RuntimeTests(unittest.TestCase):
    def test_health_is_readable_and_help_uses_single_word_commands(self):
        text=self.runtime.command('health')
        self.assertIn('Предложений:',text)
        self.assertNotIn('"items"',text)
        self.assertEqual(self.runtime.command(''),'Панель выше ⬆️')
        self.assertIn('inline_keyboard',self.sent[-1]['reply_markup'])
        self.assertTrue(self.sent[-1]['text'].startswith('🏠 Меню'))
        self.assertEqual(self.runtime.command('saved'),'Панель выше ⬆️')   # lists open as panels, no /oh_details text
        self.assertNotIn('/oh_details',self.sent[-1]['text'])
        self.assertIn('обычным текстом',self.runtime.command('help'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'opportunity-hunter'
        self.secrets = {'OH_OWNER_ID': '123', 'TELEGRAM_BOT_TOKEN': 'test-token'}
        self.session = {'HERMES_SESSION_PLATFORM': 'telegram', 'HERMES_SESSION_USER_ID': '123',
                        'HERMES_SESSION_CHAT_ID': '123'}
        self.sent = []
        self.runtime = Runtime(self.home, lambda *a: {}, self.secrets.get, self.session.get,
                               telegram_transport=self.transport)

    def transport(self, token, payload):
        self.sent.append(payload)
        return {'ok': True, 'result': {'message_id': 42}}

    def item(self):
        return self.runtime.store.upsert({'title': 'Paid Python intern', 'category': 'B',
                'organizer': 'Employer', 'url': 'https://employer.example/jobs/1',
                'official_url': 'https://employer.example/jobs/1', 'status': 'active',
                'reward': {'type': 'salary', 'amount': None},
                'evidence': [{'url': 'https://employer.example/jobs/1',
                              'quote': 'Paid internship open for students'}]})

    def test_profile_confirmation_only_trusted_owner_command(self):
        pending = self.runtime.handle({'action': 'profile_propose', 'patch': {'age': 20}})
        # The model never sees the token; only the owner's message carries it inside the ✅ button.
        self.assertTrue(pending['confirmation_sent_to_owner'])
        self.assertNotIn('token', pending)
        button = self.sent[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertTrue(button.startswith('oh:c:'))
        pending['token'] = button.split(':', 2)[2]
        self.assertNotIn(pending['token'], repr({k: v for k, v in pending.items() if k != 'token'}))
        with self.assertRaises(ValueError):
            self.runtime.handle({'action': 'profile_confirm', 'token': pending['token']})
        self.session['HERMES_SESSION_USER_ID'] = '456'
        with self.assertRaises(PermissionError):
            self.runtime.command('confirm ' + pending['token'])
        self.session['HERMES_SESSION_USER_ID'] = '123'
        self.runtime.command('confirm ' + pending['token'])
        self.assertEqual(self.runtime.store.profile()['age'], 20)

    def test_other_profile_is_rejected_before_creating_database(self):
        with self.assertRaises(PermissionError):
            Runtime(Path(self.tmp.name) / 'default', lambda *a: {}, self.secrets.get, self.session.get)
        self.assertFalse((Path(self.tmp.name) / 'default').exists())

    def test_delivery_deduplicates_and_fixed_chat_cannot_be_overridden(self):
        self.item()
        self.runtime.notify('new')
        self.runtime.notify('new')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['chat_id'], '123')
        buttons = self.sent[0]['reply_markup']['inline_keyboard']
        self.assertTrue(all(b['callback_data'].startswith('oh:a:') for row in buttons for b in row))
        self.assertEqual(self.runtime.store.stats()['notification_states'], {'sent': 1})

    def test_digest_is_one_summary_not_a_burst_of_cards(self):
        self.item()
        self.runtime.store.upsert({'title': 'Another paid intern', 'category': 'B',
            'organizer': 'Other', 'url': 'https://other.example/job', 'status': 'active',
            'official_url': 'https://other.example/job', 'reward': {'type': 'salary', 'amount': None},
            'evidence': [{'url': 'https://other.example/job', 'quote': 'Paid internship for students'}]})
        self.runtime.notify('digest')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Сводка', self.sent[0]['text'])
        self.runtime.notify('digest')
        self.assertEqual(len(self.sent), 1)

    def test_timeout_is_unknown_and_not_retried(self):
        self.item()
        def timeout(*args):
            raise TimeoutError('ambiguous network failure')
        self.runtime.telegram_transport = timeout
        self.runtime.notify('new')
        self.runtime.telegram_transport = self.transport
        self.runtime.notify('new')
        self.assertEqual(self.sent, [])

    def test_missing_token_prevents_send_without_consuming_candidate(self):
        self.item()
        self.secrets.pop('TELEGRAM_BOT_TOKEN')
        result = self.runtime.notify('new')
        self.assertEqual(result['state'], 'not_configured')
        self.secrets['TELEGRAM_BOT_TOKEN'] = 'test-token'
        self.runtime.notify('new')
        self.assertEqual(len(self.sent), 1)

    def test_no_application_capability(self):
        for action in ('apply', 'submit', 'trade', 'send_resume', 'email'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.runtime.handle({'action': action})

    def test_health_includes_storage_and_delivery_state(self):
        health = self.runtime.handle({'action': 'health'})
        self.assertGreater(health['database_bytes'], 0)
        self.assertTrue(health['telegram_configured'])

    def test_digest_covers_more_than_six_items(self):
        for number in range(7):
            self.runtime.store.upsert({'title': f'Paid intern {number}', 'category': 'B',
                'organizer': 'Employer', 'url': f'https://employer.example/jobs/{number}',
                'official_url': f'https://employer.example/jobs/{number}', 'status': 'active',
                'reward': {'type': 'salary', 'amount': None},
                'evidence': [{'url': f'https://employer.example/jobs/{number}',
                              'quote': 'Paid internship open for students'}]})
        self.runtime.notify('digest')
        self.assertEqual(len(self.runtime.store.notification_candidates('digest')), 0)

    def test_queued_fingerprint_and_transmitted_snapshot_are_identical(self):
        self.item()
        original = self.runtime.store.queue_notification
        def racing(*args, **kwargs):
            self.runtime.store.upsert({'title': 'Updated paid role', 'category': 'B',
                'organizer': 'Employer', 'url': 'https://employer.example/jobs/1',
                'official_url': 'https://employer.example/jobs/1', 'status': 'active',
                'reward': {'type': 'salary', 'amount': None},
                'evidence': [{'url': 'https://employer.example/jobs/1',
                              'quote': 'Paid internship open for students'}]})
            return original(*args, **kwargs)
        self.runtime.store.queue_notification = racing
        self.runtime.notify('new')
        self.assertIn('Updated paid role', self.sent[0]['text'])


if __name__ == '__main__':
    unittest.main()
