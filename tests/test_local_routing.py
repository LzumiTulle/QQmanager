from contextlib import ExitStack
import json
import tempfile
import unittest
from qqmanager import llm, matters, schedule
from qqmanager.analysis import analyze, due_date
from qqmanager.local_notice import local_items, supplement
from qqmanager.private_model import WireText
from qqmanager.store import Store

STAMP='2026-11-18T09:00:00+08:00'


class LocalRoutingTests(unittest.TestCase):
    def row(self, text):
        return dict(id=1, content=text, sent_at=STAMP, sender='测试人员')

    def test_two_shared_cards_one_evening_action(self):
        messages=['[分享卡片] 活动甲','[分享卡片] 活动乙','这两个活动今晚签到，没有报名的同学请抓紧时间！','@全体成员']
        with tempfile.TemporaryDirectory() as directory:
            s=Store(directory)
            try:
                s.set_setting('bridge_enabled',True)
                s.set_setting('bridge_groups',['123'])
                for n,content in enumerate(messages,1):
                    s.onebot(dict(post_type='message',message_type='group',group_id=123,self_id=7,
                                  message_id=n,time=1795053300+n,sender={'nickname':'测试'},raw_message=content))
                anchor=next(r for r in matters.raw_insights(s) if r['content']==messages[2])
                self.assertEqual(len(local_items(self.row(messages[2]))),1)
                title,ids=matters.activity_cards(s,anchor)
                self.assertTrue(all(t in title for t in ('今晚签到','活动甲','活动乙','未报名')))
                self.assertEqual(len(set(ids)),4)
                [fact]=schedule.notice_dates([anchor])
                self.assertEqual((fact['period'],fact['value']),('晚上',anchor['sent_at'][:10]))
                with s.db:s.db.execute('UPDATE messages SET sender=? WHERE id=?',('另一人',ids[0]))
                self.assertIsNone(matters.activity_cards(s,anchor))
            finally:s.db.close()

    def test_two_clocks_and_deadline_without_inventing_a_date(self):
        text='请明天10点前提交报告，下午3点开会。'
        parts=local_items(self.row(text))
        self.assertEqual([(p['kind'],p['due'][11:16]) for p in parts],[('待办','10:00'),('通知','15:00')])
        self.assertEqual(due_date(text,STAMP)[0], '2026-11-19T10:00:00+08:00')
        self.assertIsNone(due_date('明天请留意通知。周五下午3点开会。',STAMP)[0])
        self.assertEqual(due_date('下星期四下午3点半前上传材料',STAMP)[0],'2026-11-26T15:30:00+08:00')
        self.assertIsNone(due_date('第12周周三下午3点考试',STAMP)[0])
        self.assertIsNone(due_date('明天和后天下午3点开会',STAMP)[0])
        self.assertEqual(due_date('11月25日（周三）下午3点考试',STAMP)[0], '2026-11-25T15:00:00+08:00')
        self.assertIsNone(due_date('11月25日（周五）下午3点考试',STAMP)[0])
        self.assertIsNone(local_items(self.row('我已经提交过报告了。')))
        self.assertIsNone(local_items(self.row('通知：请交报告和材料，下次另行通知。')))

    def test_negated_quotes_and_mixed_instructions(self):
        row=self.row('通知：报名已取消，请勿明天10点前提交报名表。')
        raw=dict(kind='待办',title='提交报名表',reason='要求',evidence=[dict(message_id=1,quote='提交报名表')])
        self.assertEqual(llm.validate_result(dict(items=[raw]),[row],1),[])
        self.assertEqual(analyze(row['content'],STAMP)['kind'],'通知')
        self.assertIsNone(schedule.suggested_deadline([row]))
        row=self.row('请勿提交报名表，请周五18:00前上传电子版。')
        good=dict(raw,title='上传电子版',evidence=[dict(message_id=1,quote='请周五18:00前上传电子版')])
        bad_whole=dict(raw,evidence=[dict(message_id=1,quote=row['content'])])
        result=llm.validate_result(dict(items=[raw,bad_whole,good]),[row],1)
        self.assertEqual([(r['title'],r['due'][11:16]) for r in result],[('上传电子版','18:00')])

    def test_wire_masks_unknown_names_identifiers_and_links_reversibly(self):
        text='请赵小羽向孙小河提交编号230187654321，联系a@foo.test，材料见https://foo.test/p?secret=wert。明天10:30开会'
        wire=WireText()
        masked=wire.encode({'title':text,'evidence':[{'quote':text}]})
        for value in ('赵小羽','孙小河','230187654321','a@foo.test','foo.test','wert'):
            self.assertNotIn(value,json.dumps(masked,ensure_ascii=False))
        self.assertIn('明天10:30开会',masked['title'])
        self.assertEqual(wire.restore(masked['evidence'])[0]['quote'],text)

    def test_attachments_keep_resources_after_extraction(self):
        row=self.row('[文件：空白登记表.docx]\n【附件提取正文：空白登记表.docx】\n班级 学号 姓名 签字')
        self.assertEqual(local_items(row)[0]['kind'],'资料')
        row=self.row('请填写两项材料。在线表格链接：https://example.invalid/x')
        items=supplement([],row)
        self.assertIn('https://example.invalid/x',items[0]['evidence'][0]['quote'])
        self.assertEqual(len(supplement(items,row)),1)

    def test_paused_model_processes_local_jobs_behind_an_uncertain_job(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Store(directory)
            try:
                s.set_setting('bridge_enabled',True);s.set_setting('bridge_groups',['123'])
                for i,text in enumerate(('还是按刚才的来','请明天10点前提交报告，下午3点开会。'),1):
                    s.onebot(dict(post_type='message',message_type='group',group_id=123,self_id=7,
                                  message_id=i,time=1794963600,sender={'nickname':'测试'},raw_message=text))
                worker=llm.Analyzer(s,lambda *a:self.fail('unexpected model call'))
                worker.step();worker.step()
                self.assertEqual([x['state'] for x in s.query('select state from analysis_jobs order by message_id')],['pending','done'])
                self.assertEqual(len(s.search('insights')),3)
                all_matters=matters.list_matters(s)
                self.assertTrue(any('刚才' in m['title'] and not m['actionable'] for m in all_matters))
                agenda={m['topic']:m for m in all_matters if '刚才' not in m['title']}
                self.assertIsNotNone(agenda['待办']['suggested_deadline'])
                self.assertIsNone(agenda['通知']['suggested_deadline'])
                self.assertFalse(agenda['通知']['actionable'])
                self.assertEqual(agenda['通知']['date_facts'][0]['value'][-5:],'15:00')
                self.assertEqual(llm.calls_today(s),0)
            finally:s.db.close()

class ResponsibilityTests(unittest.TestCase):
    def test_document_scope_nested_actors_exception_and_archive(self):
        from qqmanager.responsibilities import document_duties
        text=('适用范围：2024级（不含专转本）。各学院通知学生登录教务系统开展学业自查。'
              '学生已修课程与培养方案要求课程代码不一致的，须办理课程替代手续。'
              '若报名成功，班长负责收集回执。已提交的学生无需重复提交。'
              '请各学院填报自查表，表格经学生本人、辅导员、学院领导签字确认后留存学院。'
              '《情况统计表》于12月20日前报送至教务处。')
        doc=document_duties(text)
        self.assertEqual(doc['scope'],'2024级（不含专转本）')
        for actor,action,condition in [
            ('各学院','通知学生',''),('学生','登录教务系统',''),('学生','课程替代','不一致'),
            ('班长','收集','报名成功'),('学生本人、辅导员、学院领导','签字',''),
            ('各学院','留存','签字确认后'),('各学院','12月20日','')]:
            with self.subTest(actor=actor,action=action):
                self.assertTrue(any(r['actor']==actor and action in r['action'] and condition in r['condition']
                                    for r in doc['duties']))
        self.assertTrue(all(r['quote'] in text for r in doc['duties']))
        self.assertTrue(any('无需重复提交' in r['quote'] for r in doc['duties']))

    def test_missing_conditions_in_model_titles_never_make_personal_tasks(self):
        from qqmanager.responsibilities import personal_actions
        for text in ('请各班团支书11月28日将读书笔记送至综合楼。',
                     '若未提交，须补交材料。','仅限获奖学生。请大家提交报告。',
                     '请大家提交报告，已交的同学无需重复提交。','请小陈提交报名表。',
                     '[文件：核查通知.docx]','@全体成员 学院领导审核后由宿舍长收齐资料。',
                     '请参赛同学明天18:00前提交报名表。'):
            with self.subTest(text=text):
                self.assertEqual(personal_actions(dict(message_id=1,kind='待办',title='提交报告',content=text,sent_at=STAMP)),[])

    def test_enumerated_checklist_is_scoped_before_scheduling(self):
        from qqmanager.responsibilities import personal_actions
        text='@全体成员\n1.完成学业自查表\n2.完成情况统计表\n宿舍长收自查表电子版给我'
        row=dict(id=1,content=text,sent_at=STAMP)
        parsed=local_items(row)
        eligible=[a for i in parsed for a in personal_actions(dict(row,message_id=1,kind=i['kind'],title=i['title']))]
        self.assertEqual(eligible,['完成学业自查表','完成情况统计表'])
        self.assertIsNone(local_items(dict(row,content=text+'\n仅限新生')))
        self.assertIsNone(local_items(dict(row,content=text.replace('2.','4.'))))

    def test_early_classes_resolve_each_date_and_do_not_assign_homework(self):
        row=dict(id=1,sent_at=STAMP,content='明天和下周二早八上操作系统的课，在 D115@全体成员')
        items=local_items(row)
        self.assertEqual([(i['kind'],i['due'][:16]) for i in items],
                         [('通知','2026-11-19T08:00'),('通知','2026-11-24T08:00')])
        self.assertTrue(all(i['evidence'][0]['quote'] in row['content'] for i in items))
        row['content']='这两个活动今晚签到，没有报名的同学请抓紧时间!'
        self.assertEqual(supplement([],row)[0]['kind'],'通知')

    def test_cache_requires_authorized_identical_file_and_rebinds_quotes(self):
        from qqmanager.media import analysis_source
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as cleanup:
            s=Store(tmp)
            cleanup.callback(s.db.close)
            s.set_setting('bridge_enabled',True);s.set_setting('bridge_groups',['11','12'])
            cfg=s.setting('llm_config');cfg.update(enabled=True,model='test',url='https://example.invalid')
            s.set_setting('llm_config',cfg);s.set_setting('llm_test',llm.signature(cfg))
            def add(group,document,caption='[文件：通知.docx]'):
                with s.db:
                    mid=s.db.execute('insert into messages(group_name,content,sender,sent_at) values(?,?,?,?)',
                                    (group,caption,'学生',STAMP)).lastrowid
                    s.db.execute("insert into analysis_jobs(message_id,account,group_id) values(?,'7',?)",(mid,group))
                    fid=s.db.execute('insert into files(digest,name,group_name,body) values(?,?,?,?)',
                                     ('a'*64,'通知.docx',str(mid),document)).lastrowid
                    s.db.execute("insert into attachments(message_id,slot,kind,name,file_id,status) values(?,0,'file','通知.docx',?,'ready')",(mid,fid))
                return mid
            calls=[]
            def predict(cfg,messages,target,interests):
                calls.append(target)
                return {'items':[dict(kind='通知',title='材料审核',reason='通知',
                                      evidence=[dict(message_id=target,quote='通知.docx')])]}
            worker=llm.Analyzer(s,predict)
            first=add('11','由学院统筹核查。');worker.step()
            second=add('12','由学院统筹核查。');worker.step()
            self.assertEqual(calls,[first])
            cached=s.query("select engine,evidence from insights where message_id=? and quality_reason=''",(second,))[0]
            self.assertEqual(cached['engine'],'reuse')
            self.assertEqual(json.loads(cached['evidence'])[0]['message_id'],second)
            changed=add('12','由学院统筹核查，下周通知。');worker.step()
            self.assertEqual(calls,[first,changed])
            s.set_setting('bridge_groups',['12'])
            restricted=add('12','由学院统筹核查。');worker.step()
            self.assertEqual(calls,[first,changed,restricted])

    def test_candidate_identity_is_not_inherited_from_grouped_official_attachment(self):
        from qqmanager import planner
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as cleanup:
            s=Store(tmp)
            cleanup.callback(s.db.close)
            for text in ('请在下周四18:00前提交学业自查表','学院应于12月20日前提交学业自查表'):
                s.add_messages([dict(content=text,group_name='班群',sender='老师',sent_at=STAMP)],'test')
            # Put both into one matter, like a forwarded school notice plus a class instruction.
            rows=s.query('select id,title from insights order by id')
            with s.db:
                s.db.execute("update insights set title='学业自查通知' where id=?",(rows[-1]['id'],))
            board=planner.snapshot(s,'2026-11-18')['workflow']['matters']
            entry=next(m for m in board.values() if m['candidate'])
            self.assertEqual(entry['suggested_deadline'],'2026-11-26T18:00')
            self.assertEqual(entry['personal_insight_id'],rows[0]['id'])
            task=schedule.save(s,dict(matter_id=entry['id'],revision=entry['revision'],planned_date='2026-11-20'))
            stored=s.query('select title,insight_id from tasks where id=?',(task,))[0]
            self.assertEqual(stored,dict(title='请在下周四18:00前提交学业自查表',insight_id=rows[0]['id']))
