"""Credential setup tests use synthetic tokens and no live services."""
import importlib.util
import tempfile
import unittest
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('configure_telegram',Path(__file__).parents[1]/'scripts/configure_telegram.py')
setup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
TOKEN='123456789:'+'x'*35

def values(path):
    if not path.is_file(): return {}
    return dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.home=self.root/'profiles/opportunity-hunter'
        self.home.mkdir(parents=True)
        (self.home/'gateway.parked').touch()
        (self.root/'.env').write_text('TELEGRAM_ALLOWED_USERS=12345\nTELEGRAM_BOT_TOKEN=555555555:'+('z'*35)+'\n')
        self.old=(self.root/'.env').read_bytes()
        self.updates=[]
        self.patch=patch.object(setup,'env_values',values)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def api(self,method,payload=None):
        if method=='getMe': return {'username':'example_test_bot','id':123456789}
        if method=='getWebhookInfo': return {'url':''}
        if method=='getUpdates': return self.updates
        if method=='getChat': return {'type':'private','id':12345}
        raise AssertionError(method)

    def run_setup(self,token=None,check=False):
        return setup.configure(token,home=self.home,api=self.api,check_only=check,expected_bot='example_test_bot')

    def test_valid_token_retained_when_start_missing(self):
        result=self.run_setup(TOKEN)
        self.assertEqual(result['status'],'awaiting_start')
        self.assertEqual(values(self.home/'.env')['TELEGRAM_BOT_TOKEN'],TOKEN)
        self.assertNotIn('OH_OWNER_ID',values(self.home/'.env'))
        self.assertEqual((self.root/'.env').read_bytes(),self.old)

    def test_finish_after_start_needs_no_second_token(self):
        self.run_setup(TOKEN)
        self.updates=[{'message':{'text':'/start','from':{'id':12345},'chat':{'id':12345,'type':'private'}}}]
        result=self.run_setup()
        self.assertTrue(result['ok'])
        stored=values(self.home/'.env')
        for k in ['OH_OWNER_ID','TELEGRAM_HOME_CHANNEL','TELEGRAM_ALLOWED_USERS']:
            self.assertEqual(stored[k],'12345')
        self.assertTrue(self.run_setup(check=True)['ok'])
        self.assertEqual((self.root/'.env').read_bytes(),self.old)

    def test_foreign_start_cannot_set_owner(self):
        self.updates=[{'message':{'text':'/start','from':{'id':99999},'chat':{'id':99999,'type':'private'}}}]
        self.assertEqual(self.run_setup(TOKEN)['status'],'awaiting_start')
        self.assertNotIn('OH_OWNER_ID',values(self.home/'.env'))

    def test_wrong_bot_never_persisted(self):
        def wrong(method,payload=None): return {'username':'another_bot','id':123456789}
        with self.assertRaises(setup.SetupError):
            setup.configure(TOKEN,home=self.home,api=wrong,expected_bot='example_test_bot')
        self.assertFalse((self.home/'.env').exists())

    def test_missing_expected_bot_is_rejected_before_any_api_call(self):
        def forbidden(method,payload=None): raise AssertionError('API must not be called')
        with patch.dict(setup.os.environ,{},clear=True):
            with self.assertRaises(setup.SetupError) as caught:
                setup.configure(TOKEN,home=self.home,api=forbidden)
        self.assertEqual(str(caught.exception),'BOT_USERNAME_NOT_CONFIGURED')
        self.assertFalse((self.home/'.env').exists())

    def test_token_collision_never_persisted(self):
        (self.root/'.env').write_text('TELEGRAM_ALLOWED_USERS=12345\nTELEGRAM_BOT_TOKEN='+TOKEN+'\n')
        with self.assertRaises(setup.SetupError): self.run_setup(TOKEN)
        self.assertFalse((self.home/'.env').exists())

    def test_api_error_does_not_disclose_exception(self):
        with patch.object(setup.urllib.request,'urlopen',side_effect=RuntimeError('SECRET_IN_EXCEPTION')):
            with self.assertRaises(setup.SetupError) as caught: setup.telegram_api(TOKEN)('getMe')
        self.assertNotIn('SECRET_IN_EXCEPTION',str(caught.exception))

class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
        import telegram_setup
        cls.client=telegram_setup

    def test_secret_in_stdin_only_and_stderr_not_logged(self):
        client=self.client
        output=SimpleNamespace(returncode=255,stdout='',stderr=TOKEN)
        with patch.object(client,'ssh_args',return_value=['ssh','server']),patch.object(client,'preflight',return_value=None),patch.object(client,'expected_bot',return_value='example_test_bot'),patch.object(client,'log') as log,patch.object(client.subprocess,'run',return_value=output) as run:
            value=client.request('',token=TOKEN)
        self.assertEqual(value['code'],'SSH_FAILED')
        self.assertNotIn(TOKEN,repr(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['input'],TOKEN+'\n')
        self.assertNotIn(TOKEN,repr(log.call_args_list))
        self.assertNotIn(TOKEN,repr(value))

    def test_success_requires_zero_exit_code(self):
        client=self.client
        output=SimpleNamespace(returncode=1,stdout='{"ok":true,"code":"TELEGRAM_CONFIGURED"}',stderr='')
        with patch.object(client,'ssh_args',return_value=['ssh','server']),patch.object(client,'preflight',return_value=None),patch.object(client,'expected_bot',return_value='example_test_bot'),patch.object(client,'log'),patch.object(client.subprocess,'run',return_value=output):
            self.assertFalse(client.request(' --check')['ok'])

class OAuthOutputTests(unittest.TestCase):
    def test_only_device_url_and_ephemeral_user_code_are_displayed(self):
        import io
        spec=importlib.util.spec_from_file_location('profile_oauth',Path(__file__).parents[1]/'scripts/profile_oauth.py')
        oauth=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(oauth)
        stream=io.StringIO()
        filtered=oauth.PublicOutput(stream)
        filtered.write('access_token='+TOKEN+'\naccount@example.invalid\n')
        filtered.write('  https://auth.openai.com/codex/device\n  ABCD-1234\n')
        self.assertNotIn(TOKEN,stream.getvalue())
        self.assertNotIn('example.invalid',stream.getvalue())
        self.assertIn('https://auth.openai.com/codex/device',stream.getvalue())
        self.assertIn('ABCD-1234',stream.getvalue())

if __name__=='__main__': unittest.main()
