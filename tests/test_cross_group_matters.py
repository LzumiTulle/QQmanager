import tempfile
import unittest
from qqmanager.store import Store
from qqmanager import matters, workflow, planner

NOTICE = '学业核查通知：请2024级学生完成本学期学业自查，检查已修课程与学分，并于12月18日前将自查表和相关证明交给学习委员。'


class CrossGroupMattersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name)
        self.s.set_setting('ws_account', '10001')
        self.s.set_setting('bridge_enabled', True)
        self.s.set_setting('bridge_groups', ['20001', '20002'])

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def add(self, group='20001', text=NOTICE, title='学业核查通知', account='10001', day='2026-11-16', file=None):
        with self.s.db:
            mid = self.s.db.execute('INSERT INTO messages(group_name,sender,sent_at,content,source) VALUES (?,?,?,?,?)',
                (group, '老师', day+'T10:00:00+08:00', text, 'test')).lastrowid
            self.s.db.execute("INSERT INTO insights(message_id,title,kind,score) VALUES (?,?,'通知',80)", (mid,title))
            self.s.db.execute("INSERT INTO analysis_jobs(message_id,group_id,account,state) VALUES (?,?,?,'done')", (mid,group,account))
            if file:
                name, digest = file
                fid = self.s.db.execute('INSERT INTO files(digest,name,group_name,body) VALUES (?,?,?,?)', (digest,name,group,'通知原文')).lastrowid
                self.s.db.execute("INSERT INTO attachments(message_id,slot,kind,name,file_id,status) VALUES (?,0,'file',?,?,'ready')", (mid,name,fid))
        return mid

    def mid(self, message):
        matters.sync(self.s)
        return self.s.query('SELECT matter_id FROM matter_members mm JOIN insights i ON i.id=mm.insight_id WHERE i.message_id=?',(message,))[0]['matter_id']

    def flow(self, mid):
        return planner.snapshot(self.s, '2026-11-18')['workflow']['matters'][str(mid)]

    def test_shared_document_merges_and_preserves_every_source(self):
        first=self.add(text='[文件：正式核查通知.docx]',file=('正式核查通知.docx','same-document'))
        one=self.mid(first)
        second=self.add('20002',text='[文件：正式核查通知.docx]',file=('正式核查通知.docx','same-document'))
        self.assertEqual(self.mid(second),one)
        detail=matters.detail(self.s,one)
        self.assertEqual(detail['message_count'],2)
        self.assertEqual(detail['groups'],['20001','20002'])
        self.assertEqual({a['group_name'] for a in detail['attachments']},{'20001','20002'})
        self.assertEqual(len(detail['attachments']),2)
        for group in detail['groups']:
            rows=matters.list_matters(self.s,group=group)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['id'],one)
            self.assertEqual(rows[0]['message_count'],2)
        old=self.s.query('SELECT source_id FROM matter_aliases')[0]['source_id']
        self.assertEqual(matters.detail(self.s,old)['id'],one)
        tid=self.s.create_task(dict(matter_id=old,revision=workflow.revision(self.s,one),title='提交材料'))
        self.assertEqual(self.s.create_task(dict(matter_id=one,revision=workflow.revision(self.s,one),title='提交材料')),tid)
        self.assertEqual(self.s.snapshot()['tasks'][0]['groups'],['20001','20002'])
        self.assertEqual(self.flow(one)['state'],'scheduled')
        self.s.update_task(tid,dict(status='done',matter_id=old,revision=workflow.revision(self.s,one)))
        self.assertEqual(self.flow(one)['state'],'done')
        self.s.db.close();self.s=Store(self.tmp.name)
        self.assertEqual(len(matters.list_matters(self.s)),1)
        self.assertEqual(self.flow(one)['state'],'done')

    def test_repost_does_not_reopen_but_followup_does(self):
        one=self.mid(self.add())
        workflow.decide(self.s,dict(id=one,revision=workflow.revision(self.s,one),decision='acknowledged'))
        two=self.mid(self.add('20002',day='2026-11-17'))
        self.assertEqual(one,two)
        self.assertEqual(self.flow(one)['state'],'acknowledged')
        self.mid(self.add('20002',text='补充：学业核查需提交第二份证明',day='2026-11-18'))
        self.assertEqual(len(matters.list_matters(self.s)),1)
        self.assertEqual(self.flow(one)['state'],'changed')
        old=self.s.query('SELECT source_id FROM matter_aliases')[0]['source_id']
        workflow.decide(self.s,dict(id=old,revision=workflow.revision(self.s,one),decision='reviewed'))
        self.assertEqual(self.flow(one)['state'],'acknowledged')

    def test_generic_title_and_template_are_not_identity(self):
        self.mid(self.add(text='学业核查通知',file=('学业自查表.docx','template')))
        self.mid(self.add('20002',text='学业核查通知',file=('学业自查表.docx','template')))
        self.assertEqual(len(matters.list_matters(self.s)),2)

    def test_separate_accounts_weeks_and_rounds(self):
        self.mid(self.add())
        self.mid(self.add('20002',account='99999'))
        self.mid(self.add('20002',day='2026-12-16'))
        self.assertEqual(len(matters.list_matters(self.s)),3)
        self.mid(self.add(title='第12周考试',text=NOTICE+'考试安排'))
        self.mid(self.add('20002',title='第13周考试',text=NOTICE+'考试安排'))
        self.assertEqual(len(matters.list_matters(self.s)),5)

    def test_relative_dates_and_manual_split_remain_separate(self):
        self.mid(self.add(text=NOTICE+'今天提交'))
        self.mid(self.add('20002',text=NOTICE+'今天提交',day='2026-11-17'))
        self.assertEqual(len(matters.list_matters(self.s)),2)
        rows=matters.list_matters(self.s)
        matters.update(self.s,dict(id=rows[0]['id'],merge_into=rows[1]['id']))
        joined=matters.list_matters(self.s)[0]
        iid=matters.detail(self.s,joined['id'])['items'][0]['id']
        matters.update(self.s,dict(id=joined['id'],split_insight=iid))
        self.assertEqual(len(matters.list_matters(self.s)),2)

    def test_revision_migration_keeps_unseen_updates_unconfirmed(self):
        one=self.mid(self.add())
        with self.s.db:
            self.s.db.execute('INSERT INTO workflow_decisions VALUES (?,?,?,0)',(one,workflow.legacy_revision(self.s,one),'acknowledged'))
        self.s.set_setting('workflow_content_revision_v2',False)
        workflow.migrate(self.s)
        self.assertEqual(self.flow(one)['state'],'acknowledged')
        with self.s.db:
            self.s.db.execute('UPDATE workflow_decisions SET revision=?',(workflow.legacy_revision(self.s,one),))
        self.mid(self.add(text='学业核查新增材料要求'))
        self.s.set_setting('workflow_content_revision_v2',False)
        workflow.migrate(self.s)
        self.assertEqual(self.flow(one)['state'],'changed')
