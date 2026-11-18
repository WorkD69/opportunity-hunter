"""Inline buttons: only the owner in the private chat can press; every payload fits Telegram limits."""
import tempfile
import unittest
from pathlib import Path

from opportunity_hunter import buttons
from opportunity_hunter.runtime import Runtime

OWNER = '123'


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = []
        secrets = {'OH_OWNER_ID': OWNER, 'TELEGRAM_BOT_TOKEN': 'test-token'}
        self.runtime = Runtime(Path(self.tmp.name) / 'opportunity-hunter', lambda *a: {}, secrets.get,
                               {}.get, telegram_transport=lambda token, payload: self.sent.append(payload) or
                               {'ok': True, 'result': {'message_id': 1}})
        self.item = self.runtime.store.upsert({
            'title': 'Paid Python intern', 'category': 'B', 'organizer': 'Employer',
            'url': 'https://employer.example/jobs/1', 'official_url': 'https://employer.example/jobs/1',
            'status': 'active', 'reward': {'type': 'salary', 'amount': None},
            'evidence': [{'url': 'https://employer.example/jobs/1', 'quote': 'Paid internship'}]})

    def press(self, data, user=OWNER, chat=OWNER):
        return buttons.dispatch(self.runtime, data, user, chat)

    def offer(self, patch):
        self.runtime.handle({'action': 'profile_propose', 'patch': patch})
        return self.sent[-1]['reply_markup']['inline_keyboard'][0]

    def test_all_callback_data_fits_64_bytes_and_parses(self):
        token_button = self.offer({'city': 'Нижний Новгород'})
        layouts = [buttons.menu_markup(), buttons.card_markup(self.item['id']),
                   {'inline_keyboard': [token_button]}]
        for markup in layouts:
            for row in markup['inline_keyboard']:
                for button in row:
                    self.assertLessEqual(len(button['callback_data'].encode()), 64)
                    self.assertIsNotNone(buttons.parse(button['callback_data']), button)

    def test_foreign_user_or_group_chat_cannot_press(self):
        confirm = self.offer({'city': 'Москва'})[0]['callback_data']
        self.assertIsNone(self.press(confirm, user='999'))
        self.assertIsNone(self.press(confirm, chat='-100500'))
        self.assertIsNone(self.press(f"oh:a:hide:{self.item['id']}", user='999'))
        self.assertIsNone(self.runtime.store.profile()['city'])
        self.assertFalse(self.runtime.store.get(self.item['id'])['hidden'])

    def test_confirm_applies_once_and_cancel_discards(self):
        confirm, _ = (b['callback_data'] for b in self.offer({'city': 'Нижний Новгород'}))
        first = self.press(confirm)
        self.assertIn('Применено', first['text'])
        self.assertEqual(self.runtime.store.profile()['city'], 'Нижний Новгород')
        version = self.runtime.store.profile()['version']
        second = self.press(confirm)
        self.assertIsNone(second['text'])
        self.assertEqual(self.runtime.store.profile()['version'], version)
        _, cancel = (b['callback_data'] for b in self.offer({'city': 'Москва'}))
        self.assertIn('отменена', self.press(cancel)['text'])
        self.assertEqual(self.runtime.store.profile()['city'], 'Нижний Новгород')

    def test_card_actions(self):
        item_id = self.item['id']
        self.assertEqual(self.press(f'oh:a:save:{item_id}')['toast'], 'Сохранено ⭐')
        self.assertTrue(self.runtime.store.get(item_id)['bookmarked'])
        details = self.press(f'oh:a:details:{item_id}')
        self.assertIn('Paid Python intern', details['text'])
        self.assertIn('unsave', repr(details['markup']))
        self.press(f'oh:a:hide:{item_id}')
        self.assertTrue(self.runtime.store.get(item_id)['hidden'])

    def test_menu_buttons_work_without_hermes_session(self):
        for row in buttons.MENU:
            for _, data in row:
                result = self.press(data)
                self.assertIsNotNone(result, data)
                self.assertTrue(result['text'], data)

    def test_foreign_or_malformed_payloads_change_nothing_and_get_an_alert(self):
        for data in ('update_prompt:y', 'oh:', 'oh:a:drop:' + self.item['id'], 'oh:a:save:../../etc',
                     'oh:m:shell', 'oh:c:x', None, 'oh:c:' + 'A' * 70, 'oh:k:B:5', 'oh:k::on', 'oh:rc:q:x',
                     'oh:l:s:100', 'oh:i:' + self.item['id'] + ':z1', 'oh:c:' + 'A' * 43 + ':q'):
            self.assertIsNone(buttons.parse(data), data)
            result = self.press(data)
            self.assertTrue(result['alert'], data)
            self.assertIsNone(result['mode'], data)
            self.assertIsNone(self.press(data, user='999'), data)
        self.assertFalse(self.runtime.store.get(self.item['id'])['bookmarked'])
        self.assertEqual(self.runtime.store.preferences('B')['interval_hours'], 12)

    def test_profile_view_never_prints_contacts(self):
        self.runtime.store.import_master({'resume_profile': {'basics': {
            'name': 'Owner Name', 'email': 'owner@example.invalid', 'phone': '+7 000 000'}}}, apply=True)
        text = self.press('oh:m:profile')['text']
        for secret in ('Owner Name', 'owner@example.invalid', '+7 000 000'):
            self.assertNotIn(secret, text)
        self.assertIn('сохранены', text)

    def test_resume_proposal_offer_is_readable(self):
        self.runtime.handle({'action': 'resume_propose', 'ops': [
            {'op': 'add', 'section': 'skills', 'item': {'id': 'sk-docker', 'name': 'Docker'}}]})
        self.assertIn('Добавить в «Навыки (с доказательствами)»: Docker', self.sent[-1]['text'])


    # ── security review regressions ─────────────────────────────────────────────────────────────
    def test_owner_sees_every_field_including_a_late_url(self):
        item = {'id': 'prj-x', 'name': 'X', 'role': 'r', 'stack': ['a'], 'result': 'r', 'summary_ru': 's',
                'start_year': 2026, 'end_year': 2026, 'team': True, 'url': 'https://evil.example/phish',
                'source': 'web'}
        self.runtime.handle({'action': 'resume_propose', 'ops': [{'op': 'add', 'section': 'projects', 'item': item}]})
        text = self.sent[-1]['text']
        for expected in ('https://evil.example/phish', 'source: web', 'id prj-x'):
            self.assertIn(expected, text)

    def test_oversized_proposal_is_refused_and_never_stored(self):
        huge = ['x' * 400] * 12
        with self.assertRaises(ValueError):
            self.runtime.handle({'action': 'profile_propose', 'patch': {'skills': huge}})
        self.assertEqual(self.runtime.store.pending_proposals(), [])
        with self.assertRaises(ValueError):
            self.runtime.handle({'action': 'resume_propose', 'ops': [
                {'op': 'add', 'section': 'skills', 'item': {'id': f'sk-{i}', 'name': 'n'}} for i in range(11)]})

    def test_remove_and_replace_show_current_content(self):
        self.runtime.store.import_master({'resume_profile': {'projects': [
            {'id': 'prj-a', 'name': 'Real project', 'result': 'old result'}]}}, apply=True)
        self.runtime.handle({'action': 'resume_propose', 'ops': [
            {'op': 'replace', 'section': 'projects', 'item': {'id': 'prj-a', 'name': 'Real project', 'result': 'new result'}}]})
        replace_text = self.sent[-1]['text']
        self.assertIn('new result', replace_text)
        self.assertIn('было:', replace_text)
        self.assertIn('old result', replace_text)
        self.runtime.handle({'action': 'resume_propose', 'ops': [{'op': 'remove', 'section': 'projects', 'id': 'prj-a'}]})
        self.assertIn('Real project', self.sent[-1]['text'])

    def test_token_never_reaches_the_model_even_when_delivery_fails(self):
        self.runtime.telegram_transport = lambda token, payload: {'ok': False}
        result = self.runtime.handle({'action': 'profile_propose', 'patch': {'city': 'Казань'}})
        pending = self.runtime.store.pending_proposals()
        self.assertEqual(len(pending), 1)
        self.assertNotIn(pending[0]['token'], repr(result))
        self.assertIn('/oh_pending', result['instruction'])
        self.runtime.telegram_transport = lambda token, payload: self.sent.append(payload) or {'ok': True}
        self.assertIn('отправлено с кнопками: 1', self.runtime.run_command('pending'))
        self.assertEqual(self.sent[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'],
                         'oh:c:' + pending[0]['token'])

    def test_at_most_three_pending_proposals(self):
        for city in ('А', 'Б', 'В'):
            self.runtime.handle({'action': 'profile_propose', 'patch': {'city': city}})
        with self.assertRaises(ValueError):
            self.runtime.handle({'action': 'profile_propose', 'patch': {'city': 'Г'}})

    def test_basics_proposals_are_validated(self):
        with self.assertRaises(ValueError):
            self.runtime.store.propose_resume([{'op': 'set_basics', 'field': 'github', 'value': 'javascript:alert(1)'}])
        with self.assertRaises(ValueError):
            self.runtime.store.propose_resume([{'op': 'add', 'section': 'skills', 'item': ['not', 'a', 'dict']}])

    def test_import_validates_sources_before_writing_anything(self):
        with self.assertRaises(ValueError):
            self.runtime.store.import_master({'criteria': {'city': 'Казань'}, 'extra_sources': [
                {'category': 'A', 'url': 'https://example.edu/a'}, {'category': 'Z', 'url': 'https://example.edu/b'}]}, apply=True)
        self.assertIsNone(self.runtime.store.profile()['city'])
        self.assertEqual(self.runtime.store.sources(), [])

    # ── navigation v2: one panel edited in place, back/menu everywhere ─────────────────────────────
    def add_items(self, count, saved=True):
        ids = []
        for n in range(count):
            item = self.runtime.store.upsert({
                'title': f'Offer {n:02d}', 'category': 'B', 'organizer': 'Employer',
                'url': f'https://employer.example/jobs/{n + 100}', 'official_url': f'https://employer.example/jobs/{n + 100}',
                'status': 'active', 'reward': {'type': 'salary', 'amount': None},
                'evidence': [{'url': f'https://employer.example/jobs/{n + 100}', 'quote': 'Paid internship'}]})
            if saved:
                self.runtime.store.action(item['id'], 'save')
            ids.append(item['id'])
        return ids

    @staticmethod
    def callbacks(result):
        return [b['callback_data'] for row in (result['markup'] or {}).get('inline_keyboard', []) for b in row]

    def import_resume(self):
        self.runtime.store.import_master({'resume_profile': {
            'projects': [{'id': 'prj-a', 'name': 'Project A', 'result': 'shipped'},
                         {'id': 'prj-b', 'name': 'Project B', 'confirmed': True}],
            'languages': [{'id': 'lang-en', 'language': 'English', 'level': 'B1–B2'}]}}, apply=True)

    def test_every_reachable_screen_edits_in_place_has_menu_and_valid_callbacks(self):
        self.add_items(7)
        self.import_resume()
        self.runtime.handle({'action': 'profile_propose', 'patch': {'city': 'Нижний Новгород'}})
        mutating = ('oh:a:', 'oh:c:', 'oh:x:', 'oh:rc:', 'oh:k:A:', 'oh:k:B:', 'oh:k:C:', 'oh:k:D:')
        seen, queue = set(), [buttons.MENU_DATA]
        while queue:
            data = queue.pop()
            if data in seen:
                continue
            seen.add(data)
            result = self.press(data)
            self.assertIsNotNone(buttons.parse(data), data)
            self.assertLessEqual(len(data.encode()), 64, data)
            if data == 'oh:z':
                continue
            self.assertEqual(result['mode'], 'edit', data)
            self.assertLessEqual(len(result['text']), 4000, data)
            found = self.callbacks(result)
            if data != buttons.MENU_DATA:
                self.assertIn(buttons.MENU_DATA, found, data)  # never a dead end
            queue += [c for c in found if not c.startswith(mutating)]
        self.assertGreater(len(seen), 15)
        self.assertFalse(any('/oh_details' in self.press(d)['text'] for d in seen if d != 'oh:z'))

    def test_list_pagination_and_back_to_the_same_page(self):
        ids = self.add_items(12)
        first = self.press('oh:l:s:0')
        self.assertIn('oh:l:s:1', self.callbacks(first))
        self.assertNotIn('◀️', first['markup']['inline_keyboard'][-2][0]['text'])
        last = self.press('oh:l:s:2')
        self.assertIn('oh:l:s:1', self.callbacks(last))
        self.assertFalse(any(c == 'oh:l:s:3' for c in self.callbacks(last)))
        self.assertIn('стр. 3/3', last['text'])
        self.assertIn('стр. 3/3', self.press('oh:l:s:9')['text'])  # stale page → last page
        middle = self.press('oh:l:s:1')
        opened = next(c for c in self.callbacks(middle) if c.startswith('oh:i:'))
        card = self.press(opened)
        self.assertIn('oh:l:s:1', self.callbacks(card))
        self.assertEqual(card['markup']['inline_keyboard'][-1][0]['text'], '⬅️ К списку')
        self.assertTrue(set(ids) >= {opened.split(':')[2]})

    def test_actions_inside_a_panel_rerender_the_card_and_on_a_card_change_only_buttons(self):
        item_id = self.item['id']
        in_panel = self.press(f'oh:a:save:{item_id}:s0')
        self.assertEqual((in_panel['mode'], in_panel['toast']), ('edit', 'Сохранено ⭐'))
        self.assertIn(f'oh:a:unsave:{item_id}:s0', self.callbacks(in_panel))
        self.assertIn('⭐ в избранном', in_panel['text'])
        on_card = self.press(f'oh:a:hide:{item_id}:c')
        self.assertEqual(on_card['mode'], 'keyboard')
        self.assertIn(f'oh:a:unhide:{item_id}:c', self.callbacks(on_card))
        self.assertTrue(self.runtime.store.get(item_id)['hidden'])
        self.press(f'oh:a:unhide:{item_id}:c')
        self.assertFalse(self.runtime.store.get(item_id)['hidden'])
        recheck = self.press(f'oh:a:recheck:{item_id}:c')
        self.assertTrue(recheck['alert'])
        self.assertLessEqual(len(recheck['toast']), 200)

    def test_legacy_buttons_from_old_messages_still_work(self):
        self.assertEqual(self.press('oh:m:saved')['mode'], 'edit')
        self.assertIn('Категории', self.press('oh:m:categories')['text'])
        self.assertEqual(self.press(f"oh:a:save:{self.item['id']}")['mode'], 'keyboard')
        self.assertIn('Paid Python intern', self.press(f"oh:a:details:{self.item['id']}")['text'])

    def test_confirm_turns_the_message_into_the_result(self):
        confirm, _ = (b['callback_data'] for b in self.offer({'city': 'Нижний Новгород'}))
        done = self.press(confirm)
        self.assertEqual((done['mode'], done['markup']), ('edit', None))  # standalone: buttons disappear
        stale = self.press(confirm)
        self.assertEqual((stale['mode'], stale['markup'], stale['alert']), ('keyboard', None, True))
        self.runtime.handle({'action': 'profile_propose', 'patch': {'english_level': 'B2'}})
        pending = self.press('oh:m:pend')
        view = self.press(next(c for c in self.callbacks(pending) if c.startswith('oh:p:')))
        self.assertIn('Английский: B2', view['text'])
        panel_confirm = next(c for c in self.callbacks(view) if c.startswith('oh:c:'))
        self.assertTrue(panel_confirm.endswith(':p'))
        applied = self.press(panel_confirm)
        self.assertIn(buttons.MENU_DATA, self.callbacks(applied))  # in a panel: navigation stays
        self.assertEqual(self.runtime.store.profile()['english_level'], 'B2')

    def test_categories_toggle_and_frequency(self):
        self.press('oh:k:B:off')
        self.assertFalse(self.runtime.store.preferences('B')['enabled'])
        screen = self.press('oh:k:B:72')
        self.assertEqual(self.runtime.store.preferences('B')['interval_hours'], 72)
        self.assertIn('• 3 дня', repr(screen['markup']))
        self.assertIn('oh:k:B:on', self.callbacks(screen))
        self.assertIn('oh:m:cats', self.callbacks(screen))

    def test_owner_confirms_one_resume_fact_by_button(self):
        self.import_resume()
        entry = self.press('oh:re:p:prj-a')
        self.assertIn('result: shipped', entry['text'])
        confirm = next(c for c in self.callbacks(entry) if c.startswith('oh:rc:p:prj-a:'))
        self.assertIn('oh:rs:p:0', self.callbacks(entry))
        self.assertIsNone(self.press(confirm, user='999'))
        done = self.press(confirm)
        self.assertEqual(done['toast'], 'Подтверждено ✅')
        self.assertFalse(any(c.startswith('oh:rc:') for c in self.callbacks(done)))
        resume = self.runtime.store.resume_profile(redact=False)
        self.assertTrue(next(e for e in resume['projects'] if e['id'] == 'prj-a')['confirmed'])
        self.assertFalse(resume['languages'][0]['confirmed'])
        self.assertEqual(self.press(confirm)['toast'], 'Уже подтверждено')
        self.assertTrue(self.press('oh:rc:p:prj-missing:abcdef')['alert'])

    def test_old_confirm_button_does_not_confirm_a_changed_entry(self):
        self.import_resume()
        confirm = next(c for c in self.callbacks(self.press('oh:re:p:prj-a')) if c.startswith('oh:rc:'))
        self.runtime.store.import_master({'resume_profile': {'projects': [
            {'id': 'prj-a', 'name': 'Project A', 'result': 'something else'}]}}, apply=True)
        result = self.press(confirm)
        self.assertTrue(result['alert'])
        self.assertIn('something else', result['text'])  # the new version is shown for review
        self.assertFalse(self.runtime.store.resume_profile(redact=False)['projects'][0]['confirmed'])

    def test_entry_too_long_to_show_cannot_be_confirmed(self):
        self.runtime.store.import_master({'resume_profile': {'projects': [
            {'id': 'prj-long', 'name': 'Long', 'summary_ru': 'я' * 1900, 'summary_en': 'e' * 1900,
             'highlights_ru': ['h' * 300] * 5}]}}, apply=True)
        entry = self.press('oh:re:p:prj-long')
        self.assertFalse(any(c.startswith('oh:rc:') for c in self.callbacks(entry)))
        self.assertIn('не помещается', entry['text'])
        self.assertLessEqual(len(entry['text']), 4000)

    def test_stale_proposals_disappear_after_a_version_bump(self):
        self.import_resume()
        self.runtime.handle({'action': 'resume_propose', 'ops': [
            {'op': 'add', 'section': 'skills', 'item': {'id': 'sk-go', 'name': 'Go'}}]})
        self.assertEqual(len(self.runtime.store.pending_proposals()), 1)
        confirm = next(c for c in self.callbacks(self.press('oh:re:p:prj-a')) if c.startswith('oh:rc:'))
        self.press(confirm)
        self.assertEqual(self.runtime.store.pending_proposals(), [])  # no dead entries, no blocked slots
        for city in ('А', 'Б', 'В'):
            self.runtime.handle({'action': 'profile_propose', 'patch': {'city': city}})

    def test_pending_command_survives_a_proposal_that_no_longer_fits(self):
        self.runtime.store.import_master({'resume_profile': {'projects': [
            {'id': 'prj-a', 'name': 'A', 'result': 'short'}]}}, apply=True)
        self.runtime.handle({'action': 'resume_propose', 'ops': [
            {'op': 'replace', 'section': 'projects', 'item': {'id': 'prj-a', 'name': 'A', 'result': 'new'}}]})
        self.runtime.handle({'action': 'profile_propose', 'patch': {'city': 'Казань'}})
        original = buttons.format_proposal
        buttons.format_proposal = lambda p, c=None: (_ for _ in ()).throw(ValueError('big')) \
            if p['scope'] == 'resume' else original(p, c)
        try:
            text = self.runtime.run_command('pending')
        finally:
            buttons.format_proposal = original
        self.assertIn('отправлено с кнопками: 1', text)
        self.assertIn('слишком большие: 1', text)

    def test_model_cannot_confirm_resume_facts(self):
        source = (Path(__file__).resolve().parents[1] / 'opportunity_hunter/assets/plugin/__init__.py').read_text(encoding='utf-8')
        self.assertNotIn('confirm_resume_entry', source)
        self.import_resume()
        with self.assertRaises(ValueError):
            self.runtime.handle({'action': 'confirm_resume_entry', 'section': 'projects', 'id': 'prj-a'})


if __name__ == '__main__':
    unittest.main()
