import base64
import tempfile
import unittest
from unittest.mock import patch
from qqmanager.store import Store
from qqmanager.native import NativeBridge
from qqmanager import media

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        for k,v in {'ws_account':'777777','bridge_groups':['123456'],'bridge_enabled':True,'native_enabled':True}.items():self.s.set_setting(k,v)
        self.n=NativeBridge(self.s)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def event(self):
        return {'post_type':'message','message_type':'group','self_id':'777777','group_id':'123456','message_id':'90001','time':1794801000,'sender':{'nickname':'老师'},'message':[{'type':'image','data':{'name':'考试.png','element_id':'80001','size':100}}]}
    def upload(self,**changes):
        return media.receive(self.s,dict(account='777777',group='123456',message_id='90001',element_id='80001',data=base64.b64encode(b'test image bytes').decode(),**changes))
    def test_attachment_keeps_original_and_ocr_separate_and_requeues_analysis(self):
        e=self.event();self.n.events({'events':[e]});self.upload()
        with patch('qqmanager.ocr.recognize',return_value='第13周考试日程\n11月20日 9:00 数学'):
            self.assertTrue(media.step(self.s))
        msg=self.s.search('messages')[0]
        self.assertEqual(msg['content'],'[图片：未解析]')
        self.assertEqual(msg['attachments'][0]['status'],'ready')
        self.assertIn('11月20日',media.analysis_source(self.s,msg)['content'])
        self.assertEqual(len(self.s.search('files')),1)
        self.n.events({'events':[e]});self.upload()
        self.assertEqual(len(self.s.search('files')),1)
        self.assertEqual(len(self.s.search('messages')),1)
    def test_scope_unknown_message_and_bad_encoding_are_rejected(self):
        self.n.events({'events':[self.event()]})
        b={'account':'777777','group':'999999','message_id':'90001','element_id':'80001','data':'YWJj'}
        with self.assertRaises(ValueError):media.receive(self.s,b)
        b['group']='123456';b['message_id']='90002'
        with self.assertRaises(ValueError):media.receive(self.s,b)
        self.assertEqual(self.s.query('SELECT count(*) n FROM files')[0]['n'],0)
    def test_recovery_enriches_only_preexisting_message(self):
        e=self.event();e['recovery']=True
        self.assertEqual(self.n.events({'events':[e]})['messages'],0)
        e.pop('recovery');self.n.events({'events':[e]})
        e['recovery']=True;self.n.events({'events':[e]})
        self.assertEqual(len(self.s.search('messages')),1)
        self.assertEqual(len(media.recovery_targets(self.s)),1)
        self.s.set_setting('bridge_groups',[])
        self.assertEqual(media.recovery_targets(self.s),[])
    def test_failed_ocr_preserves_download_and_is_not_a_noise_filter(self):
        self.n.events({'events':[self.event()]});self.upload()
        with patch('qqmanager.ocr.recognize',side_effect=RuntimeError('private path')):media.step(self.s)
        a=self.s.search('messages')[0]['attachments'][0]
        self.assertEqual(a['status'],'error');self.assertTrue(a['file_id'])
        self.assertNotIn('private path',a['error'])
    def test_exam_pointer_and_academic_tasks_have_fallback(self):
        from qqmanager.analysis import analyze
        self.assertEqual(analyze('13周考试日程如上')['kind'],'通知')
        self.assertEqual(analyze('补考安排见图')['kind'],'通知')
        self.assertEqual(analyze('请学习委员负责收集材料，请同学们核查自己的学业情况。')['kind'],'待办')
        self.assertIsNone(analyze('明天考试好烦啊'))

    def test_ocr_dates_require_original_image_confirmation(self):
        from qqmanager.llm import validate_result
        row={'id':1,'content':'[图片：未解析]\n【图片文字识别，可能有误：考试.png】\n考试安排：明天9点数学考试','sent_at':'2026-11-16T10:00:00+08:00'}
        item={'kind':'通知','title':'查看考试安排','reason':'考试日程','evidence':[{'message_id':1,'quote':'考试安排：明天9点数学考试'}]}
        result=validate_result({'items':[item]},[row],1)[0]
        self.assertIsNone(result['due'])
        self.assertIn('核对原图',result['date_note'])

    def test_http_attachment_authorization_and_inert_preview(self):
        import json,threading,urllib.request,urllib.error
        from qqmanager.server import make_server
        self.n.events({'events':[self.event()]})
        server=make_server(self.s,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def post(body,auth=True):
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+self.s.setting('native_token')
            return json.load(urllib.request.urlopen(urllib.request.Request(base+'/api/native/attachment',json.dumps(body).encode(),headers)))
        body=dict(account='777777',group='123456',message_id='90001',element_id='80001',data=base64.b64encode(b'<html><script>alert(1)</script>').decode())
        try:
            with self.assertRaises(urllib.error.HTTPError) as err:post(body,False)
            self.assertEqual(err.exception.code,401)
            fid=post(body)['file_id']
            with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(base+'/api/preview/'+str(fid))
            self.assertEqual(err.exception.code,415)
            with urllib.request.urlopen(base+'/api/download/'+str(fid)) as response:
                self.assertEqual(response.headers['Content-Type'],'application/octet-stream')
                self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
            self.assertEqual(json.load(urllib.request.urlopen(base+'/api/file/'+str(fid)))['name'],'考试.png')
        finally:server.shutdown();server.server_close();thread.join()
