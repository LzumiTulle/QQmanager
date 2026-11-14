import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from qqmanager.llm import (Analyzer, ModelError, request_model, save_config, signature,
                           status, test_connection, validate_result)
from qqmanager.store import Store


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.set_setting('bridge_enabled', True)
        self.store.set_setting('bridge_groups', ['123','456'])
        self.store.set_setting('ws_account', '7')
        save_config(self.store, {'url':'https://example.test/v1','model':'test-model','key':'secret-key'})

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def emit(self, number=1, text='通知：参赛同学明天18:00前提交报名表。资料：https://example.test/form', group=123, account=7):
        return self.store.onebot({'post_type':'message','message_type':'group','group_id':group,'self_id':account,
            'message_id':number,'time':1794618000,'sender':{'nickname':'同学'},'raw_message':text})

    @staticmethod
    def good(config, messages, target, interests=''):
        row = next(x for x in messages if x['id'] == target)
        return {'items':[{'kind':kind,'title':'标题 '+kind,'reason':'包含明确内容',
                          'evidence':[{'message_id':target,'quote':row['content']}]} for kind in ('待办','资料')]}

    def enable(self):
        test_connection(self.store, caller=self.good)

    def test_new_live_messages_queued_once_not_imported_history(self):
        self.emit();self.emit()
        self.store.import_data('manual.txt', '通知：测试'.encode(), '导入群', 'chat')
        self.assertEqual(len(self.store.query('SELECT * FROM analysis_jobs')),1)
        self.assertFalse(Analyzer(self.store,self.good).step())
        self.assertEqual(self.store.query('SELECT state FROM analysis_jobs')[0]['state'],'pending')

    def test_multiple_items_verbatim_evidence_and_no_auto_reminder(self):
        self.enable();self.emit()
        worker=Analyzer(self.store,self.good)
        self.assertTrue(worker.step());self.assertFalse(worker.step())
        rows=self.store.search('insights')
        self.assertEqual(len(rows),2)
        self.assertEqual({r['kind'] for r in rows},{'待办','资料'})
        self.assertTrue(all(r['engine']=='llm' for r in rows))
        evidence=json.loads(rows[0]['evidence'])[0]
        self.assertEqual(evidence['quote'],rows[0]['content'])
        self.assertEqual(rows[0]['due'],'2026-11-15T18:00:00+08:00')
        self.assertIsNone(rows[1]['due'])
        self.assertEqual(self.store.snapshot()['counts']['tasks'],0)
        self.store.reanalyze()
        self.assertEqual(len(self.store.search('insights')),2)

    def test_invalid_evidence_retry_then_fail_keeps_rule(self):
        self.enable();self.emit()
        def invalid(*args):
            obj=self.good(*args);obj['items'][0]['evidence'][0]['quote']='不存在的引用';return obj
        worker=Analyzer(self.store,invalid)
        for _ in range(3):
            self.assertTrue(worker.step())
            self.store.db.execute('UPDATE analysis_jobs SET next_at=0');self.store.db.commit()
        self.assertEqual(self.store.query('SELECT state,attempts FROM analysis_jobs')[0],{'state':'failed','attempts':3})
        self.assertEqual(self.store.search('insights')[0]['engine'],'rule')

    def test_schema_rejects_foreign_id_and_missing_target(self):
        rows=[{'id':1,'content':'原文','sent_at':None},{'id':2,'content':'上下文','sent_at':None}]
        result={'items':[{'kind':'待办','title':'事项','reason':'原因','evidence':[{'message_id':3,'quote':'原文'}]}]}
        with self.assertRaises(ModelError):validate_result(result,rows,1)
        result['items'][0]['evidence']=[{'message_id':2,'quote':'上下文'}]
        with self.assertRaises(ModelError):validate_result(result,rows,1)
        result['items'][0]['evidence']=[{'message_id':1,'quote':'原文'}]
        self.assertIsNone(validate_result(result,rows,1)[0]['due'])

    def test_context_is_bounded_same_group_same_account(self):
        self.enable()
        self.emit(1,group=456);self.emit(2,account=8)
        for i in range(3,14):self.emit(i,text='上下文 '+str(i))
        self.store.db.execute("UPDATE analysis_jobs SET state='done' WHERE message_id<13");self.store.db.commit()
        seen=[]
        def capture(c, messages, target, interests):
            seen.extend(messages);return {'items':[]}
        Analyzer(self.store,capture).step()
        self.assertEqual(len(seen),8)
        self.assertNotIn(1,[r['id'] for r in seen]);self.assertNotIn(2,[r['id'] for r in seen])
        self.assertEqual(seen[-1]['id'],13)

    def test_unselected_group_and_switched_account_never_sent(self):
        self.enable();self.emit();self.emit(2,account=8,group=456)
        self.store.set_setting('bridge_groups',['456'])
        def no_call(*args):self.fail('must not send')
        worker=Analyzer(self.store,no_call);worker.step();worker.step()
        self.assertTrue(all(r['state']=='skipped' for r in self.store.query('SELECT state FROM analysis_jobs')))

    def test_queue_restart_and_daily_cap(self):
        self.enable();self.emit()
        cfg=self.store.setting('llm_config');cfg['daily_limit']=1;self.store.set_setting('llm_config',cfg)
        self.assertFalse(Analyzer(self.store,self.good).step()) # synthetic test used one call
        self.store.db.execute("UPDATE analysis_jobs SET state='running'");self.store.db.commit()
        self.store.db.close();self.store=Store(self.tmp.name)
        self.assertEqual(self.store.query('SELECT state FROM analysis_jobs')[0]['state'],'pending')
        self.assertEqual(status(self.store)['calls_today'],1)

    def test_no_db_lock_during_provider_call_and_pause_discards_result(self):
        self.enable();self.emit()
        def provider(*args):
            done=threading.Event()
            def receive():
                self.emit(2,text='新的消息')
                cfg=self.store.setting('llm_config');cfg['enabled']=False;self.store.set_setting('llm_config',cfg)
                done.set()
            t=threading.Thread(target=receive);t.start()
            self.assertTrue(done.wait(2),'database was locked during network request');t.join()
            return self.good(*args)
        Analyzer(self.store,provider).step()
        self.assertEqual(self.store.query("SELECT count(*) n FROM insights WHERE engine='llm'")[0]['n'],0)
        self.assertEqual(len(self.store.query('SELECT * FROM analysis_jobs')),2)

    def test_preserve_user_task_and_do_not_hide_model_failure(self):
        self.enable();self.emit()
        rule=self.store.search('insights')[0]
        self.store.create_task({'title':'用户确认事项','insight_id':rule['id']})
        Analyzer(self.store,self.good).step()
        self.assertEqual(self.store.snapshot()['tasks'][0]['title'],'用户确认事项')
        self.assertEqual(len(self.store.search('insights')),3)

    def test_configuration_secret_redaction_and_no_forward_on_endpoint_change(self):
        self.assertNotIn('secret-key',json.dumps(self.store.snapshot()))
        with self.assertRaises(ValueError):save_config(self.store,{'url':'https://other.test/v1'})
        with self.assertRaises(ValueError):save_config(self.store,{'url':'http://remote.test/v1','key':'x'})
        save_config(self.store,{'url':'http://127.0.0.1:11434/v1','clear_key':True})
        self.assertFalse(status(self.store)['has_key'])
        self.assertFalse(status(self.store)['enabled'])

    def test_migration_keeps_legacy_task_foreign_key_and_backup(self):
        self.emit()
        item=self.store.search('insights')[0]
        self.store.create_task({'title':'保留','insight_id':item['id']})
        self.store.db.commit();self.store.db.execute('PRAGMA foreign_keys=OFF')
        self.store.db.executescript('''CREATE TABLE legacy (id INTEGER PRIMARY KEY,message_id INTEGER UNIQUE REFERENCES messages(id),
            kind TEXT,title TEXT,score INTEGER,reason TEXT,due TEXT,date_note TEXT,status TEXT DEFAULT 'new');
            INSERT INTO legacy SELECT id,message_id,kind,title,score,reason,due,date_note,status FROM insights;
            DROP TABLE insights; ALTER TABLE legacy RENAME TO insights;''')
        self.store.db.close();self.store=Store(self.tmp.name)
        self.assertEqual(self.store.snapshot()['tasks'][0]['insight_id'],item['id'])
        self.assertEqual(self.store.query('PRAGMA foreign_key_check'),[])
        self.assertTrue(list(Path(self.tmp.name).glob('before-model-*.db')))
        self.store.reanalyze()


class ProviderHTTPTests(unittest.TestCase):
    def test_real_http_json_contract_and_no_redirected_credentials(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                captured.append((self.path,self.headers.get('Authorization'),body))
                if self.path.startswith('/redirect/'):
                    self.send_response(307);self.send_header('Location','/stolen');self.end_headers();return
                data=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            config={'url':'http://127.0.0.1:'+str(server.server_port)+'/v1','key':'test-only','model':'fake'}
            self.assertEqual(request_model(config,[{'id':1,'content':'你好'}],1),{'items':[]})
            self.assertEqual(captured[0][0],'/v1/chat/completions')
            self.assertEqual(captured[0][2]['response_format'],{'type':'json_object'})
            config['url']=config['url'].replace('/v1','/redirect')
            with self.assertRaises(ModelError):request_model(config,[],1)
            self.assertEqual(len(captured),2)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()

class WorkflowHTTPTests(unittest.TestCase):
    def test_config_test_enable_ingest_and_source_via_http(self):
        import urllib.request
        from qqmanager.server import make_server
        calls=[]
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                request=json.loads(body['messages'][1]['content'])
                calls.append(request)
                row=next(r for r in request['context'] if r['id']==request['target_id'])
                result={'items':[{'kind':'待办','title':'提交报名表','reason':'明确要求提交',
                    'evidence':[{'message_id':row['id'],'quote':row['content']}]}]}
                raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        provider_thread=threading.Thread(target=provider.serve_forever,daemon=True);provider_thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);app=make_server(store,0)
            app_thread=threading.Thread(target=app.serve_forever,daemon=True);app_thread.start()
            base='http://127.0.0.1:'+str(app.server_port)
            try:
                csrf=json.load(urllib.request.urlopen(base+'/api/session'))['csrf']
                def post(path,body):
                    return json.load(urllib.request.urlopen(urllib.request.Request(base+path,json.dumps(body).encode(),
                        {'Content-Type':'application/json','X-CSRF-Token':csrf})))
                post('/api/llm/config',{'url':'http://127.0.0.1:'+str(provider.server_port)+'/v1','model':'mock','key':'secret'})
                self.assertTrue(post('/api/llm/test',{'enable':True})['ok'])
                store.set_setting('bridge_enabled',True);store.set_setting('bridge_groups',['12'])
                event={'post_type':'message','message_type':'group','group_id':12,'message_id':1,'self_id':7,
                       'time':1794618000,'sender':{'nickname':'测试群友'},'raw_message':'通知：明天18:00提交报名表'}
                req=urllib.request.Request(base+'/api/onebot',json.dumps(event).encode(),
                    {'Content-Type':'application/json','Authorization':'Bearer '+store.setting('bridge_token')})
                self.assertEqual(json.load(urllib.request.urlopen(req))['messages'],1)
                self.assertTrue(Analyzer(store).step())
                result=json.load(urllib.request.urlopen(base+'/api/state'))
                self.assertNotIn('secret',json.dumps(result))
                self.assertEqual(result['llm']['counts']['done'],1)
                insight=result['insights'][0]
                self.assertEqual(insight['engine'],'llm')
                self.assertEqual(json.loads(insight['evidence'])[0]['quote'],event['raw_message'])
                self.assertEqual(result['tasks'],[])
                post('/api/tasks',{'title':insight['title'],'insight_id':insight['id'],'due':insight['due']})
                result=json.load(urllib.request.urlopen(base+'/api/state'))
                self.assertEqual(result['tasks'][0]['evidence'],insight['evidence'])
                post('/api/llm/pause',{})
                self.assertFalse(store.setting('llm_config')['enabled'])
                self.assertEqual(len(calls),2)
            finally:
                app.shutdown();app.server_close();app_thread.join();store.db.close()
                provider.shutdown();provider.server_close();provider_thread.join()
