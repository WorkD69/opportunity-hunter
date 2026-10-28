"""Reproduce old missing-/start behavior with synthetic credentials and a fake API."""
from pathlib import Path
import contextlib,importlib.util,io,json,sys,tempfile
base=Path('/opt/data/profiles/opportunity-hunter/operator')
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
old=load('old_setup',base/'configure_telegram.previous.py')
new=load('new_setup',base/'configure_telegram.py')
token='123456789:'+'x'*35
def api(method,payload=None):
    return {'getMe':{'username':'example_test_bot'},'getWebhookInfo':{'url':''},'getUpdates':[]}[method]
class Response:
    def __init__(self,request):self.method=request.full_url.rsplit('/',1)[-1]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return json.dumps({'ok':True,'result':api(self.method)}).encode()
with tempfile.TemporaryDirectory(prefix='oh-setup-repro-') as directory:
    root=Path(directory)
    home=root/'profiles/opportunity-hunter'
    home.mkdir(parents=True)
    (home/'gateway.parked').touch()
    (root/'.env').write_text('TELEGRAM_ALLOWED_USERS=12345\n')
    old.HOME=home
    saved_stdin=sys.stdin
    original_urlopen=old.urllib.request.urlopen
    try:
        sys.stdin=io.StringIO(token)
        old.urllib.request.urlopen=lambda request,**kwargs:Response(request)
        rejected=False
        try:old.main()
        except ValueError:rejected=True
        old_saved=(home/'.env').exists()
    finally:
        old.urllib.request.urlopen=original_urlopen
        sys.stdin=saved_stdin
    result=new.configure(token,home=home,api=api,expected_bot='example_test_bot')
    new_saved=new.env_values(home/'.env').get('TELEGRAM_BOT_TOKEN')==token
    print(json.dumps({'synthetic_inputs_only':True,'live_api_called':False,
       'old_rejects_missing_start':rejected,'old_token_retained':old_saved,
       'new_waits_for_start':result['status']=='awaiting_start','new_token_retained_verified':new_saved}))
    assert rejected and not old_saved and new_saved
