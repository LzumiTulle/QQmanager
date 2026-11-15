import json
import tempfile
import unittest
from qqmanager.native import NativeBridge
from qqmanager.store import Store

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);self.n=NativeBridge(self.s)
        for k,v in {'native_enabled':True,'bridge_enabled':True,'bridge_groups':['123456'],'ws_account':'777777'}.items():self.s.set_setting(k,v)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def event(self,**changes):
        e={'post_type':'message','message_type':'group','self_id':'777777','group_id':'123456','message_id':'9007199254740993999','time':1794618000,'sender':{'nickname':'测试'},'message':[{'type':'text','data':{'text':'通知：明天18:00提交报告'}}]};e.update(changes);return e
    def test_ingest_into_same_analysis_pipeline_and_dedup(self):
        self.assertEqual(self.n.events({'events':[self.event()]})['messages'],1)
        self.assertEqual(self.n.events({'events':[self.event()]})['messages'],0)
        self.assertEqual(self.s.query('SELECT source FROM messages')[0]['source'],'QQ 客户端插件')
        self.assertEqual(self.s.query('SELECT state FROM analysis_jobs')[0]['state'],'pending')
        self.assertEqual(self.s.snapshot()['counts']['tasks'],0)
    def test_wrong_account_rejects_entire_batch_and_other_groups_ignored(self):
        with self.assertRaises(ValueError):self.n.events({'events':[self.event(),self.event(self_id='888888')]})
        self.assertEqual(self.s.snapshot()['counts']['messages'],0)
        self.assertEqual(self.n.events({'events':[self.event(group_id='999999')]})['messages'],0)
    def test_private_messages_and_remote_commands_rejected(self):
        for event in [self.event(message_type='private'),{'action':'send_msg'},self.event(message=[{'type':'image','data':{'url':'https://example.com'}}])]:
            with self.assertRaises(ValueError):self.n.events({'events':[event]})
    def test_group_selection_add_remove_clear_and_history_retention(self):
        self.n.events({'events':[self.event(group_name='设计讨论群')]})
        self.n.select_groups(['999999','123456','999999'],{'999999':'测试用群'})
        self.assertEqual(self.s.setting('bridge_groups'),['999999','123456'])
        self.assertEqual(len(self.n.group_catalog()),2)
        self.n.select_groups(['999999'])
        self.assertEqual(self.n.events({'events':[self.event(message_id='100') ]})['messages'],0)
        self.assertEqual(self.s.snapshot()['counts']['messages'],1)
        self.assertFalse(next(g for g in self.n.group_catalog() if g['group_id']=='123456')['selected'])
        self.n.select_groups([])
        self.assertFalse(self.n.heartbeat({'account':'777777'})['enabled'])
        self.assertFalse(self.n.snapshot()['enabled'])
        self.n.select_groups(['123456'])
        self.assertTrue(self.n.heartbeat({'account':'777777'})['enabled'])
        self.s.set_setting('ws_account','888888');self.s.set_setting('bridge_groups',[])
        self.assertEqual(self.n.group_catalog(),[])
    def test_invalid_group_selection_does_not_change_configuration(self):
        for groups in [None,'123456',['123'],['abcde'],['１２３４５'],[123456]]:
            with self.assertRaises(ValueError):self.n.select_groups(groups)
        with self.assertRaises(ValueError):self.n.select_groups(['999999'],{'123456':'wrong group'})
        self.assertEqual(self.s.setting('bridge_groups'),['123456'])
    def test_full_group_catalog_is_local_account_scoped_and_does_not_select_groups(self):
        catalog={'account':'777777','revision':1,'groups':[{'group_id':'999999','group_name':'可选群','secret':'not retained'}]}
        self.n.heartbeat({'account':'777777','group_catalog':catalog})
        self.assertEqual(self.s.setting('bridge_groups'),['123456'])
        self.assertFalse(next(g for g in self.n.group_catalog() if g['group_id']=='999999')['selected'])
        self.assertNotIn('secret',json.dumps(self.s.setting('native_group_catalog')))
        self.assertEqual(self.n.snapshot()['groups_count'],1)
        self.assertEqual(self.n.events({'events':[self.event(group_id='999999') ]})['messages'],0)
        self.n.refresh_groups();self.assertEqual(self.n.heartbeat({'account':'777777'})['group_refresh'],1)
        with self.assertRaises(ValueError):self.n.heartbeat({'account':'888888','group_catalog':catalog})
        self.s.set_setting('ws_account','888888');self.s.set_setting('bridge_groups',[])
        self.assertEqual(self.n.group_catalog(),[]);self.assertEqual(self.n.snapshot()['groups_count'],0)
    def test_pause_refuses_delivery_and_heartbeat_only_reveals_safe_diagnostics(self):
        self.s.set_setting('native_enabled',False)
        with self.assertRaises(ValueError):self.n.events({'events':[self.event()]})
        result=self.n.heartbeat({'account':'777777','platform':'darwin','secret':'DO NOT KEEP','message':'private text','signals':{'observer_started':1}})
        self.assertFalse(result['enabled']);self.assertTrue(self.n.snapshot()['account_matches'])
        raw=json.dumps(self.n.snapshot());self.assertNotIn('DO NOT KEEP',raw);self.assertNotIn('private text',raw);self.assertNotIn('777777',raw)

class InstallerTests(unittest.TestCase):
    def test_install_restore_on_disposable_app_preserves_original_bytes(self):
        import importlib.util
        import sys
        from pathlib import Path
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('qqm_installer',Path(__file__).resolve().parents[1]/'scripts/native/install_mac.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'QQ.app';folder=app/'Contents/Resources/app';(folder/'app_launcher').mkdir(parents=True)
            raw=b'{"main":"./application.asar/app_launcher/index.js","version":"fake-test"}\n'
            (folder/'package.json').write_bytes(raw);stage=root/'stage';profile=root/'profile'
            def run(action):
                args=['install_mac.py',action,'--app',str(app),'--stage',str(stage),'--profile',str(profile)]
                with patch.object(sys,'argv',args):module.main()
            with patch.object(module,'request_config',return_value={'url':'http://127.0.0.1:8765/','token':'x'*40,'account':'777777','groups':['123456']}):run('prepare')
            self.assertEqual((folder/'package.json').read_bytes(),raw)
            run('install');self.assertEqual(json.loads((folder/'package.json').read_text())['main'],module.NEW_MAIN)
            self.assertTrue((profile/'main.cjs').exists());run('restore')
            self.assertEqual((folder/'package.json').read_bytes(),raw)
            self.assertFalse((folder/'app_launcher/qqmanager-bootstrap.cjs').exists())
    def test_restore_refuses_to_overwrite_later_qq_update(self):
        import importlib.util,sys,hashlib
        from pathlib import Path
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('qqm_installer',Path(__file__).resolve().parents[1]/'scripts/native/install_mac.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'QQ.app';folder=app/'Contents/Resources/app';folder.mkdir(parents=True);stage=root/'stage';stage.mkdir()
            package=folder/'package.json';package.write_text('{"main":"new-qq-entry"}')
            (stage/'backup.json').write_text(json.dumps({'package':str(package),'installed_sha256':'old','original_sha256':'older'}))
            before=package.read_bytes()
            with patch.object(sys,'argv',['install_mac.py','restore','--app',str(app),'--stage',str(stage)]):
                with self.assertRaises(SystemExit):module.main()
            self.assertEqual(package.read_bytes(),before)

class NativeHTTPTests(unittest.TestCase):
    def test_plugin_auth_and_pairing_csrf(self):
        import threading,urllib.request,urllib.error
        from qqmanager.server import make_server
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);server=make_server(store,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                csrf=json.load(urllib.request.urlopen(base+'/api/session'))['csrf']
                def post(route,body,headers={}):
                    return json.load(urllib.request.urlopen(urllib.request.Request(base+route,json.dumps(body).encode(),{'Content-Type':'application/json',**headers})))
                with self.assertRaises(urllib.error.HTTPError) as err:post('/api/native/pair',{})
                self.assertEqual(err.exception.code,403)
                with self.assertRaises(urllib.error.HTTPError) as err:post('/api/native/heartbeat',{})
                self.assertEqual(err.exception.code,401)
                config=post('/api/native/pair',{'account':'777777','groups':['123456']},{'X-CSRF-Token':csrf})
                self.assertGreater(len(config['token']),30)
                answer=post('/api/native/heartbeat',{'account':'777777','platform':'test'},{'Authorization':'Bearer '+config['token']})
                self.assertTrue(answer['enabled'])
                snapshot=json.load(urllib.request.urlopen(base+'/api/native'))
                self.assertTrue(snapshot['account_matches']);self.assertNotIn(config['token'],json.dumps(snapshot))
            finally:
                server.shutdown();server.server_close();thread.join();store.db.close()
