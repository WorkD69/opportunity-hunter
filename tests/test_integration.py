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


if __name__ == '__main__':
    unittest.main()
