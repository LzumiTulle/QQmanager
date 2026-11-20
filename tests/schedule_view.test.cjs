const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const QQPlanner = require('../web/planner-model');

function timetable(courses, exams = [], items = []) {
  const blocks = [['08:00', '09:40'], ['10:00', '11:40'], ['14:00', '15:40']];
  const days = Array.from({length:7}, (_, i) => '2026-11-'+(16+i)).map((date, index) =>
    ({date, weekday: index+1, courses: index ? [] : courses}));
  const context = vm.createContext({
    QQPlanner, plannerState: {days, config: {blocks}, workflow: {calendar_exams: exams, matters: {}}},
    document: {addEventListener() {}}, weekdays: ['周一','周二','周三','周四','周五','周六','周日'], lessonAction: () => '', esc: String, items,
  });
  vm.runInContext(fs.readFileSync(require.resolve('../web/schedule.js'), 'utf8'), context);
  return (expanded = false) => vm.runInContext('showScheduleBreaks='+expanded+'; scheduleGrid(items)', context);
}

test('one heading per teaching block without empty end-time rows or duplicate course times', () => {
  const render = timetable([{title:'软件工程', start_time:'08:00', end_time:'09:40'}]);
  const html = render();
  assert.equal((html.match(/class="lesson-band"/g) || []).length, 3);
  assert.doesNotMatch(html, /class="break-band"/);
  assert.match(html, /第 1–2 节<\/b><small>08:00–09:40/);
  assert.match(html, /class="schedule-event se-course"[^>]*><strong>软件工程/);
  assert.match(html, /data-slot="2026-11-17" data-start="08:00" data-end="09:40"/);
  assert.match(render(true), /data-start="09:40" data-end="10:00"/);
  assert.doesNotMatch(render(), /class="break-band"/);
});

test('an exam in the lunch break and a partial-period event keep their true intervals', () => {
  const exam = {course:'数学', date:'2026-11-16', start_time:'12:20', end_time:'13:30', matter_id:3};
  const render = timetable([{title:'讨论课', start_time:'08:30', end_time:'09:20'}], [exam]);
  const html = render();
  assert.equal((html.match(/第 1–2 节/g) || []).length, 1);
  assert.doesNotMatch(html, /class="break-band"/);
  const dayBand = text => text.match(/<tr class="day-band">.*?<\/tr>/s)[0];
  assert.match(dayBand(html), /12:20–13:30<\/small><strong>数学考试/);
  assert.match(html, /data-slot="2026-11-16" data-start="08:00" data-end="08:30"/);
  assert.match(html, /data-slot="2026-11-16" data-start="09:20" data-end="09:40"/);
  assert.doesNotMatch(html, /data-slot="2026-11-16" data-start="12:20"/);
  assert.doesNotMatch(dayBand(render(true)), /数学考试/);
  assert.match(render(true), /data-slot="2026-11-16" data-start="13:30" data-end="14:00"/);
});

test('a plan at 09:45 and an exam at 13:40 fit the same grid as an empty week', () => {
  const course = {title:'Java Web程序设计（产教融合课程）', start_time:'10:00', end_time:'11:40'};
  const exam = {course:'编译原理', date:'2026-11-18', start_time:'13:40', end_time:'15:40', matter_id:13};
  const task = {title:'第一次读书笔记审核与上交', planned_date:exam.date, planned_start:'09:45', planned_end:'10:15'};
  const render = timetable([course], [exam], [{key:'t3',task}]);
  const grid = html => [...html.matchAll(/<tr class="([^"]+)">(.*?)<\/tr>/gs)]
    .map(([,band,row]) => [band, (row.match(/<td>/g)||[]).length]);
  for(const expanded of [false,true]) {
    assert.deepEqual(grid(render(expanded)), grid(timetable([])(expanded)));
  }
  const html = render();
  assert.match(html, /09:45–10:15<\/small><strong>第一次读书笔记审核与上交/);
  assert.match(html, /13:40–15:40<\/small><strong>编译原理考试/);
  assert.match(html, /data-slot="2026-11-18" data-start="10:15" data-end="11:40"/);
  assert.doesNotMatch(html, /data-slot="2026-11-18" data-start="14:00"/);
  assert.match(html, /title="Java Web程序设计（产教融合课程） · 10:00–11:40"/);
});

test('unmatched college sessions do not appear on the personal timetable or the exam card', () => {
  const matter = {id:5, title:'第 12 周考试安排', topic:'考试', state:'acknowledged', class_filtered:true,
    exam_class:'B计算机241', exams:[], changes:[], date_facts:[], exam_sessions:[
      {date:'2026-11-25', start_time:'16:00', end_time:'18:00'}]};
  const plannerState = {days:[{date:'2026-11-25', weekday:3, courses:[]}], config:{blocks:[['08:00','09:40']]},
    workflow:{matters:{5:matter}, calendar_exams:[]}};
  const ctx = vm.createContext({plannerState, matter, weekdays:['周一','周二','周三'], esc:String,
    document:{addEventListener() {}}});
  vm.runInContext(fs.readFileSync(require.resolve('../web/schedule.js'), 'utf8'), ctx);
  assert.equal(vm.runInContext('examNotices(plannerState)',ctx),'');
  assert.equal(vm.runInContext('scheduleFacts(matter)',ctx),'');
  assert.doesNotMatch(vm.runInContext('scheduleGrid([])',ctx),/学院考试|16:00|18:00/);
});

test('all three views archive the same expired notices, including acknowledged ones', () => {
  let clock=Date.parse('2026-11-19T09:59:59+08:00');
  const archive={open:false};
  const notice=(id,title,facts)=>({id,title,date_facts:facts,tasks:[],exams:[],changes:[],group_name:'班群'});
  const early=notice(1,'早八提醒',[{value:'2026-11-19T08:00',end:'10:00',label:'上课'}]);
  const timeless=notice(2,'签到提醒',[]);
  const mixed=notice(3,'两场活动',[{value:'2026-11-17'},{value:'2026-11-20'}]);
  const task={id:10,title:'提交实验报告',status:'open',deadline:'2026-11-18',planned_date:'2026-12-02'};
  const update={...notice(4,'实验要求更新',[]),tasks:[task],state:'changed'};
  const acknowledged={...early,id:5,title:'两个活动今晚签到',state:'acknowledged'};
  const dismissed={...early,id:6,state:'dismissed'};
  const matters=Object.fromEntries([early,timeless,mixed,update,acknowledged,dismissed].map(m=>[m.id,m]));
  const days=Array.from({length:7},(_,i)=>({date:'2026-11-'+(9+i),weekday:i+1,courses:[]}));
  const context=vm.createContext({
    QQPlanner, Date:class extends Date { static now(){return clock;} },
    state:{tasks:[task]}, overviewState:null, esc:String, icon:()=>'',sourceIssue:()=>'',
    document:{addEventListener(){},querySelector:()=>archive},
    snapshot:{days,monday:days[0].date,today:'2026-11-19',config:{name:'',blocks:[]},week:10,courses:[],unscheduled:[],
      workflow:{matters,confirm:[early,timeless,mixed],updates:[update],fixed_exams:[],calendar_exams:[]}}
  });
  for(const file of ['planner','schedule']){
    vm.runInContext(fs.readFileSync(require.resolve('../web/'+file+'.js'),'utf8'),context);
  }
  const render=()=>vm.runInContext('plannerState=snapshot; homeView()',context);
  assert.match(render(),/title="查看需要确认"><strong>3/);
  clock+=1000;
  const html=render();
  assert.match(html,/title="查看需要确认"><strong>2/);
  const confirm=html.split('id="work-confirm"')[1].split('</section>')[0];
  assert.match(confirm,/section-count">2</);
  assert.doesNotMatch(confirm,/早八提醒/);
  assert.match(confirm,/签到提醒/);
  assert.match(confirm,/两场活动/);
  const items=vm.runInContext('matterListSections(Object.values(snapshot.workflow.matters).filter(m=>m.state!=="dismissed"),false).expired',context);
  assert.deepEqual(Array.from(items,m=>m.id),[1,5]);
  for(const page of [html,vm.runInContext('scheduleView()',context)]){
    const archive=page.match(/<details class="expired-notices"[^>]*>(.*?)<\/details>/s)[1];
    assert.match(archive,/expired-count">2</);
    assert.deepEqual(Array.from(archive.matchAll(/data-tab="messages" data-id="(\d+)"/g),m=>Number(m[1])),[1,5]);
  }
  assert.ok(html.indexOf('id="work-expired"')<html.indexOf('id="work-confirm"'));
  const next=html.split('id="work-next"')[1].split('</section>')[0];
  assert.match(next,/提交实验报告/);
  assert.match(next,/已逾期/);
  assert.doesNotMatch(next,/future-tasks/);
  archive.open=true;
  assert.match(render(),/id="work-expired" open/);
  early.read=true;
  for(const page of [render(),vm.runInContext('scheduleView()',context)]){
    assert.match(page,/expired-count">1</);
    assert.doesNotMatch(page,/早八提醒/);
  }
  assert.deepEqual(Array.from(vm.runInContext('matterListSections([snapshot.workflow.matters[1]],false,Date.now(),true).expired',context),m=>m.id),[1]);
});

test('schedule inbox and free-slot picker omit expired notices and put open overdue tasks first', () => {
  let now=Date.parse('2026-11-19T10:00:00+08:00');
  const notice=(id,facts,deadline=null)=>({id,title:'通知'+id,topic:'通知',candidate:true,group_name:'班群',
    state:'confirm',tasks:[],exams:[],changes:[],date_facts:facts,suggested_deadline:deadline});
  const expired=notice(1,[{kind:'event',value:'2026-11-19T08:00',end:'10:00'}]);
  const mixed=notice(2,[{kind:'event',value:'2026-11-20'}],'2026-11-10');
  const timeless=notice(3,[]);
  const task=(id,deadline,extra={})=>({id,title:'待办'+id,status:'open',deadline,...extra});
  const tasks=[task(10,'2026-11-19'),task(11,'2026-11-18'),task(12,'2026-11-17',{status:'done'}),
    task(13,'2026-11-18',{planned_date:'2026-11-23'}),task(14,'2026-11-19T10:00')];
  const days=Array.from({length:7},(_,i)=>({date:'2026-11-'+(16+i),courses:[],weekday:i+1}));
  const snapshot={monday:days[0].date,today:days[3].date,days,config:{blocks:[]},courses:[],unscheduled:[],
    workflow:{matters:{1:expired,2:mixed,3:timeless},confirm:[expired,mixed,timeless],calendar_exams:[],fixed_exams:[],updates:[]}};
  let formHTML;
  const captured=new Error('captured rendered form');
  const context=vm.createContext({snapshot,state:{tasks},QQPlanner,esc:String,icon:()=>'',
    Date:class extends Date {static now(){return now;}},
    document:{addEventListener(){},querySelector:()=>null},openModal(title,body){formHTML=body;throw captured;}});
  for(const name of ['planner','schedule']){
    vm.runInContext(fs.readFileSync(require.resolve('../web/'+name+'.js'),'utf8'),context);
  }
  const schedule=()=>vm.runInContext('plannerState=snapshot; scheduleView()',context);
  const page=schedule();
  assert.match(page,/id="schedule-expired">[^]*?data-tab="messages" data-id="1"/);
  assert.ok(page.indexOf('id="schedule-expired"')<page.indexOf('class="schedule-inboxes"'));
  const inbox=()=>schedule().split('<aside ')[1].split('</aside>')[0];
  const keys=text=>Array.from(text.matchAll(/(?:data-schedule-item|option value)="([tm]\d+)"/g),m=>m[1]);
  const expected=['t11','t14','m2','t10','m3'];
  assert.deepEqual(keys(inbox()),expected);
  assert.match(inbox(),/待安排 <small>5</);
  assert.throws(()=>vm.runInContext("scheduleDialog(null,{date:'2026-11-23',start:'10:00',end:'11:40'})",context),e=>e===captured);
  assert.match(formHTML,/<select name="choice"><option value="new">新建待办<\/option>/);
  assert.deepEqual(keys(formHTML),expected);
  assert.match(formHTML,/option value="t11">已逾期 · 待办11/);
  now-=1000;
  assert.ok(keys(inbox()).includes('m1'));
});

test('matter lists pin overdue open tasks across topics and retain expired notices as source links', () => {
  const at=Date.parse('2026-11-19T10:00:00+08:00');
  const dated={id:10,title:'早八通知',topic:'通知',date_facts:[{kind:'event',value:'2026-11-19T08:00',end:'10:00'}],
    changes:[],exams:[],tasks:[],state:'confirm',group_name:'班群'};
  const undated={...dated,id:11,title:'签到提醒',date_facts:[]};
  const task=(id,deadline,extra={})=>({id,title:'作业'+id,personal:true,status:'open',deadline,...extra});
  const rows=[dated,undated,task(1,'2026-11-19'),task(10,'2026-11-18',{topic:'学业',planned_date:'2026-12-02'}),
    task(20,'2026-11-17',{topic:'通知'}),task(30,null,{remind_at:1}),task(40,'2026-11-17',{status:'done'})];
  const ctx=vm.createContext({QQPlanner,rows,at,esc:String,fmt:String,icon:()=>'',
    document:{addEventListener(){}},snapshot:{workflow:{matters:{10:dated,11:undated}}}});
  for(const file of ['planner','schedule'])vm.runInContext(fs.readFileSync(require.resolve('../web/'+file+'.js'),'utf8'),ctx);
  vm.runInContext('plannerState=snapshot',ctx);
  const sections=vm.runInContext('matterListSections(rows,true,at)',ctx);
  assert.deepEqual(Array.from(sections.overdue,x=>x.id),[20,10]);
  assert.deepEqual(Array.from(sections.expired,x=>x.title),['早八通知']);
  assert.deepEqual(Array.from(sections.regular,x=>x.id),[11,1,30,40]);
  assert.equal(vm.runInContext('matterListSections(rows,false,at-1).expired.length',ctx),0);
  assert.match(vm.runInContext("matterCard(rows[0],'messages')",ctx),/data-tab="messages" data-id="10"/);
});

test('hour and minute menus preserve midnight, individual minutes and date-only deadlines', () => {
  const ctx=vm.createContext({document:{addEventListener(){}},esc:String});
  vm.runInContext(fs.readFileSync(require.resolve('../web/schedule.js'),'utf8'),ctx);
  const fields=ctx.clockField('截止时间','deadline','00:07');
  assert.match(fields, /value="00" selected/);
  assert.match(fields, /value="07" selected/);
  assert.equal((fields.match(/<option /g)||[]).length,24+60+2);
  const form={elements:{deadline_date:{value:'2026-11-19'},deadline_hour:{value:'00'},deadline_minute:{value:'07'}}};
  assert.equal(ctx.readDeadline(form),'2026-11-19T00:07');
  form.elements.deadline_hour.value='';
  assert.throws(()=>ctx.readDeadline(form),/小时/);
  form.elements.deadline_minute.value='';
  assert.equal(ctx.readDeadline(form),'2026-11-19');
});

test('confirmed exam without a personal plan is upcoming until its exam date', () => {
  const days=Array.from({length:7},(_,i)=>({date:'2026-11-'+(16+i),weekday:i+1,courses:[]}));
  const task={id:2,title:'编译原理考试',status:'open',due:null,remind_at:1};
  const exam={task_id:2,date:'2026-12-02',start_time:'13:40',end_time:'15:40'};
  const ctx=vm.createContext({QQPlanner,esc:String,icon:()=>'',sourceIssue:()=>'',state:{tasks:[task]},overviewState:null,
    document:{addEventListener(){},querySelector:()=>null},snapshot:{today:'2026-11-19',days,config:{name:'测试'},changes:[],
    workflow:{confirm:[],updates:[],matters:{},fixed_exams:[exam]}}});
  for(const file of ['planner','schedule'])vm.runInContext(fs.readFileSync(require.resolve('../web/'+file+'.js'),'utf8'),ctx);
  const html=vm.runInContext('plannerState=snapshot; homeView()',ctx);
  const next=html.split('id="work-next"')[1].split('</section>')[0];
  assert.doesNotMatch(next.split('class="future-tasks"')[0],/编译原理考试/);
  assert.match(next.split('class="future-tasks"')[1],/编译原理考试/);
  assert.match(next,/2026-12-02 13:40/);
  assert.match(next,/已添加提醒/);
});

test('course corrections show the newest notice and retain earlier originals under its matter', () => {
  const notes = ['更正为 C4+C5','实验做 C4','记得带报告册'].map((content,i) => ({
    id:3-i,content,kind:'实验内容',sender:'老师',group_name:'班群',sent_at:'10:00',
  }));
  const ctx = vm.createContext({notes, esc:String, fmt:String, document:{addEventListener(){}}});
  vm.runInContext(fs.readFileSync(require.resolve('../web/planner.js'),'utf8'),ctx);
  const html = vm.runInContext('courseNoticeDetails(notes,{7:{id:7,source_message_ids:[2,3]}})',ctx);
  assert.match(html,/更正为 C4\+C5.*<details><summary>此前通知（1）<\/summary>.*实验做 C4.*<\/details>.*记得带报告册/s);
  assert.equal((html.match(/data-action="message-detail"/g)||[]).length,3);
});
