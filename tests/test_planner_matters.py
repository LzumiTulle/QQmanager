import json
import tempfile
import unittest
from datetime import date
from qqmanager.store import Store
from qqmanager import planner,matters

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.s.set_setting('academic_calendar',planner.validate_config(dict(anchor_date='2026-11-16',anchor_week=11,total_weeks=19)))
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def test_anchor_and_week13(self):
        c=planner.config(self.s)
        self.assertEqual(planner.week_one(c),date(2026,9,7))
        self.assertEqual(planner.school_week(c,date(2026,11,30)),13)
        self.assertEqual(planner.school_week(c,date(2026,11,22)),11)
    def test_week_ranges_enabled_conflicts_and_unscheduled(self):
        self.assertEqual(planner.weeks('3-9单'),[3,5,7,9])
        self.assertEqual(planner.weeks('2-8双'),[2,4,6,8])
        for value in ('0-8','9-3','abc','61'):
            with self.assertRaises(ValueError):planner.weeks(value)
        base=dict(title='编译原理',weekday=1,start_period=5,end_period=6,weeks='3-12')
        a=planner.save_course(self.s,base)
        planner.save_course(self.s,dict(base,title='个人选修课'))
        planner.save_course(self.s,dict(base,title='课程设计',weeks='15'))
        planner.save_course(self.s,dict(base,title='待安排课程',weekday=0,start_period=0,end_period=0))
        p=planner.snapshot(self.s,'2026-11-16')
        self.assertEqual(len(p['days'][0]['courses']),2)
        self.assertTrue(all(x['conflict'] for x in p['days'][0]['courses']))
        self.assertEqual(len(p['unscheduled']),1)
        planner.save_course(self.s,dict(base,id=a,enabled=False))
        self.assertEqual(len(planner.snapshot(self.s,'2026-11-16')['days'][0]['courses']),1)
        self.assertEqual(len(planner.snapshot(self.s,'2026-12-14')['days'][0]['courses']),1)
    def add(self,title,week=13,group='班级',when='2026-11-16T15:00:00+08:00',kind='通知'):
        self.s.add_messages([dict(content=title,group_name=group,sender='老师',sent_at=when)],'test')
        mid=self.s.query('SELECT max(id) id FROM messages')[0]['id']
        with self.s.db:
            self.s.db.execute('DELETE FROM insights WHERE message_id=?',(mid,))
            iid=self.s.db.execute('INSERT INTO insights(message_id,title,kind,score) VALUES (?,?,?,75)',(mid,title,kind)).lastrowid
        return iid,mid
    def test_week_merge_detail_new_updates_and_group_boundaries(self):
        a,mid=self.add('第13周考试安排见图')
        b,_=self.add('第13周重修考试补充说明',when='2026-11-17T15:00:00+08:00')
        self.add('第12周考试安排变更')
        self.add('第13周考试安排见图',group='另一个班')
        es=matters.list_matters(self.s)
        self.assertEqual(len(es),3)
        e=next(x for x in es if x['week']==13 and x['group_name']=='班级')
        self.assertEqual(e['message_count'],2)
        self.assertEqual(e['week_start'],'2026-11-30')
        self.assertEqual(len(matters.detail(self.s,e['id'])['messages']),2)
        matters.update(self.s,dict(id=e['id'],status='read'))
        self.add('第13周考试提醒带证件',when='2026-11-18T15:00:00+08:00')
        e=next(x for x in matters.list_matters(self.s) if x['id']==e['id'])
        self.assertEqual(e['status'],'new');self.assertEqual(e['message_count'],3)
        matters.update(self.s,dict(id=e['id'],split_insight=a))
        self.assertEqual(len(matters.list_matters(self.s)),4)
        separate=next(x for x in matters.list_matters(self.s) if x['title']=='第13周考试安排见图' and x['group_name']=='班级')
        matters.update(self.s,dict(id=separate['id'],merge_into=e['id']))
        self.assertEqual(len(matters.list_matters(self.s)),3)
        other=next(x for x in es if x['group_name']=='另一个班')
        with self.assertRaises(ValueError):matters.update(self.s,dict(id=e['id'],merge_into=other['id']))
    def test_academic_topic_merges_documents_and_tasks(self):
        self.add('关于开展学生学业完成情况核查工作的通知',kind='资料')
        self.add('学习委员收集学位自查材料',kind='待办')
        es=matters.list_matters(self.s)
        self.assertEqual(len(es),1);self.assertEqual(es[0]['item_count'],2)
    def test_image_week_heading_dates_and_favorites(self):
        a,mid=self.add('跟班重修名单：汇编语言程序设计')
        with self.s.db:
            self.s.db.execute('UPDATE insights SET evidence=? WHERE id=?',(json.dumps([{'message_id':mid,'content':'信息学院第13周考试安排\n跟班重修名单'}]),a))
        self.add('第13周考试安排（12月2日）')
        e=matters.list_matters(self.s)[0]
        self.assertEqual(e['week'],13);self.assertEqual(e['item_count'],2)
        self.assertFalse(e['date_conflict'])
        self.assertEqual(e['dates'],['2026-12-02'])
        matters.update(self.s,dict(id=e['id'],status='saved'))
        matters.update(self.s,dict(id=e['id'],status='read'))
        self.assertTrue(matters.list_matters(self.s)[0]['saved'])
        self.assertEqual(matters.mentioned_dates(dict(title='1-2节上课',sent_at='2026-11-16T09:00:00+08:00')),[])
    def test_relative_days_and_multiweek_markers(self):
        self.add('第11周和第12周周三课程调至对应周周二')
        e=matters.list_matters(self.s)[0]
        self.assertEqual(e['week_starts'],['2026-11-16','2026-11-23'])
        self.assertEqual(e['dates'],[])
        self.assertEqual(matters.mentioned_dates(dict(title='明天和下周二早八上课',sent_at='2026-11-16T21:00:00+08:00')),['2026-11-17','2026-11-24'])
    def test_semantic_grouping_cannot_cross_exam_weeks(self):
        from qqmanager.llm import signature
        a,_=self.add('第12周考试通知');b,_=self.add('第13周考试通知');c,_=self.add('考试补充说明')
        with self.s.db:
            for r in self.s.query('SELECT id FROM messages'):
                self.s.db.execute("INSERT INTO analysis_jobs(message_id,group_id,account,state) VALUES (?,'12345','67890','done')",(r['id'],))
        self.s.set_setting('bridge_groups',['12345']);self.s.set_setting('bridge_enabled',True);self.s.set_setting('ws_account','67890')
        cfg=self.s.setting('llm_config');cfg.update(enabled=True,url='https://example.invalid',model='test');self.s.set_setting('llm_config',cfg);self.s.set_setting('llm_test',signature(cfg))
        matters.organize_step(self.s,lambda *_args,**_kwargs:{'groups':[{'ids':[a,b,c],'title':'错误合并'}]})
        self.assertEqual(len(matters.list_matters(self.s)),3)
    def test_import_confirmation_dedup_preserves_user_edits(self):
        c=planner.validate_course(dict(title='测试课程',weekday=2,start_period=1,end_period=2,weeks='1-10'))
        with self.s.db:
            iid=self.s.db.execute('INSERT INTO planner_imports(kind,result) VALUES (?,?)',('courses',json.dumps(dict(courses=[c])))).lastrowid
        self.assertEqual(planner.apply_import(self.s,{'id':iid})['added'],1)
        self.assertEqual(planner.apply_import(self.s,{'id':iid})['added'],0)
        row=planner.course_rows(self.s)[0];planner.save_course(self.s,dict(row,location='个人修改教室'))
        with self.s.db:
            iid2=self.s.db.execute('INSERT INTO planner_imports(kind,result) VALUES (?,?)',('courses',json.dumps(dict(courses=[c])))).lastrowid
        self.assertEqual(planner.apply_import(self.s,{'id':iid2})['added'],0)
        self.assertEqual(planner.course_rows(self.s)[0]['location'],'个人修改教室')

if __name__=='__main__':unittest.main()
