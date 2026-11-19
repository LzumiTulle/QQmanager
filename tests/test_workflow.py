import unittest
from tests import test_exams_overview as exam_fixture
from qqmanager import exams,workflow,matters,planner
from qqmanager.store import Store

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=exam_fixture.ExamOverviewTests();self.fixture.setUp();self.s=self.fixture.s
    def tearDown(self):self.fixture.s=self.s;self.fixture.tearDown()
    def flow(self):return planner.snapshot(self.s,'2026-11-18')['workflow']
    def generic(self,text='材料提交通知，请提交学业核查材料'):
        mid=self.fixture.add(text)
        matters.sync(self.s)
        return self.s.query('SELECT mm.matter_id FROM matter_members mm JOIN insights i ON i.id=mm.insight_id WHERE i.message_id=?',(mid,))[0]['matter_id']
    def task(self,mid):
        return self.s.create_task(dict(matter_id=mid,revision=workflow.revision(self.s,mid),title='提交材料',due='2026-11-20T12:00:00+08:00'))
    def entry(self,mid):return self.flow()['matters'][str(mid)]
    def new_notice(self,mid):
        new=self.generic('材料提交补充通知，请补充第二份证明')
        if new!=mid:matters.update(self.s,dict(id=new,merge_into=mid))
    def test_decision_task_completion_and_followup_share_one_state(self):
        mid=self.generic();self.assertEqual(self.entry(mid)['state'],'confirm')
        tid=self.task(mid);self.assertEqual(self.task(mid),tid)
        self.assertEqual(self.entry(mid)['state'],'scheduled')
        self.assertEqual(self.entry(mid)['tasks'][0]['id'],tid)
        self.s.update_task(tid,dict(status='done'));self.assertEqual(self.entry(mid)['state'],'done')
        self.new_notice(mid);self.assertEqual(self.entry(mid)['state'],'changed')
        self.assertEqual(self.entry(mid)['tasks'][0]['status'],'done')
        workflow.decide(self.s,dict(id=mid,revision=workflow.revision(self.s,mid),decision='reviewed'))
        self.assertEqual(self.entry(mid)['state'],'done')
        self.s.update_task(tid,dict(status='open'));self.assertEqual(self.entry(mid)['state'],'scheduled')
    def test_read_is_not_confirmation_and_stale_decisions_are_rejected(self):
        mid=self.generic();matters.update(self.s,dict(id=mid,status='read'))
        self.assertEqual(self.entry(mid)['state'],'confirm')
        rev=workflow.revision(self.s,mid)
        workflow.decide(self.s,dict(id=mid,revision=rev,decision='acknowledged'))
        self.assertEqual(self.entry(mid)['state'],'acknowledged')
        self.new_notice(mid)
        with self.assertRaises(ValueError):workflow.decide(self.s,dict(id=mid,revision=rev,decision='reviewed'))
        with self.assertRaises(ValueError):self.s.create_task(dict(matter_id=mid,revision=rev,title='旧表单'))
        self.assertEqual(self.entry(mid)['state'],'changed')
    def test_new_exam_facts_update_existing_task_only_after_confirmation(self):
        exams.recognize_step(self.s,lambda *a,**k:self.fixture.result())
        first=self.fixture.confirm_exam();old=exams.snapshot(self.s)['events'][0];mid=old['matter_id']
        self.fixture.add('第13周考试补充：编译原理改为12月3日')
        self.assertEqual(self.entry(mid)['state'],'changed')
        self.assertEqual(self.entry(mid)['tasks'][0]['id'],first['id'])
        result=self.fixture.result();result['exams'][0]['date']='2026-12-03'
        exams.recognize_step(self.s,lambda *a,**k:result)
        candidate=exams.snapshot(self.s)['events'][0]
        self.assertEqual(candidate['previous_task']['id'],first['id'])
        self.assertEqual(self.s.query('SELECT due FROM tasks')[0]['due'],'2026-12-02T13:40:00+08:00')
        saved=self.fixture.confirm_exam(due='2026-12-03T13:40:00+08:00')
        self.assertEqual(saved['id'],first['id']);self.assertEqual(len(self.s.query('SELECT * FROM tasks')),1)
        self.assertEqual(self.entry(mid)['state'],'scheduled')
        self.assertEqual(self.entry(mid)['tasks'][0]['due'],'2026-12-03T13:40:00+08:00')
        self.assertIsNone(exams.linked_task(self.s,old))
    def test_duplicate_merge_suppresses_counts_and_reminders_and_can_restore(self):
        exams.recognize_step(self.s,lambda *a,**k:self.fixture.result())
        first=self.fixture.confirm_exam();e=exams.snapshot(self.s)['events'][0]
        for _ in range(2):self.s.create_task(dict(title=exams.task_title(e),due='2026-12-02T13:40:00+08:00'))
        merged=workflow.merge_exam_duplicates(self.s);self.assertEqual(len(merged),2)
        self.assertEqual(workflow.merge_exam_duplicates(self.s),[])
        snap=self.s.snapshot();self.assertEqual(snap['counts']['tasks'],1);self.assertEqual(len(snap['tasks']),1)
        self.assertEqual(len(self.s.query('SELECT * FROM tasks')),3)
        self.assertEqual(len(self.s.due_notifications(now=2_000_000_000)),1)
        self.s.db.close();self.s=Store(self.fixture.tmp.name);self.fixture.s=self.s
        self.assertEqual(self.s.snapshot()['counts']['tasks'],1)
        workflow.restore_merge(self.s,merged[0]);self.assertEqual(self.s.snapshot()['counts']['tasks'],2)
    def test_distinct_personal_reminders_are_not_merged(self):
        exams.recognize_step(self.s,lambda *a,**k:self.fixture.result())
        self.fixture.confirm_exam();e=exams.snapshot(self.s)['events'][0]
        self.s.create_task(dict(title=exams.task_title(e),due='2026-12-02T12:40:00+08:00'))
        self.assertEqual(workflow.merge_exam_duplicates(self.s),[])
