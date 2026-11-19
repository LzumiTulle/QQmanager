import tempfile
import unittest

from qqmanager import identity, llm, planner, exams
from qqmanager.store import Store


class LocalIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Store(tmp.name)
        self.addCleanup(self.s.db.close)
        self.s.set_setting('ws_account','42')

    def save(self, **fields):
        p = dict(account='42',grade='2024',major='软件工程',class_name='软件1班')
        return identity.save_profile(self.s, dict(p,**fields))

    def test_only_academic_fields_persist(self):
        p=self.save(roles=['班长'], league=True, party=False)
        self.assertEqual(set(p), {'grade','major','class_name'})
        self.assertEqual(p, self.s.setting('identity:42'))
        self.assertEqual(set(self.s.snapshot()['identity']), set(p)|{'account'})

    def test_exam_audience_accepts_academic_scopes_and_preserves_unknowns(self):
        p=self.save(class_name='B软件241')
        for text in ('2024级软件工程专业','软件工程专业','2024级','2024级软件工程专业B软件241','B软件241','全体同学','B软件241、B软件242'):
            self.assertTrue(exams.academic_audience_matches(text,p),text)
        for text in ('2025级','2024级建筑学专业','2024级软件工程专业B软件242','B软件242'):
            self.assertFalse(exams.academic_audience_matches(text,p),text)
        self.assertTrue(exams.academic_audience_matches('2024级软件工程专业',self.save(grade='',major='')))

    def test_mixed_notice_stays_together_and_only_general_action_is_suggested(self):
        self.save()
        self.s.set_setting('bridge_enabled',True)
        self.s.set_setting('bridge_groups',['12'])
        texts=['全体同学参加班会；团员另交材料；党员填写登记表','宿舍长收集材料']
        for i,text in enumerate(texts,1):
            self.s.onebot(dict(post_type='message',message_type='group',group_id=12,self_id=42,message_id=i,
                               sender={'nickname':'通知'},time=1794963600+i,raw_message=text))
        worker=llm.Analyzer(self.s,lambda *a:self.fail('unexpected AI request'))
        self.assertTrue(worker.step())
        self.assertTrue(worker.step())
        flow=planner.snapshot(self.s,'2026-11-18')['workflow']['matters']
        self.assertEqual([m['personal_title'] for m in flow.values() if m['candidate']],['全体同学参加班会'])
        self.assertEqual(len(flow),2)
        self.assertTrue(all('review_clauses' not in m for m in flow.values()))
        self.assertFalse(self.s.snapshot()['tasks'])

    def test_existing_explicit_task_keeps_its_state_and_source_after_upgrade(self):
        old=dict(self.save(),roles=['宿舍长'],league=True)
        for owner in ('42','43'):
            self.s.set_setting('identity:'+owner,old)
        self.s.add_messages([dict(content='请在明天提交报告',group_name='班群',sender='老师',sent_at='2026-11-18')],'test')
        src=self.s.query('SELECT id FROM insights')[0]['id']
        with self.s.db:
            task=self.s.db.execute("INSERT INTO tasks(title,status,created) VALUES ('收集报告','done',1)").lastrowid
            self.s.db.execute('INSERT INTO identity_answers VALUES (?,?,?,?,?)',('42','old-clause','old-profile',src,task))
        reopened=Store(self.s.directory)
        self.addCleanup(reopened.db.close)
        for owner in ('42','43'):
            self.assertEqual(set(reopened.setting('identity:'+owner)),set(identity.ACADEMIC_FIELDS))
        flow=planner.snapshot(reopened,'2026-11-18')['workflow']['matters']
        entry=next(iter(flow.values()))
        self.assertFalse(entry['candidate'])
        self.assertEqual(entry['state'],'done')
        self.assertEqual([t['id'] for t in entry['tasks']],[task])
        reopened.update_task(task,dict(matter_id=entry['id'],revision=entry['revision'],status='open'))
        entry=planner.snapshot(reopened,'2026-11-18')['workflow']['matters'][str(entry['id'])]
        self.assertEqual(entry['label'],'待安排')
        self.assertEqual(llm.calls_today(reopened),0)
