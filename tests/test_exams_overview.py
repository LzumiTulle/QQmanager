import copy,json,tempfile,unittest
from datetime import datetime
from qqmanager.store import Store
from qqmanager import planner,exams,overview,matters
from qqmanager.llm import signature

class ExamOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.s.set_setting('academic_calendar',planner.validate_config(dict(anchor_date='2026-11-16',anchor_week=11,total_weeks=19)))
        self.s.set_setting('bridge_enabled',True);self.s.set_setting('bridge_groups',['12345']);self.s.set_setting('ws_account','67890')
        cfg=self.s.setting('llm_config');cfg.update(enabled=True,url='https://example.invalid',model='test')
        self.s.set_setting('llm_config',cfg);self.s.set_setting('llm_test',signature(cfg))
        planner.save_course(self.s,dict(title='编译原理',weekday=1,start_period=5,end_period=6,weeks='3-12'))
        self.text='第13周考试：编译原理 2026年12月2日 13:40-15:40 教室D115 计算机241班'
        self.mid=self.add(self.text)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def add(self,text,group='12345',important=True):
        self.s.add_messages([dict(content=text,group_name='测试群',sender='老师',sent_at='2026-11-16T10:00:00+08:00')],'test')
        mid=self.s.query('SELECT max(id) id FROM messages')[0]['id']
        with self.s.db:
            self.s.db.execute('DELETE FROM insights WHERE message_id=?',(mid,))
            if important:self.s.db.execute('INSERT INTO insights(message_id,title,kind,score) VALUES (?,?,?,80)',(mid,text,'通知'))
            self.s.db.execute("INSERT INTO analysis_jobs(message_id,group_id,account,state) VALUES (?,?,'67890','done')",(mid,group))
            self.s.db.execute('UPDATE messages SET created=? WHERE id=?',(datetime(2026,11,17,0,10,tzinfo=planner.TZ).timestamp(),mid))
        return mid
    def result(self):
        return {'exams':[dict(course='编译原理',date='2026-12-02',start_time='13:40',end_time='15:40',location='D115',audience='计算机241班',evidence=[dict(message_id=self.mid,quote=self.text)])]}
    def test_candidates_match_courses_keep_evidence_and_never_change_calendar(self):
        before=planner.course_rows(self.s)
        result=self.result();result['exams'].append(copy.deepcopy(result['exams'][0]))
        self.assertTrue(exams.recognize_step(self.s,lambda *a,**k:result))
        rows=exams.snapshot(self.s)['events'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['matches'],['编译原理']);self.assertTrue(rows[0]['needs_review'])
        self.assertEqual(rows[0]['date'],'2026-12-02');self.assertEqual(rows[0]['evidence'][0]['quote'],self.text)
        self.assertEqual(planner.course_rows(self.s),before);self.assertFalse(self.s.query('SELECT * FROM tasks'))
        self.s.set_setting('bridge_groups',[]);self.assertFalse(exams.snapshot(self.s)['events'])
    def test_invalid_evidence_and_invented_subject_are_rejected(self):
        detail=matters.detail(self.s,matters.list_matters(self.s)[0]['id']);src=exams.source(self.s,detail)
        for field,value in [('course','操作系统'),('date','2030-12-02'),('start_time','26:99')]:
            result=self.result();result['exams'][0][field]=value
            with self.assertRaises(ValueError):exams.validate_result(result,src)
        result=self.result();result['exams'][0]['evidence'][0]['quote']='无中生有'
        with self.assertRaises(ValueError):exams.validate_result(result,src)
    def test_updated_notice_invalidates_old_exam_candidate(self):
        exams.recognize_step(self.s,lambda *a,**k:self.result())
        self.add('第13周考试补充：编译原理改为12月3日')
        self.assertFalse(exams.snapshot(self.s)['events']);self.assertTrue(exams.snapshot(self.s)['pending'])
    def test_ocr_subject_and_class_matching_do_not_cross_other_classes(self):
        self.assertTrue(exams.near_name('编译原理','缤译原理'))
        self.assertFalse(exams.near_name('操作系统','操作系统课程设计'))
        self.assertTrue(exams.class_match('Bit算机241','B计算机241'))
        self.assertFalse(exams.class_match('B计t算机244','B计算机241'))
        self.assertFalse(exams.class_match('W计算机241','B计算机241'))
        self.assertFalse(exams.class_match('B光电信息241','B计算机241'))
        cfg=planner.config(self.s);cfg['class_name']='B计算机241';self.s.set_setting('academic_calendar',cfg)
        result=self.result();result['exams'][0]['audience']='B计算机244'
        exams.recognize_step(self.s,lambda *a,**k:result)
        self.assertEqual(exams.snapshot(self.s)['events'][0]['matches'],[])
    def test_partial_invalid_rows_preserve_valid_exam_and_report_omission(self):
        src=exams.make_source(self.s,matters.detail(self.s,matters.list_matters(self.s)[0]['id']))
        result=self.result();bad=copy.deepcopy(result['exams'][0]);bad['course']='没有依据的科目';result['exams'].append(bad)
        parsed=exams.validate_result(result,src)
        self.assertEqual(len(parsed['exams']),1);self.assertIn('未通过原文核验',parsed['note'])
    def test_receipt_counts_use_ingestion_date_scope_and_paginate_over_500(self):
        for n in range(505):self.add('普通消息'+str(n),important=False)
        self.add('未选群通知',group='99999')
        v=overview.snapshot(self.s,'2026-11-17')
        self.assertEqual(v['counts']['received'],506);self.assertEqual(v['counts']['important'],1)
        self.assertEqual(len(v['messages']),20);self.assertEqual(len(v['important']),1)
        self.assertEqual(len(overview.snapshot(self.s,'2026-11-17',offset=500)['messages']),6)
        self.assertEqual(overview.snapshot(self.s,'2026-11-16')['counts']['received'],0)
        self.s.set_setting('bridge_groups',[]);self.assertEqual(overview.snapshot(self.s,'2026-11-17')['counts']['received'],0)
