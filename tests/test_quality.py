import json
import tempfile
import unittest
from pathlib import Path
from qqmanager.analysis import analyze
from qqmanager.quality import noise_reason
from qqmanager.llm import Analyzer, save_config, test_connection, queue_review, validate_result
from qqmanager.store import Store

class QualityTests(unittest.TestCase):
    def test_noise_even_with_times_keywords_and_interests(self):
        for text in ['@助手 /今日运势','您今天的运势为\n财运：74\n桃花运：55\n事业运：89',
                     '抽卡结果：SSR卡片，明天继续签到','[图片：未解析]','晚安','明天下午两点睡觉',
                     '某平台送大会员了','12点刚开现在领的太多了','领不到']:
            with self.subTest(text=text):
                self.assertTrue(noise_reason(text))
                self.assertIsNone(analyze(text,'2026-11-15T09:00:00+08:00',text))
        for text in ['今天学Python真有趣','怎么提交啊','分享一下','https://example.test','作业好多啊',
                     '我提交作业了','提交作业了','刚刚填写好了表格']:
            self.assertIsNone(analyze(text))

    def test_real_actions_and_resources_are_not_overfiltered(self):
        for text,kind in [('通知：明天10点在A会议室开会','通知'),('今天3点公开赛','通知'),
                          ('会议延期到明天','通知'),('请参赛同学明天18:00前提交报名表','待办'),
                          ('分享数据分析教程：https://example.test/guide','资料'),
                          ('解决方法：导入 CSV 时选择 UTF-8 编码','资料'),
                          ('通知：运势机器人明天进行维护，请更新配置','通知')]:
            with self.subTest(text=text):self.assertEqual(analyze(text)['kind'],kind)
        self.assertEqual(noise_reason('学生认证会员领取方式：打开软件，进入活动页点击领取。'), '')

    def test_model_cannot_resurrect_fortune_quote_in_mixed_message(self):
        quote='财运：74\n桃花运：55\n事业运：89'
        text=quote+'\n通知：请同学们明天18点提交报名表'
        result={'items':[{'kind':'通知','title':'运势通知','reason':'有数字','evidence':[{'message_id':1,'quote':quote}]}]}
        self.assertEqual(validate_result(result,[{'id':1,'content':text,'sent_at':None}],1),[])

class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name))
        self.s.set_setting('bridge_enabled',True);self.s.set_setting('bridge_groups',['123','456']);self.s.set_setting('ws_account','7')
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def emit(self,num,text,group=123,at=1794704400):
        self.s.onebot({'post_type':'message','message_type':'group','self_id':7,'group_id':group,'message_id':num,
                      'time':at,'sender':{'nickname':'群友'+str(num)},'raw_message':text})
    def test_reposts_group_and_keep_all_evidence_and_search(self):
        first='通知：明天10点在A会议室开会。'
        self.emit(1,first);self.emit(2,'再次提醒：明天10点在A会议室开会，谢谢大家。')
        rows=self.s.search('insights');self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['merged_count'],2)
        self.assertEqual(rows[0]['merged_sources'][0]['content'].startswith('再次提醒'),True)
        self.assertEqual(self.s.snapshot()['counts']['messages'],2)
        self.assertEqual(self.s.snapshot()['counts']['insights'],1)
        self.assertEqual(len(self.s.search('insights','群友2')),1)
        self.s.update_insight(rows[0]['id'],'saved')
        self.assertTrue(all(x['status']=='saved' for x in self.s.query('SELECT status FROM insights')))
        self.s.reanalyze();self.assertEqual(self.s.search('insights')[0]['merged_count'],2)
    def test_changes_groups_and_recurring_dates_are_not_merged(self):
        texts=['通知：明天10点在A会议室开会。','通知：明天11点在A会议室开会。',
               '通知：明天10点在B会议室开会。','通知：明天10点在A会议室开会取消。']
        for n,t in enumerate(texts):self.emit(n+1,t)
        self.emit(5,texts[0],group=456);self.emit(6,texts[0],at=1794790800)
        self.assertEqual(len(self.s.search('insights')),6)
    def enable(self,caller):
        save_config(self.s,{'url':'https://example.test','model':'fake','key':'test-only'})
        test_connection(self.s,caller=caller)
    def good(self,config,messages,target,interests=''):
        row=next(m for m in messages if m['id']==target)
        return {'items':[{'kind':'通知','title':'明天10点在A会议室开会','reason':'具体会议安排',
                          'evidence':[{'message_id':target,'quote':row['content']}]}]}
    def test_noise_and_verified_reposts_do_not_spend_model_calls(self):
        self.enable(self.good);self.emit(1,'通知：明天10点在A会议室开会。')
        Analyzer(self.s,self.good).step()
        calls=self.s.query('SELECT count(*) n FROM model_calls')[0]['n']
        self.emit(2,'@助手 /今日运势');self.emit(3,'再次提醒：明天10点在A会议室开会，谢谢大家。')
        def forbidden(*args):self.fail('noise and identical reposts must not invoke provider')
        worker=Analyzer(self.s,forbidden);worker.step();worker.step()
        self.assertEqual(self.s.query('SELECT count(*) n FROM model_calls')[0]['n'],calls)
        self.assertEqual(self.s.search('messages')[1]['analysis_state'],'filtered')
        rows=self.s.search('insights');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['merged_count'],2)
        self.assertEqual(self.s.query('PRAGMA foreign_key_check'),[])
    def test_full_review_recovers_old_omissions_and_protects_human_decisions(self):
        self.emit(1,'请提交作业一')
        self.emit(2,'请提交作业二')
        self.emit(3,'请提交作业三')
        ids=[r['id'] for r in self.s.query('SELECT id FROM messages ORDER BY id')]
        kept=self.s.query('SELECT id FROM insights WHERE message_id=?',(ids[1],))[0]['id']
        self.s.create_task(dict(title='我改过的标题',insight_id=kept))
        with self.s.db:
            self.s.db.execute("UPDATE analysis_jobs SET state='done',policy_version=999,finished=1")
            self.s.db.execute("UPDATE insights SET quality_reason='旧规则漏报' WHERE message_id!=?",(ids[1],))
            self.s.db.execute("UPDATE analysis_jobs SET account='other' WHERE message_id=?",(ids[2],))
        self.assertEqual(self.s.reanalyze(full=True),{'reviewed':2,'queued':1})
        self.assertEqual(self.s.reanalyze(full=True)['queued'],0)
        self.assertEqual([r['title'] for r in self.s.snapshot()['tasks']],['我改过的标题'])
        self.assertEqual([r['message_id'] for r in self.s.query("SELECT message_id FROM insights WHERE quality_reason='' ORDER BY message_id")],ids[:2])

    def test_review_progress_tracks_only_this_batch_and_survives_restart(self):
        import threading
        import time
        from unittest.mock import patch
        from qqmanager.review import HistoryReview
        self.emit(1,'请提交作业')
        self.emit(2,'请提交报告')
        review=HistoryReview(self.s)
        entered,release=threading.Event(),threading.Event()
        original=self.s.reanalyze
        def paused(**kwargs):
            kwargs['progress'](1,2)
            entered.set()
            release.wait(3)
            return original(**kwargs)
        with patch.object(self.s,'reanalyze',side_effect=paused):
            first=review.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(review.start()['run'],first['run'])
                self.assertEqual(review.status()['reviewed'],1)
            finally:
                release.set()
            end=time.monotonic()+3
            while review.status()['phase']=='scanning' and time.monotonic()<end:
                time.sleep(.01)
        self.emit(3,'另一个任务')
        self.assertEqual(review.status()['queued'],2)
        with self.s.db:
            self.s.db.execute("UPDATE analysis_jobs SET filter_reason='等待模型启用' WHERE review_run IS NOT NULL")
        self.assertTrue(review.status()['waiting_only'])
        with self.s.db:
            self.s.db.execute("UPDATE analysis_jobs SET state=CASE WHEN message_id=1 THEN 'done' ELSE 'failed' END WHERE review_run IS NOT NULL")
        final=review.status()
        self.assertEqual((final['phase'],final['processed'],final['failed']),('finished',2,1))
        self.assertEqual(HistoryReview(self.s).status(),final)

    def test_review_retires_stale_results_but_preserves_raw_and_tasks(self):
        self.enable(self.good);self.emit(1,'通知：明天10点在A会议室开会，具体议程群内商量。')
        worker=Analyzer(self.s,self.good);worker.step()
        original=self.s.search('messages')
        self.s.db.execute('UPDATE analysis_jobs SET policy_version=0');self.s.db.commit()
        self.assertEqual(queue_review(self.s)['queued'],1)
        Analyzer(self.s,lambda *args:{'items':[]}).step()
        self.assertEqual(self.s.search('insights'),[])
        self.assertEqual(self.s.search('messages')[0]['content'],original[0]['content'])
        self.assertEqual(self.s.query('PRAGMA foreign_key_check'),[])
        self.assertEqual(queue_review(self.s)['queued'],0)
