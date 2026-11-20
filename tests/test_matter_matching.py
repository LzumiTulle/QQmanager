import tempfile
import unittest

from qqmanager import matters, planner, workflow
from qqmanager.llm import Analyzer, signature
from qqmanager.store import Store


class AutomaticMatterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Store(tmp.name)
        self.addCleanup(self.s.db.close)
        planner.save_config(self.s, dict(anchor_date='2026-11-16', anchor_week=1))
        for title in ('编译原理','计算机网络'):
            planner.save_course(self.s, dict(title=title, location='机房', weeks=[1,2],
                                             weekday=5, start_period=3, end_period=4))

    def add(self, text, when='2026-11-20T10:05:00+08:00', group='班群', sender='老师', live=False):
        msg = dict(content=text, sent_at=when, sender=sender, group_name=group)
        if live:
            msg.update(_live_group='12345', _account='67890')
        self.s.add_messages([msg], 'test')
        return self.s.query('SELECT max(id) id FROM messages')[0]['id']

    def containing(self, msg):
        return next(m for m in matters.list_matters(self.s) if msg in m['source_message_ids'])

    def test_live_lab_correction_needs_no_model_or_manual_confirmation(self):
        self.s.set_setting('bridge_enabled',True)
        self.s.set_setting('bridge_groups',['12345'])
        self.s.set_setting('ws_account','67890')
        ids = [self.add('本次编译原理实验做C4',live=True),
               self.add('更正：是C4+C5，不是只有C4','2026-11-20T10:07:00+08:00',live=True)]
        analyzer = Analyzer(self.s)
        while analyzer.step():
            pass
        result = planner.snapshot(self.s,'2026-11-20')
        flow = self.containing(ids[0])
        self.assertEqual(flow['source_message_ids'],ids)
        self.assertEqual(result['workflow']['matters'][str(flow['id'])]['state'],'course')
        lessons = [c for d in result['days'] for c in d['courses'] if c['notices']]
        self.assertEqual(len(lessons),1)
        self.assertEqual([n['id'] for n in lessons[0]['notices']],ids[::-1])
        self.assertFalse(self.s.query('SELECT * FROM tasks'))
        self.assertFalse(self.s.query('SELECT * FROM model_calls'))
        next_lab = self.add('本次编译原理实验做C4','2026-11-27T10:05:00+08:00',live=True)
        self.assertNotEqual(flow['id'],self.containing(next_lab)['id'])

    def test_short_correction_cannot_cross_intervening_chat_sender_or_group(self):
        self.add('本次编译原理实验做C4')
        self.add('无关聊天','2026-11-20T10:06:00+08:00')
        self.add('补充：C4+C5','2026-11-20T10:08:00+08:00')
        self.add('更正：C4+C6','2026-11-20T10:06:00+08:00',group='其他群')
        self.add('补充：C5','2026-11-20T10:05:30+08:00',sender='其他人')
        result = planner.snapshot(self.s,'2026-11-20')
        self.assertEqual([n['id'] for d in result['days'] for c in d['courses'] for n in c['notices']], [1])

    def test_numbered_work_persists_through_history_review_and_manual_split(self):
        a = self.add('请提交编译原理作业四')
        original = self.containing(a)
        tid = self.s.create_task(dict(matter_id=original['id'],revision=workflow.revision(self.s,original['id']),
                                      title='我的作业',deadline='2026-11-23T20:00'))
        b = self.add('通知：编译原理第4次作业提交地点改为办公室')
        c = self.add('请提交编译原理作业五')
        d = self.add('请提交计算机网络作业四')
        self.s.reanalyze(full=True)
        combined = self.containing(b)
        self.assertEqual((combined['id'],combined['source_message_ids']), (original['id'],[a,b]))
        self.assertEqual(workflow.linked_tasks(self.s,combined['id'])[0]['id'],tid)
        self.assertNotEqual(self.containing(c)['id'],combined['id'])
        self.assertNotEqual(self.containing(d)['id'],combined['id'])
        detail = matters.detail(self.s,combined['id'])
        child = matters.update(self.s, dict(id=combined['id'],split_insight=next(i['id'] for i in detail['items'] if i['message_id']==b)))['id']
        self.s.reanalyze(full=True)
        self.assertEqual(self.containing(b)['id'],child)
        self.assertEqual(self.containing(a)['source_message_ids'],[a])

    def test_repost_keeps_acknowledgment_and_new_document_requirement_reopens(self):
        a = self.add('请大家提交本学期读书笔记')
        m = self.containing(a)
        workflow.decide(self.s,dict(id=m['id'],revision=workflow.revision(self.s,m['id']),decision='acknowledged'))
        b = self.add('再次提醒：请提交本学期读书笔记，谢谢大家！')
        self.assertEqual(self.containing(b)['id'],m['id'])
        self.assertEqual(planner.snapshot(self.s)['workflow']['matters'][str(m['id'])]['state'],'acknowledged')
        a = self.add('请提交《学生实践活动报名表》')
        m = self.containing(a)
        workflow.decide(self.s,dict(id=m['id'],revision=workflow.revision(self.s,m['id']),decision='acknowledged'))
        b = self.add('请填写《学生实践活动报名表》并附证明')
        self.assertEqual(self.containing(b)['id'],m['id'])
        self.assertEqual(planner.snapshot(self.s)['workflow']['matters'][str(m['id'])]['state'],'changed')

    def test_explicit_followup_inherits_one_preceding_deliverable(self):
        a = self.add('请提交编译原理作业四')
        b = self.add('这份作业改交到右边第一排','2026-11-20T10:10:00+08:00')
        self.assertEqual(self.containing(b)['source_message_ids'],[a,b])
        c = self.add('这份作业改交到办公室','2026-11-20T13:10:00+08:00')
        self.assertNotEqual(self.containing(c)['id'],self.containing(a)['id'])

    def test_generic_extracted_titles_do_not_merge_unrelated_originals(self):
        a = self.add('请提交本学期实践报告')
        b = self.add('请提交军训报名材料')
        with self.s.db:
            self.s.db.execute("UPDATE insights SET title='提交材料'")
        self.assertNotEqual(self.containing(a)['id'],self.containing(b)['id'])

    def test_model_suggestion_cannot_join_different_assignment_numbers(self):
        self.s.set_setting('bridge_enabled',True)
        self.s.set_setting('bridge_groups',['12345'])
        self.s.set_setting('ws_account','67890')
        self.add('请提交编译原理作业四',live=True)
        self.add('请提交编译原理作业五',live=True)
        ids = [r['id'] for r in self.s.query('SELECT id FROM insights')]
        cfg = dict(self.s.setting('llm_config'),enabled=True,model='test',url='https://example.invalid')
        self.s.set_setting('llm_config',cfg)
        self.s.set_setting('llm_test',signature(cfg))
        matters.organize_step(self.s,lambda *_: {'groups':[dict(ids=ids,title='作业')]})
        self.assertEqual(len(matters.list_matters(self.s)),2)
