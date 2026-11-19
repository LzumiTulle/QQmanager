import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from qqmanager.analysis import analyze, due_date, timestamp
from qqmanager.importers import extract_file, parse_chat
from qqmanager.server import bridge_authorized, make_server
from qqmanager.store import Store


class ExtractionTests(unittest.TestCase):
    def test_relative_dates_anchor_to_message_not_import_time(self):
        due, note = due_date('请于明天18:00提交材料', '2024-12-31T09:00:00+08:00')
        self.assertEqual(due, '2025-01-01T18:00:00+08:00')
        self.assertIsNone(due_date('明天18:00提交', None)[0])
        self.assertIsNone(due_date('周五提交', '2026-11-14T09:00:00+08:00')[0])

    def test_explicit_week_and_ambiguous_dates(self):
        self.assertEqual(due_date('下周五晚上8点提交', '2026-11-14T09:00:00+08:00')[0], '2026-11-20T20:00:00+08:00')
        self.assertIsNone(due_date('2026年2月30日 18:00', None)[0])
        self.assertEqual(timestamp('2026/1/2 8:01:02').month, 1)

    def test_rules_and_interests(self):
        self.assertIsNone(analyze('大家中午好'))
        self.assertEqual(analyze('会议延期到明天')['kind'], '通知')
        self.assertIsNone(analyze('今天学 Python', interests='摄影,Python'))
        self.assertIn('Python', analyze('分享 Python 入门教程，包含基础练习', interests='摄影,Python')['reason'])

    def test_qq_multiline_and_both_header_orders(self):
        raw = '消息对象：学习群\n2026-11-14 09:00:00 张三(123)\n通知：提交作业\n附件见下文\n李四 2026/11/14 10:00:00\n收到'
        rows = parse_chat('QQ.txt', raw.encode(), '默认群')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['group_name'], '学习群')
        self.assertIn('\n附件', rows[0]['content'])
        self.assertEqual(rows[1]['sender'], '李四')

    def test_qq_sticker_keeps_the_accompanying_text(self):
        from qqmanager.importers import segments
        from qqmanager.quality import noise_reason
        face = {'type':'mface','data':{}}
        self.assertEqual(segments([{'type':'text','data':{'text':'请大家相互转告'}},face]),'请大家相互转告[表情]')
        self.assertEqual(noise_reason(segments([face,face])),'仅表情')

    def test_json_csv_and_bad_input(self):
        rows = parse_chat('a.json', json.dumps({'messages':[{'text':'报名截止','sender':'张三','time':1794618000}]}).encode(), '群')
        self.assertIsNotNone(rows[0]['sent_at'])
        rows = parse_chat('a.csv', 'content,sender,group_name\n提交材料,小王,工作群'.encode(), '群')
        self.assertEqual(rows[0]['group_name'], '工作群')
        with self.assertRaises(ValueError):
            parse_chat('a.json', b'{not json}', '群')
        with self.assertRaises(ValueError):
            parse_chat('a.json', b'[{"sender":"x"}]', '群')

    def test_utf16_qq_export(self):
        rows = parse_chat('a.txt', '2026-11-14 09:00:00 张三\n通知：提交作业'.encode('utf-16'), '群')
        self.assertEqual(rows[0]['sender'], '张三')
        self.assertEqual(rows[0]['content'], '通知：提交作业')

    def test_docx_runs_preserve_words(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>数据</w:t></w:r><w:r><w:t>分析</w:t></w:r></w:p></w:body></w:document>')
        text, status = extract_file('资料.docx', data.getvalue())
        self.assertEqual(text, '数据分析')

    def test_pdf_text_and_scan_status(self):
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 250 Td (Python analysis resources) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO(); writer.write(buffer)
        self.assertIn('Python analysis', extract_file('a.pdf', buffer.getvalue())[0])
        blank = PdfWriter(); blank.add_blank_page(width=100, height=100)
        buffer = io.BytesIO(); blank.write(buffer)
        self.assertIn('OCR', extract_file('scan.pdf', buffer.getvalue())[1])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_import_dedup_and_persistence(self):
        data = '通知：请提交设计稿'.encode()
        a = self.store.import_data('a.txt', data, '设计群', 'chat')
        b = self.store.import_data('renamed.txt', data, '设计群', 'chat')
        self.assertEqual(a['messages'], 1)
        self.assertEqual(b['duplicates'], 1)
        self.assertEqual(self.store.snapshot()['counts']['tasks'], 0)
        reopened = Store(Path(self.temp.name) / 'db')
        self.assertEqual(reopened.snapshot()['counts']['messages'], 1)
        reopened.db.close()

    def test_unread_follows_original_across_replay_account_changes_and_restart(self):
        store = self.store
        row = dict(group_name='课程群', sender='老师', content='通知：请提交作业',
                   sent_at='2026-11-19T09:00:00+08:00', _live_group='g', _account='A')
        store.set_setting('ws_account', 'A')
        store.add_messages([row, dict(row, _account='B', _backfill=True)], 'QQ')
        store.add_messages([dict(row, content='通知：请核对考试时间', _backfill=True)], 'QQ')
        self.assertEqual(store.library('messages')['days'][0]['unread'], 2)
        self.assertEqual(store.snapshot()['counts']['unread_messages'], 2)
        store.read_backfill([1, 2, 3])
        self.assertEqual(store.snapshot()['counts']['unread_messages'], 1)
        self.assertEqual(store.backfill_receipts(), [])
        store.read_messages([1, 2])
        self.assertEqual(store.add_messages([row], 'QQ')['duplicates'], 1)
        reopened = Store(store.directory)
        try:
            self.assertEqual(reopened.snapshot()['counts']['unread_messages'], 0)
            reopened.set_setting('ws_account', 'B')
            self.assertEqual(reopened.snapshot()['counts']['unread_messages'], 1)
            self.assertEqual(len(reopened.backfill_receipts()), 1)
            reopened.read_messages([2])
            self.assertEqual(reopened.backfill_receipts(), [])
        finally:
            reopened.db.close()

    def test_library_uses_message_time_before_applying_result_limit(self):
        rows = [('recent', '2026-11-18T00:01:00+08:00')]
        rows.extend((f'old-{n}', '2026-11-17T15:59:00Z') for n in range(500))
        rows.append(('undated', None))
        with self.store.db:
            self.store.db.executemany(
                'INSERT INTO messages(content,sent_at,group_name) VALUES (?, ?, "test")', rows)
        found = self.store.search('messages', group='test')
        self.assertEqual(len(found), 500)
        self.assertEqual(found[0]['content'], 'recent')
        self.assertEqual(found[1]['content'], 'old-499')
        self.assertEqual(self.store.search('messages', 'undated')[0]['content'], 'undated')
        archive = self.store.library('messages', group='test', day='2026-11-17', offset=450)
        self.assertEqual([(d['day'], d['count']) for d in archive['days']],
                         [('2026-11-18', 1), ('2026-11-17', 500), ('unknown', 1)])
        self.assertEqual(len(archive['rows']), 50)
        self.assertEqual(archive['rows'][-1]['content'], 'old-0')
        filtered = self.store.library('messages', query='recent', group='test', day='2026-11-18')
        self.assertEqual([d['preview'] for d in filtered['days']], ['recent'])
        self.assertEqual(filtered['rows'][0]['content'], 'recent')
        self.assertEqual(self.store.library('messages', day='unknown')['rows'][0]['content'], 'undated')

    def test_filename_content_search_and_literal_wildcards(self):
        self.store.import_data('教程.md', 'Python 数据分析，完成率100%'.encode(), '技术群', 'file')
        self.store.import_data('照片.jpg', b'not-an-image', '摄影群', 'file')
        self.assertEqual(len(self.store.search('files', 'Python 分析')), 1)
        self.assertEqual(len(self.store.search('files', '%')), 1)
        archive = self.store.library('files', query='%', group='技术群')
        self.assertEqual([(d['preview'], d['count']) for d in archive['days']], [('教程.md', 1)])
        self.assertEqual(len(self.store.search('files', 'Python', '摄影群')), 0)
        self.assertIn('暂不支持', self.store.search('files', '照片')[0]['parse_status'])

    def test_reminder_once_and_completed_tasks(self):
        task_id = self.store.create_task({'title':'交作业', 'due':'2026-01-01T10:00:00+08:00'})
        now = timestamp('2026-01-01T11:00:00+08:00').timestamp()
        self.assertEqual(len(self.store.due_notifications(now)), 1)
        self.assertEqual(len(self.store.due_notifications(now)), 0)
        self.store.update_task(task_id, {'due':'2026-01-01T12:00:00+08:00'})
        self.assertEqual(self.store.snapshot()['notifications'], [])
        self.assertEqual(self.store.due_notifications(now), [])
        self.store.update_task(task_id, {'status':'done'})
        self.assertEqual(self.store.due_notifications(now+10000), [])

    def test_watch_reimports_changed_file_without_duplicate_messages(self):
        folder=Path(self.temp.name)/'inbox';folder.mkdir()
        file=folder/'chat.txt';file.write_text('2026-11-14 09:00:00 张三\n通知：提交报告',encoding='utf-8')
        os.utime(file,(time.time()-5,time.time()-5))
        self.store.add_watch({'path':str(folder),'mode':'chat','group_name':'群'})
        self.store.scan_watches();self.store.scan_watches()
        self.assertEqual(self.store.snapshot()['counts']['messages'],1)
        file.write_text(file.read_text(encoding='utf-8')+'\n2026-11-14 10:00:00 李四\n活动报名开始',encoding='utf-8')
        os.utime(file,(time.time()-5,time.time()-5))
        self.store.scan_watches()
        self.assertEqual(self.store.snapshot()['counts']['messages'],2)

    def test_watch_errors_visible_and_own_storage_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_watch({'path':str(self.store.directory),'mode':'file'})
        folder=Path(self.temp.name)/'inbox';folder.mkdir()
        file=folder/'bad.json';file.write_text('{bad',encoding='utf-8')
        os.utime(file,(time.time()-5,time.time()-5))
        self.store.add_watch({'path':str(folder),'mode':'chat'})
        self.store.scan_watches();self.store.scan_watches()
        self.assertIn('JSON', self.store.snapshot()['watches'][0]['error'])

    def test_onebot_allowlist_segments_and_event_dedup(self):
        self.store.set_setting('bridge_enabled',True)
        self.store.set_setting('bridge_groups',['123'])
        event={'post_type':'message','message_type':'group','group_id':123,'time':1794618000,'message_id':1,'sender':{'nickname':'群友'},'message':[{'type':'text','data':{'text':'通知：提交作业'}},{'type':'image','data':{'url':'http://invalid'}}]}
        self.assertEqual(self.store.onebot(event)['messages'],1)
        self.assertEqual(self.store.onebot(event)['duplicates'],1)
        event['message_id']=2
        self.assertEqual(self.store.onebot(event)['messages'],1)
        event['group_id']=999
        self.assertIn('ignored',self.store.onebot(event))
        self.store.onebot({'post_type':'notice','notice_type':'group_upload','group_id':123,'file':{'id':'f1','name':'课件.pdf','size':123}})
        f=self.store.snapshot()['files'][0]
        self.assertTrue(f['parse_status'].startswith('待获取'))
        self.assertEqual(f['excerpt'],'')

    def test_interests_do_not_promote_chatter_and_reanalysis_keeps_task(self):
        self.store.import_data('a.txt','Python 真有趣'.encode(),'群','chat')
        self.store.set_setting('interests','Python');self.store.reanalyze()
        self.assertEqual(self.store.snapshot()['counts']['insights'],0)
        self.store.import_data('b.txt','请于明天18:00提交 Python 作业'.encode(),'群','chat')
        insight=self.store.snapshot()['insights'][0]
        self.store.create_task({'title':insight['title'],'insight_id':insight['id']})
        self.store.reanalyze()
        self.assertEqual(self.store.snapshot()['counts']['tasks'],1)



class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.server=make_server(self.store,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.csrf=json.load(urllib.request.urlopen(self.url+'/api/session'))['csrf']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.db.close();self.temp.cleanup()

    def request(self,path,data,headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.url+path,json.dumps(data).encode(),dict({'Content-Type':'application/json'},**(headers or {}))))

    def test_import_requires_csrf_and_rejects_foreign_origin(self):
        body={'name':'a.txt','text':'通知：请提交报告','group_name':'工作群','mode':'chat'}
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/import',body)
        self.assertEqual(cm.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/import',body,{'X-CSRF-Token':self.csrf,'Origin':'https://evil.example'})
        self.assertEqual(cm.exception.code,403)
        result=json.load(self.request('/api/import',body,{'X-CSRF-Token':self.csrf}))
        self.assertEqual(result['messages'],1)

    def test_bridge_auth_and_host(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/onebot',{})
        self.assertEqual(cm.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/onebot',{}, {'Host':'attacker.example'})
        self.assertEqual(cm.exception.code,403)
        raw=b'{"a":1}';token='secret';sign='sha1='+hmac.new(token.encode(),raw,hashlib.sha1).hexdigest()
        self.assertTrue(bridge_authorized({'X-Signature':sign},raw,token))
        self.assertFalse(bridge_authorized({'X-Signature':sign},raw+b' ',token))

    def test_native_groups_can_be_saved_and_cleared_without_exposing_pairing_token(self):
        self.store.set_setting('ws_account','777777')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.request('/api/native/groups',{'groups':['123456']})
        self.assertEqual(cm.exception.code,403)
        headers={'X-CSRF-Token':self.csrf}
        result=json.load(self.request('/api/native/groups',{'groups':['123456'],'names':{'123456':'验证群'}},headers))
        self.assertTrue(result['collecting']);self.assertNotIn('token',result)
        result=json.load(self.request('/api/native/groups',{'groups':[]},headers))
        self.assertFalse(result['collecting']);self.assertEqual(self.store.setting('bridge_groups'),[])

    def test_native_pairing_can_fetch_catalog_before_any_groups_are_selected(self):
        result=json.load(self.request('/api/native/pair',{'account':'777777','groups':[]},{'X-CSRF-Token':self.csrf}))
        self.assertEqual(result['groups'],[])
        self.assertEqual(self.store.setting('ws_account'),'777777')
        self.assertFalse(self.store.setting('bridge_enabled'))
        self.assertTrue(self.store.setting('native_enabled'))

    def test_file_download_preserves_bytes(self):
        self.store.import_data('资料.txt','数据分析资料'.encode(),'群','file')
        f=self.store.snapshot()['files'][0]
        response=urllib.request.urlopen(self.url+'/api/download/'+str(f['id']))
        self.assertEqual(response.read(),'数据分析资料'.encode())
        self.assertIn('attachment',response.headers['Content-Disposition'])


if __name__=='__main__':unittest.main()
