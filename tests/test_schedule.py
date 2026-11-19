import tempfile
import unittest
from qqmanager import matters, planner, schedule
from qqmanager.store import Store


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s=Store(tmp.name)
        self.addCleanup(self.s.db.close)
        planner.save_config(self.s,dict(anchor_date='2026-11-16',anchor_week=1))
        planner.save_course(self.s,dict(title='算法',weekday=1,start_period=1,end_period=2,weeks=[1]))

    def snapshot(self):
        return planner.snapshot(self.s,'2026-11-16')['workflow']

    def test_lab_notice_is_linked_to_the_next_matching_lab_with_its_original(self):
        for weekday,room in [(3,'讲堂'),(5,'四机房')]:
            planner.save_course(self.s,dict(title='计算机网络原理',location=room,teacher='许老师',weekday=weekday,
                                           start_period=1,end_period=2,weeks=[1,2]))
        self.s.add_messages([dict(content='实验课大家记得带上实验报告册和课本，可以当堂完成报告。',
                                  sender='许老师',group_name='26 计算机网络',sent_at='2026-11-17T12:30:00+08:00')],'test')
        data=planner.snapshot(self.s,'2026-11-16')
        matches=[(d['date'],c) for d in data['days'] for c in d['courses'] if c['notices']]
        self.assertEqual([(d,c['location']) for d,c in matches],[('2026-11-20','四机房')])
        self.assertEqual(matches[0][1]['notices'][0]['id'],1)
        self.assertEqual(data['workflow']['confirm'],[])
        next_week=planner.snapshot(self.s,'2026-11-23')
        self.assertFalse(any(c['notices'] for d in next_week['days'] for c in d['courses']))

    def test_pending_matter_single_task_separate_deadline_and_course_conflict(self):
        self.s.add_messages([dict(content='请在下周四18:00前提交学业自查表',group_name=group,sender='老师',
                                  sent_at='2026-11-16T08:00:00+08:00') for group in ('班群','年级群')],'test')
        rows=matters.list_matters(self.s)
        if len(rows)==2:
            matters.update(self.s,dict(id=rows[1]['id'],merge_into=rows[0]['id']))
        candidate=self.snapshot()['confirm'][0]
        self.assertTrue(candidate['candidate'])
        self.assertEqual(candidate['suggested_deadline'],'2026-11-26T18:00')
        body=dict(matter_id=candidate['id'],revision=candidate['revision'],deadline=candidate['suggested_deadline'],
                  planned_date='2026-11-16',planned_start='08:30',planned_end='09:00')
        with self.assertRaisesRegex(ValueError,'算法'):schedule.save(self.s,body)
        self.assertFalse(self.s.query('SELECT * FROM tasks'))
        body.update(planned_start='09:40',planned_end='10:00')
        task=schedule.save(self.s,body)
        self.assertEqual(task,schedule.save(self.s,body))
        self.assertEqual(len(self.s.snapshot()['tasks']),1)
        self.assertEqual(self.snapshot()['matters'][str(candidate['id'])]['label'],'已安排')
        self.s.update_task(task,dict(due='2026-11-24T15:00:00+08:00'))
        saved=self.s.snapshot()['tasks'][0]
        self.assertEqual(len(saved['groups']),2)
        clear=dict(task_id=task,expected=['2026-11-16','09:40','10:00',body['deadline']],deadline=body['deadline'])
        schedule.save(self.s,clear)
        self.assertEqual(self.snapshot()['matters'][str(candidate['id'])]['label'],'待安排')
        self.assertEqual(self.s.snapshot()['tasks'][0]['due'],'2026-11-24T15:00:00+08:00')
        self.s.update_task(task,dict(status='done'))
        item=self.snapshot()['matters'][str(candidate['id'])]
        self.assertEqual(item['label'],'已完成')
        self.assertFalse(item['candidate'])
        with self.assertRaises(ValueError):schedule.save(self.s,body)

    def test_single_save_keeps_original_reminder_deadline_and_plan_together(self):
        self.s.add_messages([dict(content='请在下周四18:00前提交学业自查表',group_name='课程群',
                                  sender='老师',sent_at='2026-11-16T08:00:00+08:00')],'test')
        source=self.snapshot()['confirm'][0]
        body=dict(title='作业截图',due='2026-11-22T08:00:00+08:00',deadline='2026-11-22T20:00',
                  matter_id=source['id'],revision=source['revision'],
                  plan=dict(planned_date='2026-11-16',planned_start='08:00',planned_end='08:30'))
        with self.assertRaisesRegex(ValueError,'时段冲突'):
            self.s.create_task(body)
        self.assertFalse(self.s.query('SELECT id FROM tasks'))
        body['plan']['planned_date']='2026-11-22'
        tid=self.s.create_task(body)
        self.assertEqual(tid,self.s.create_task(body))
        task=self.snapshot()['matters'][str(source['id'])]['tasks'][0]
        self.assertEqual((task['id'],task['planned_start'],task['planned_end'],task['due'],task['deadline']),
                         (tid,'08:00','08:30',body['due'],body['deadline']))
        expected=['2026-11-22','08:00','08:30',body['deadline']]
        with self.assertRaisesRegex(ValueError,'时段冲突'):
            self.s.update_task(tid,dict(title='不应写入',due=None,expected=expected,
                                        plan=dict(body['plan'],planned_date='2026-11-16')))
        self.s.update_task(tid,dict(expected=expected,plan=dict(body['plan'],planned_date='2026-11-23')))
        with self.assertRaisesRegex(ValueError,'已更改'):
            self.s.update_task(tid,dict(expected=expected,plan=body['plan']))
        [saved]=self.s.snapshot()['tasks']
        self.assertEqual((saved['title'],saved['due'],saved['deadline'],saved['planned_date']),
                         ('作业截图',body['due'],body['deadline'],'2026-11-23'))

    def test_stale_edits_and_overlapping_tasks(self):
        a,b=(self.s.create_task(dict(title=t)) for t in ('整理资料','打印材料'))
        body=dict(task_id=a,planned_date='2026-11-17',planned_start='13:00',planned_end='13:30',expected=[None]*4)
        schedule.save(self.s,body)
        with self.assertRaisesRegex(ValueError,'时段冲突'):schedule.save(self.s,dict(body,task_id=b))
        with self.assertRaisesRegex(ValueError,'已更改'):schedule.save(self.s,dict(body,planned_start='12:30'))
        schedule.save(self.s,dict(body,task_id=b,planned_start=None,planned_end=None,deadline='2026-12-01'))
        self.assertEqual(self.s.query('SELECT deadline FROM tasks WHERE id=?',(b,))[0]['deadline'],'2026-12-01')
        self.assertIsNone(schedule.suggested_deadline([dict(content='请领取明天活动的门票',sent_at='2026-11-16')]))

    def test_separate_student_deadline_and_group_delivery(self):
        from datetime import datetime
        notices=[dict(content=text,sent_at='2026-11-18T14:44:00+08:00') for text in
                 ('请大家抓紧时间完成读书笔记，下周四全部交给我。','请各班团支书11月28日将读书笔记送至办公室。')]
        facts=schedule.notice_dates(notices)
        self.assertEqual([(f['kind'],f['value']) for f in facts],[('deadline','2026-11-26'),('handoff','2026-11-28')])
        limit=schedule.single_deadline(facts)
        busy={'2026-11-25':[('08:00','09:40'),('09:45','10:15'),('10:15','11:45')]}
        pick=schedule.recommend(facts,limit,None,busy,datetime(2026,11,18,16,30))
        self.assertEqual((pick['date'],pick['start'],pick['end']),('2026-11-25','11:45','12:15'))

    def test_event_time_manual_overrides_and_unknown_dates(self):
        from datetime import datetime
        now=datetime(2026,11,18,16,30)
        def facts(text):return schedule.notice_dates([dict(content=text,sent_at=now.isoformat())])
        event=facts('明天下午3点至4点召开班会')
        result=schedule.recommend(event,None,None,{},now)
        self.assertEqual((result['date'],result['start'],result['end']),('2026-11-19','15:00','16:00'))
        self.assertNotIn('start',schedule.recommend(event,None,None,{'2026-11-19':[('15:15','15:30')]},now))
        self.assertEqual(schedule.recommend(facts('明天下午举行讲座'),None,None,{},now)['start'],'13:00')
        self.assertNotIn('date',schedule.recommend(facts('周四提交，周五提交'),None,None,{},now))
        self.assertNotIn('date',schedule.recommend([], '2026-11-17',None,{},now))
        self.assertEqual(schedule.recommend([],None,'2026-11-23',{},now)['date'],'2026-11-23')
        self.assertEqual(schedule.recommend([],None,None,{},now)['start'],'16:45')

    def test_legacy_morning_class_uses_message_date(self):
        def facts(content):
            return schedule.notice_dates([dict(content=content,sent_at='2026-11-16T22:30:00+08:00')])
        self.assertEqual(facts('明天有早八，大家不要忘记去上了@全体成员'), [
            dict(kind='event',value='2026-11-17T08:00',end='',label='上课',period='')])
        self.assertEqual(facts('PU活动记得签到@全体成员'), [])
        self.assertEqual(facts('如果明天有早八，大家不要忘记去上课'), [])

    def test_checkin_context_uses_the_same_evening_and_keeps_both_originals(self):
        anchor='这两个活动今晚签到，没有报名的同学请抓紧时间!'
        reminder='PU活动记得签到@全体成员'
        followup='PU活动定位签到失败的话，可以关闭定位后再签到@全体成员'
        def post(group,time,text,sender='发布人'):
            self.s.add_messages([dict(group_name=group,sender=sender,sent_at='2026-11-'+time+'+08:00',
                                      content=text)],'test')
        for group in ('班群','另有场次','不同发言人','次日提醒','只有提醒'):
            if group!='只有提醒':post(group,'16T07:38:46',anchor)
            post(group,'16T15:20:00','请大家相互转告')
            day='17' if group=='次日提醒' else '16'
            post(group,day+'T18:07:13',reminder)
            post(group,day+'T18:58:39',followup,'另一人' if group=='不同发言人' else '发布人')
        post('另有场次','16T12:00:00','另一场活动明天签到')
        rows=self.s.query('SELECT id message_id,group_name,content,sent_at FROM messages')
        linked=matters.evening_checkins(self.s,rows)
        valid=[r for r in rows if r['group_name']=='班群']
        source=next(r['message_id'] for r in valid if r['content']==anchor)
        self.assertEqual(set(linked),{r['message_id'] for r in valid if r['content'] in (reminder,followup)})
        for fact in linked.values():
            self.assertEqual((fact['value'],fact['period'],fact['end'],fact['source_message_id']),
                             ('2026-11-16','晚上','',source))
        post('班群','16T19:05:00','签到延期到明天')
        self.assertEqual(matters.evening_checkins(self.s,rows),{})
