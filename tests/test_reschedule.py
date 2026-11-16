import json
import tempfile
import unittest
from qqmanager.store import Store
from qqmanager import planner, matters, reschedule
from qqmanager.llm import signature

class RescheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.s.set_setting('academic_calendar',planner.validate_config(dict(anchor_date='2026-11-16',anchor_week=11,total_weeks=19)))
        self.s.set_setting('bridge_enabled',True);self.s.set_setting('bridge_groups',['12345']);self.s.set_setting('ws_account','67890')
        cfg=self.s.setting('llm_config');cfg.update(enabled=True,url='https://example.invalid',model='test')
        self.s.set_setting('llm_config',cfg);self.s.set_setting('llm_test',signature(cfg))
        self.base=dict(title='操作系统',weekday=3,start_period=3,end_period=4,weeks='1-12',location='D115')
        self.course=planner.save_course(self.s,self.base)
        planner.save_course(self.s,dict(self.base,weekday=1,start_period=7,end_period=8))
        planner.save_course(self.s,dict(self.base,title='操作系统课程设计',weeks='15'))
        planner.save_course(self.s,dict(self.base,title='软件工程',weekday=2,start_period=1,end_period=2,weeks='1-10'))
        self.text='操作系统第11周和第12周周三上午三四节调至对应周周二上午一二节，教室D115'
        self.mid=self.message(self.text)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def message(self,text):
        self.s.add_messages([dict(content=text,group_name='测试班级',sender='老师',sent_at='2026-11-16T21:05:00+08:00')],'test')
        mid=self.s.query('SELECT max(id) id FROM messages')[0]['id']
        with self.s.db:
            self.s.db.execute('DELETE FROM insights WHERE message_id=?',(mid,))
            self.s.db.execute('INSERT INTO insights(message_id,title,kind,score) VALUES (?,?,?,80)',(mid,text,'通知'))
            self.s.db.execute("INSERT INTO analysis_jobs(message_id,group_id,account,state) VALUES (?,'12345','67890','done')",(mid,))
        return mid
    def result(self):
        return {'changes':[dict(course='操作系统',from_date=a,to_date=b,from_start_period=3,from_end_period=4,to_start_period=1,to_end_period=2,location='D115',evidence=[dict(message_id=self.mid,quote=self.text)]) for a,b in [('2026-11-18','2026-11-17'),('2026-11-25','2026-11-24')]],'note':''}
    def draft(self,result=None):
        self.assertTrue(reschedule.recognize_step(self.s,lambda *a,**kw:result or self.result()))
        return self.s.query('SELECT max(id) id FROM schedule_changes')[0]['id']
    def confirm(self,cid,**extra):
        return reschedule.apply(self.s,dict(id=cid,confirmed=True,token=reschedule.review(self.s,cid).get('token',''),**extra))
    def test_two_weeks_confirm_idempotent_calendar_and_undo(self):
        cid=self.draft();p=planner.snapshot(self.s,'2026-11-16')
        self.assertEqual(len(p['days'][2]['courses']),1);self.assertFalse(p['days'][1]['courses'])
        plan=reschedule.review(self.s,cid);self.assertTrue(plan['ready']);self.assertEqual(plan['conflicts'],[])
        with self.assertRaises(ValueError):reschedule.apply(self.s,dict(id=cid,token=plan['token']))
        self.assertEqual(self.confirm(cid)['updated'],2)
        self.assertTrue(reschedule.apply(self.s,dict(id=cid,confirmed=True))['already_applied'])
        for d in ('2026-11-16','2026-11-23'):
            p=planner.snapshot(self.s,d);self.assertEqual(p['days'][2]['courses'],[])
            self.assertEqual(len(p['days'][1]['courses']),1);self.assertTrue(p['days'][1]['courses'][0]['adjusted'])
            self.assertEqual(len(p['days'][0]['courses']),1)
        self.assertEqual(len(planner.snapshot(self.s,'2026-11-09')['days'][2]['courses']),1)
        self.assertEqual(len(planner.snapshot(self.s,'2026-12-14')['days'][2]['courses']),1)
        reschedule.action(self.s,dict(id=cid,action='undo'))
        p=planner.snapshot(self.s,'2026-11-16');self.assertEqual(len(p['days'][2]['courses']),1);self.assertFalse(p['days'][1]['courses'])
        self.assertFalse(reschedule.recognize_step(self.s,lambda *a,**kw:self.result()))
    def test_stale_tokens_source_update_and_conflict_acknowledgement(self):
        cid=self.draft();old=reschedule.review(self.s,cid)['token']
        planner.save_course(self.s,dict(self.base,title='个人选修',weekday=2,start_period=1,end_period=2,weeks='11'))
        with self.assertRaises(ValueError):reschedule.apply(self.s,dict(id=cid,confirmed=True,token=old))
        self.assertTrue(reschedule.review(self.s,cid)['conflicts'])
        with self.assertRaises(ValueError):self.confirm(cid)
        self.confirm(cid,allow_conflicts=True)
        self.assertTrue(all(c['conflict'] for c in planner.snapshot(self.s,'2026-11-16')['days'][1]['courses']))
    def test_ambiguous_missing_and_name_dont_match_course_design(self):
        result=self.result();result['changes'][0]['course']='操作系统课程设计'
        cid=self.draft(result);self.assertFalse(reschedule.review(self.s,cid)['ready'])
        with self.assertRaises(ValueError):self.confirm(cid)
        self.assertFalse(self.s.query('SELECT * FROM course_overrides'))
    def test_bad_evidence_and_unknown_date_never_applied(self):
        result=self.result();result['changes'][0]['evidence'][0]['quote']='编造原文'
        self.assertFalse(reschedule.recognize_step(self.s,lambda *a,**kw:result))
        cid=self.s.query('SELECT id FROM schedule_changes')[0]['id'];self.assertEqual(reschedule.review(self.s,cid)['state'],'error')
        reschedule.action(self.s,dict(id=cid,action='retry'))
        result=self.result();result['changes'][0]['to_date']=None
        self.assertTrue(reschedule.recognize_step(self.s,lambda *a,**kw:result));self.assertFalse(reschedule.review(self.s,cid)['ready'])
    def test_group_scope_source_revision_and_consent(self):
        cid=self.draft()
        self.s.set_setting('bridge_groups',[])
        self.assertFalse(reschedule.review(self.s,cid)['ready'])
        with self.assertRaises(ValueError):self.confirm(cid)
        self.s.set_setting('bridge_groups',['12345'])
        new=self.message('操作系统调课补充：改到周五。')
        es=matters.list_matters(self.s);old=next(e for e in es if self.text==e['title']);new_e=next(e for e in es if old['id']!=e['id'])
        matters.update(self.s,dict(id=new_e['id'],merge_into=old['id']))
        self.assertFalse(reschedule.review(self.s,cid)['ready'])
        self.assertTrue(reschedule.recognize_step(self.s,lambda *a,**kw:self.result()))
        self.assertEqual(reschedule.review(self.s,cid)['state'],'superseded')
    def test_chained_moves_undo_order_and_cross_week(self):
        first=self.draft();self.confirm(first)
        self.mid=self.message('操作系统再次调课：第11周周二一二节改到第12周周四五六节，教室C101')
        self.text='操作系统再次调课：第11周周二一二节改到第12周周四五六节，教室C101'
        second=dict(changes=[dict(course='操作系统',from_date='2026-11-17',to_date='2026-11-26',from_start_period=1,from_end_period=2,to_start_period=5,to_end_period=6,location='C101',evidence=[dict(message_id=self.mid,quote=self.text)])])
        cid=self.draft(second);self.confirm(cid)
        first_marker=next(c for c in reschedule.list_changes(self.s) if c['id']==first)
        self.assertNotIn('2026-11-17',first_marker['dates'])
        self.assertFalse(planner.snapshot(self.s,'2026-11-16')['days'][1]['courses'])
        moved=planner.snapshot(self.s,'2026-11-23')['days'][3]['courses'][0];self.assertEqual(moved['location'],'C101')
        with self.assertRaises(ValueError):reschedule.action(self.s,dict(id=first,action='undo'))
        reschedule.action(self.s,dict(id=cid,action='undo'));reschedule.action(self.s,dict(id=first,action='undo'))
        self.assertEqual(len(planner.snapshot(self.s,'2026-11-16')['days'][2]['courses']),1)
    def test_reposted_applied_change_is_not_offered_again(self):
        cid=self.draft();self.confirm(cid)
        self.text='再次转发：'+self.text;self.mid=self.message(self.text)
        self.assertTrue(reschedule.recognize_step(self.s,lambda *a,**kw:self.result()))
        self.assertEqual(self.s.query('SELECT state FROM schedule_changes ORDER BY id DESC')[0]['state'],'duplicate')
        self.assertEqual(len(reschedule.list_changes(self.s)),1)
        self.assertEqual(len(self.s.query('SELECT * FROM course_overrides')),2)

    def test_confirmation_routes_preserve_csrf_and_require_review_token(self):
        import threading,urllib.request,urllib.error
        from qqmanager.server import make_server
        cid=self.draft();server=make_server(self.s,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def get(path):
            with urllib.request.urlopen(base+path) as response:return json.load(response)
        csrf=get('/api/session')['csrf']
        def post(path,body,token=csrf):
            request=urllib.request.Request(base+path,json.dumps(body).encode(),{'Content-Type':'application/json','X-CSRF-Token':token})
            with urllib.request.urlopen(request) as response:return json.load(response)
        try:
            plan=get('/api/planner/change/'+str(cid))
            body=dict(id=cid,confirmed=True,token=plan['token'])
            with self.assertRaises(urllib.error.HTTPError) as exc:post('/api/planner/change/confirm',body,'invalid')
            self.assertEqual(exc.exception.code,403)
            self.assertEqual(post('/api/planner/change/confirm',body)['updated'],2)
            snapshot=get('/api/planner?date=2026-11-16')
            self.assertTrue(snapshot['days'][1]['courses'][0]['adjusted'])
            post('/api/planner/change/action',dict(id=cid,action='undo'))
            self.assertFalse(get('/api/planner?date=2026-11-16')['days'][1]['courses'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_base_calendar_changes_protected_and_ignore_doesnt_modify(self):
        cid=self.draft();self.confirm(cid)
        with self.assertRaises(ValueError):planner.save_course(self.s,dict(self.base,id=self.course,weekday=4))
        with self.assertRaises(ValueError):planner.save_config(self.s,dict(anchor_date='2026-11-16',anchor_week=10,total_weeks=19))
        reschedule.action(self.s,dict(id=cid,action='undo'))
        # Dismiss a fresh draft explicitly and ensure it cannot be applied.
        with self.s.db:self.s.db.execute("UPDATE schedule_changes SET state='pending' WHERE id=?",(cid,))
        reschedule.action(self.s,dict(id=cid,action='dismiss'))
        with self.assertRaises(ValueError):self.confirm(cid)
        self.assertFalse(self.s.query('SELECT * FROM course_overrides'))

if __name__=='__main__':unittest.main()
