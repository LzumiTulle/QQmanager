import tempfile,unittest
from qqmanager.store import Store
from qqmanager.native import NativeBridge
from qqmanager import planner
class OverviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def test_removed_groups_remain_only_in_history(self):
  self.s.set_setting('ws_account','123456')
  bridge=NativeBridge(self.s)
  bridge.select_groups(['11111','22222'],{'11111':'当前群','22222':'旧群'})
  self.s.add_messages([dict(content='历史原文',group_name='旧群',sender='测试',sent_at='2026-11-17')],'test')
  bridge.select_groups(['11111'])
  v=self.s.snapshot();self.assertEqual(v['groups'],['当前群']);self.assertIn('旧群',v['history_groups']);self.assertEqual(len(v['messages']),1)
  bridge.select_groups([]);self.assertEqual(self.s.snapshot()['groups'],[])
 def test_full_month_and_weekdays(self):
  self.s.set_setting('academic_calendar',planner.validate_config(dict(anchor_date='2026-11-16',anchor_week=11,total_weeks=19)))
  planner.save_course(self.s,dict(title='课',weekday=1,start_period=1,end_period=2,weeks='1-19'))
  p=planner.snapshot(self.s,'2026-11-17','month');self.assertEqual(len(p['days']),30);self.assertEqual(p['days'][0]['weekday'],7);self.assertEqual(p['days'][-1]['date'],'2026-11-30');self.assertEqual(sum(len(d['courses']) for d in p['days']),5)
  self.assertEqual(len(planner.snapshot(self.s,'2028-02-29','month')['days']),29)
