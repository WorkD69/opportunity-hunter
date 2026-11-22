import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from opportunity_hunter.packaging import build_profile


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'opportunity-hunter'

    def test_build_is_complete_parked_secret_free_and_refuses_overwrite(self):
        build_profile(self.home)
        self.assertTrue((self.home / 'gateway.parked').exists())
        self.assertTrue((self.home / 'plugins/opportunity-hunter/__init__.py').exists())
        self.assertTrue((self.home / 'skills/opportunity-hunter/SKILL.md').exists())
        self.assertFalse((self.home / 'auth.json').exists())
        self.assertFalse((self.home / '.env').exists())
        jobs = json.loads((self.home / 'cron-spec.json').read_text(encoding='utf-8'))
        self.assertEqual({j['category'] for j in jobs if 'category' in j}, set('ABCD'))
        self.assertTrue(all(j['deliver'] == 'local' for j in jobs))
        with self.assertRaises(FileExistsError):
            build_profile(self.home)

    def test_plugin_reload_picks_up_changed_submodules(self):
        build_profile(self.home)
        stubs = {'hermes_constants': types.ModuleType('hermes_constants'),
                 'agent.secret_scope': types.ModuleType('agent.secret_scope'),
                 'gateway.session_context': types.ModuleType('gateway.session_context')}
        path = self.home / 'plugins/opportunity-hunter/__init__.py'
        runtime_file = path.parent / 'opportunity_hunter/runtime.py'

        def load():
            spec = importlib.util.spec_from_file_location('test_oh_reload', path,
                                                         submodule_search_locations=[str(path.parent)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules['test_oh_reload'] = mod
            spec.loader.exec_module(mod)
            return mod

        with patch.dict(sys.modules, stubs):
            self.assertFalse(hasattr(load().Runtime, 'DEPLOY_MARKER'))
            runtime_file.write_text(runtime_file.read_text(encoding='utf-8')
                                    + '\nRuntime.DEPLOY_MARKER = 2\n', encoding='utf-8')
            self.assertEqual(load().Runtime.DEPLOY_MARKER, 2)   # hot reload, not the cached old module
            for name in [n for n in sys.modules if n.startswith('test_oh_reload')]:
                del sys.modules[name]

    def test_packaged_plugin_registers_profile_scoped_tool_and_trusted_command(self):
        build_profile(self.home)
        hc = types.ModuleType('hermes_constants')
        hc.get_hermes_home = lambda: self.home
        secret = types.ModuleType('agent.secret_scope')
        secret.get_secret = lambda name: None
        session = types.ModuleType('gateway.session_context')
        session.get_session_env = lambda name: None
        registrations = {}
        class Context:
            profile_name = 'opportunity-hunter'
            def register_tool(self, **kwargs):
                registrations['tool'] = kwargs
            def register_command(self, **kwargs):
                registrations['command'] = kwargs
                registrations.setdefault('commands',{})[kwargs['name']]=kwargs
            def dispatch_tool(self, name, args):
                return '{}'
        path = self.home / 'plugins/opportunity-hunter/__init__.py'
        spec = importlib.util.spec_from_file_location('test_oh_plugin', path,
                                                     submodule_search_locations=[str(path.parent)])
        mod = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'test_oh_plugin': mod, 'hermes_constants': hc,
                                     'agent.secret_scope': secret, 'gateway.session_context': session}):
            spec.loader.exec_module(mod)
            mod.register(Context())
            # Hermes normalizes Telegram underscores to hyphens before plugin dispatch.
            for telegram_name in ['oh_health','oh_profile','oh_saved','oh_save','oh_details']:
                self.assertIn(telegram_name.replace('_','-'),registrations['commands'])
            result = json.loads(registrations['tool']['handler']({'action': 'profile'}))
            self.assertIn('year', result)
            self.assertIsNone(result['year'])
            self.assertIsNone(result['citizenship'])
            with self.assertRaises(PermissionError):
                registrations['command']['handler']('profile')
        self.assertEqual(registrations['tool']['toolset'], 'opportunity_hunter')
        actions = registrations['tool']['schema']['parameters']['properties']['action']['enum']
        self.assertNotIn('profile_confirm', actions)
        self.assertNotIn('submit', actions)


    def test_button_presses_are_handled_only_for_this_profiles_bot_and_owner(self):
        import asyncio
        build_profile(self.home)
        secrets = {'TELEGRAM_BOT_TOKEN': '111:test-token', 'OH_OWNER_ID': '123'}
        hc = types.ModuleType('hermes_constants')
        hc.get_hermes_home = lambda: self.home
        secret = types.ModuleType('agent.secret_scope')
        secret.get_secret = secrets.get
        session = types.ModuleType('gateway.session_context')
        session.get_session_env = lambda name: None
        captured = {}

        class CallbackQueryHandler:
            def __init__(self, callback, pattern=None):
                self.callback, self.pattern = callback, pattern
        ext = types.ModuleType('telegram.ext')
        ext.CallbackQueryHandler = CallbackQueryHandler

        class BadRequest(Exception):
            pass
        error = types.ModuleType('telegram.error')
        error.BadRequest = BadRequest
        telegram = types.ModuleType('telegram')
        telegram.InlineKeyboardButton = lambda text, callback_data: (text, callback_data)
        telegram.InlineKeyboardMarkup = lambda rows: rows

        class Context:
            profile_name = 'opportunity-hunter'
            def register_tool(self, **kwargs): pass
            def register_command(self, **kwargs): pass
            def dispatch_tool(self, name, args): return '{}'
            def register_telegram_handler(self, factory): captured['factory'] = factory

        class Native:
            def add_handler(self, handler): captured['handler'] = handler

        sent, answers, edits = [], [], []
        import contextlib
        gateway_run = types.ModuleType('gateway.run')
        gateway_run._profile_runtime_scope = lambda home: contextlib.nullcontext()

        class Bot:
            def __init__(self, bot_id): self.id = bot_id
            async def send_message(self, **kwargs): sent.append(kwargs)

        def press(data, user, chat, bot_id, edit_error=None):
            async def answer(text=None, show_alert=False): answers.append((text, show_alert))
            async def edit_message_text(text, reply_markup=None, **kwargs):
                if edit_error:
                    raise BadRequest(edit_error)
                edits.append((text, reply_markup))
            async def edit_message_reply_markup(reply_markup=None): edits.append((None, reply_markup))
            query = types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=user),
                                          message=types.SimpleNamespace(chat=types.SimpleNamespace(id=chat)),
                                          answer=answer, edit_message_text=edit_message_text,
                                          edit_message_reply_markup=edit_message_reply_markup)
            asyncio.run(captured['handler'].callback(types.SimpleNamespace(callback_query=query),
                                                     types.SimpleNamespace(bot=Bot(bot_id))))

        path = self.home / 'plugins/opportunity-hunter/__init__.py'
        spec = importlib.util.spec_from_file_location('test_oh_plugin_buttons', path,
                                                     submodule_search_locations=[str(path.parent)])
        mod = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'test_oh_plugin_buttons': mod, 'hermes_constants': hc, 'agent.secret_scope': secret,
                                     'gateway.session_context': session, 'telegram': telegram, 'telegram.error': error,
                                     'telegram.ext': ext, 'gateway.run': gateway_run}):
            spec.loader.exec_module(mod)
            mod.register(Context())
            captured['factory'](Native(), None)
            self.assertEqual(captured['handler'].pattern, r'^oh:')
            press('oh:m:help', 123, 123, 222)      # another profile's bot: ignored entirely
            press('oh:m:help', 999, 999, 111)      # a stranger on our bot: ignored
            self.assertEqual((sent, answers, edits), ([], [], []))
            press('oh:m:help', 123, 123, 111)      # the owner on our bot: the panel is edited in place
            self.assertEqual(sent, [])
            self.assertEqual(len(answers), 1)
            text, keyboard = edits[-1]
            self.assertIn('обычным текстом', text)
            self.assertIn(('🏠 Меню', 'oh:m:menu'), keyboard[-1])
            press('oh:m:health', 123, 123, 111, edit_error='Message is not modified')  # ignored quietly
            self.assertEqual(sent, [])
            press('oh:m:menu', 123, 123, 111, edit_error="Message can't be edited")    # falls back to a new panel
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]['chat_id'], 123)
            self.assertTrue(sent[0]['text'].startswith('🏠 Меню'))
            press('oh:old:junk', 123, 123, 111)     # stale button: an alert instead of a spinner
            self.assertEqual(answers[-1][1], True)
            count = len(answers)

            def broken_scope(home):
                raise RuntimeError('scope failure')
            gateway_run._profile_runtime_scope = broken_scope   # a failure before the owner check:
            press('oh:m:help', 999, 999, 222)                   # a stranger gets nothing, not even a toast
            self.assertEqual(len(answers), count)
            del gateway_run._profile_runtime_scope     # profile scope API missing: buttons fail closed
            press('oh:m:help', 123, 123, 111)
            self.assertEqual((len(sent), len(answers)), (1, count))

if __name__ == '__main__':
    unittest.main()
