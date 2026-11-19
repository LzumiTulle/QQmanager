'use strict';

function scheduleItems(now=Date.now()) {
  const flow = plannerState.workflow;
  const examIds = new Set(flow.fixed_exams.map(e => e.task_id));
  const sources = new Map();
  for (const matter of Object.values(flow.matters)) {
    for (const task of matter.tasks) sources.set(task.id, matter);
  }
  const tasks = state.tasks.filter(t => !examIds.has(t.id)).map(task => ({
    key:'t'+task.id, title:task.title, deadline:effectiveDeadline(task,sources.get(task.id)), group:task.group_name,
    task, matter:task.identity_task && sources.has(task.id)?{...sources.get(task.id),date_facts:[],suggested_deadline:task.deadline}:sources.get(task.id)
  }));
  const suggestions = [];
  for(const matter of Object.values(flow.matters)){
    if(!matter.candidate)continue;
    const {expiry,expired}=workflowTiming(matter,now);
    if(expired)continue;
    suggestions.push({expiresAt:expiry?.at,key:'m'+matter.id, title:matter.personal_title||matter.title, deadline:matter.suggested_deadline,
    group:matter.group_name, matter});
  }
  return [...tasks,...suggestions];
}

function effectiveDeadline(task,matter) {
  return task?.deadline || (task?.deadline_reviewed ? null : matter?.suggested_deadline);
}

function noticeMilestones(matter,limit) {
  if (!matter) return [];
  const dates=(matter.date_facts||[]).filter(f=>f.kind!=='deadline');
  if (limit) dates.push({kind:'deadline',value:limit,label:'截止'});
  else if (!matter.suggested_deadline) dates.push(...(matter.date_facts||[]).filter(f=>f.kind==='deadline'));
  return dates;
}

function dateCaption(value) {
  return value ? Number(value.slice(5,7))+'月'+Number(value.slice(8,10))+'日'+(value.slice(11)?' '+value.slice(11,16):'') : '';
}

function displayFacts(matter,limit) {
  let facts=noticeMilestones(matter,limit);
  if(matter?.topic==='考试') {
    if(matter.class_filtered)return [];
    const personal=matter.exams.filter(e=>e.date);
    const sessions=personal.length?personal:(matter.exam_class?[]:matter.exam_sessions||[]);
    if(sessions.length)facts=[...sessions.map(e=>({
      value:e.date+(e.start_time?'T'+e.start_time:''),end:e.end_time,
      label:personal.length?'匹配考试':'学院场次'
    })),...facts.filter(f=>f.kind==='deadline')];
  }
  if(!facts.length && limit)facts.push({kind:'deadline',value:limit,label:'截止'});
  return facts;
}

function expiryTag(expiry) {
  if(!expiry)return '';
  return `<em class="expiry-tag" data-expires="${expiry.at}" ${Date.now()<expiry.at?'hidden':''}>${expiry.label}</em>`;
}

function taskExpiryTags(t,exam,limit=t.deadline) {
  if(t.status==='done'||exam?.outside_class)return '';
  if(exam)return expiryTag(QQPlanner.expiryFor({value:exam.date,end:exam.end_time}));
  return expiryTag(QQPlanner.expiryFor({kind:'deadline',value:limit}))+expiryTag(QQPlanner.expiryFor({kind:'plan',value:t.planned_date,end:t.planned_end}));
}

function scheduleFacts(matter,limit) {
  const facts=displayFacts(matter,limit);
  if(!facts.length && matter?.week_start && !matter.class_filtered)return '<span>通知周次</span><b>'+dateCaption(matter.week_start)+' – '+dateCaption(dayShift(matter.week_start,6))+'</b>';
  return facts.map(f=>`<span>${esc(f.kind==='deadline'?'截止':f.label)}</span><b>${esc(dateCaption(f.value)+(f.period||'')+(f.end?'–'+f.end:''))}${matter?.state==='done'?'':expiryTag(QQPlanner.expiryFor(f))}</b>`).join('');
}

function waitingItems(items,now=Date.now()) {
  const waiting=[];
  for(const item of items){
    const {matter,task,deadline}=item;
    if(matter?.topic==='考试'&&!task?.identity_task)continue;
    if(task && (task.status!=='open'||task.planned_date))continue;
    const deadlineAt=QQPlanner.expiryFor({kind:'deadline',value:deadline})?.at ?? Infinity;
    waiting.push({...item,deadlineAt,overdue:Boolean(task && deadlineAt<=now)});
  }
  return waiting.sort((a,b)=>Number(b.overdue)-Number(a.overdue) || a.deadlineAt-b.deadlineAt || a.title.localeCompare(b.title,'zh-CN'));
}

function taskTimeAction(t) {
  return t.planned_date?'调整日程':t.deadline||t.remind_at?'编辑安排':'安排时间';
}
function planDescription(t) {
  const parts=[];
  if(t.planned_date)parts.push('计划 '+t.planned_date+(t.planned_start?' '+t.planned_start+'–'+t.planned_end:''));
  else if(t.status==='done')parts.push('已完成');
  else if(!t.deadline && !t.remind_at)parts.push('待安排');
  if(t.deadline)parts.push('截止 '+t.deadline.replace('T',' '));
  return parts.join(' · ');
}

function scheduleEvents(day,items) {
  const events = day.courses.map(c => ({
    kind:c.adjusted?'adjusted':'course', title:c.title, detail:[c.location,c.adjusted?'已调课':'',c.conflict?'时间冲突':''].filter(Boolean).join(' · '),
    date:day.date,start:c.start_time,end:c.end_time,courseId:c.id,notices:c.notices,action:lessonAction(c)
  }));
  for (const e of plannerState.workflow.calendar_exams.filter(e => e.date===day.date)) {
    events.push({date:day.date,done:e.status==='done',kind:'exam',title:e.course+'考试',detail:e.location,start:e.start_time,end:e.end_time,
      action:e.id?'data-plan="exam" data-key="'+esc(e.id)+'"':'data-plan="matter" data-id="'+e.matter_id+'"'});
  }
  for (const x of items) {
    const t=x.task;
    if (t?.planned_date===day.date) events.push({
      date:day.date,kind:t.status==='done'?'done':'task',title:t.title,detail:t.status==='done'?'已完成':'计划',
      start:t.planned_start,end:t.planned_end,action:'data-schedule-item="'+x.key+'"'
    });
  }
  return events;
}

function scheduleEventHTML(e, showTime=true) {
  const time=[e.start,e.end].filter(Boolean).join('–');
  const description=[e.title,time,e.detail].filter(Boolean).join(' · ');
  const expiry=!e.done && ['task','exam'].includes(e.kind)?expiryTag(QQPlanner.expiryFor({value:e.date,end:e.end,kind:e.kind==='task'?'plan':'event'})):'';
  const reminder=e.notices?.length?`<button class="course-notice" data-plan="course-notices" data-id="${e.courseId}" data-date="${e.date}" title="${esc(e.notices[0].content)}">课前提醒 · ${esc(e.notices[0].content)}</button>`:'';
  return `<button class="schedule-event se-${e.kind}" ${e.action} title="${esc(description)}">${showTime && time?`<small>${esc(time)}</small>`:''}<strong>${esc(e.title)}</strong>${e.detail||expiry?`<span>${esc(e.detail)}${expiry}</span>`:''}</button>${reminder}`;
}

function slotButton(date,start,end,partial=false) {
  const label = date+' '+start+'–'+end+' 安排待办';
  return `<button class="schedule-free" data-slot="${date}" data-start="${start}" data-end="${end}" title="${label}" aria-label="${label}">＋${partial?' '+start+'–'+end:''}</button>`;
}

let showScheduleBreaks=false;

function timetableBands(blocks,showBreaks) {
  const lessons=blocks.map(([start,end],block)=>({start,end,block})).sort((a,b)=>a.start.localeCompare(b.start));
  if(!showBreaks)return lessons;
  const bands=[];
  let cursor='07:00';
  for(const lesson of lessons){
    if(cursor<lesson.start)bands.push({start:cursor,end:lesson.start,block:-1});
    bands.push(lesson);
    if(cursor<lesson.end)cursor=lesson.end;
  }
  if(cursor<'22:00')bands.push({start:cursor,end:'22:00',block:-1});
  return bands;
}

function timetableCell(date,band,events) {
  const controls=[];
  let cursor=band.start;
  for(const event of events){
    if(cursor<event.start)controls.push(slotButton(date,cursor,event.start,true));
    const standardCourse=['course','adjusted'].includes(event.kind) && band.block>=0 && event.start===band.start && event.end===band.end;
    controls.push(scheduleEventHTML(event,!standardCourse));
    if(event.end>cursor)cursor=event.end;
  }
  if(cursor<band.end)controls.push(slotButton(date,cursor,band.end,events.length>0));
  return controls.join('');
}

function scheduleGrid(items) {
  const p=plannerState;
  const columns=p.days.map(day=>scheduleEvents(day,items));
  const bands=timetableBands(p.config.blocks,showScheduleBreaks);
  const timed=columns.map(events=>events.filter(e=>e.start && e.end)
    .sort((a,b)=>a.start.localeCompare(b.start) || a.end.localeCompare(b.end)));
  const cells=timed.map(events=>bands.map(b=>events.filter(e=>e.start<b.end && b.start<e.end)));
  const until=columns.map(()=>0);
  const rows=bands.map((band,i)=>{
    const {start,end,block}=band;
    const heading=block>=0?`<b>第 ${block*2+1}–${block*2+2} 节</b>`:'';
    return `<tr class="${block>=0?'lesson-band':'break-band'}"><th scope="row">${heading}<small>${start}–${end}</small></th>${cells.map((col,j)=>{
      if(i<until[j])return '';
      const events=col[i];
      // A single event occupying several whole periods gets one card across those rows.
      let next=i+1;
      const first=events[0];
      if(events.length===1 && first.start<=start && first.end>=end){
        while(next<bands.length && col[next].length===1 && col[next][0]===first && first.end>=bands[next].end)next++;
      }
      until[j]=next;
      const span=next-i;
      const content=span>1?scheduleEventHTML(first):timetableCell(p.days[j].date,band,events);
      return `<td${span>1?' rowspan="'+span+'"':''}><div class="schedule-cell">${content}</div></td>`;
    }).join('')}</tr>`;
  }).join('');
  const markers=columns.map((events,i)=>{
    const date=p.days[i].date;
    const limits=items.filter(x=>x.task?.status!=='done' && x.deadline?.startsWith(date));
    const notices=[];
    // Older timed tasks may only have a reminder and a deadline. Show their actual times.
    for(const x of items){
      if(!x.task?.remind_at || x.task.planned_date || x.task.status!=='open')continue;
      const reminder=reminderInput(x.task);
      if(reminder.startsWith(date) && !x.deadline?.startsWith(date))
        notices.push(`<button class="schedule-milestone" data-schedule-item="${x.key}">提醒 ${esc(reminder.slice(11))} · ${esc(x.title)}</button>`);
    }
    for (const m of Object.values(p.workflow.matters)) {
      if(['dismissed','done'].includes(m.state)||m.changes.length)continue;
      const exams=m.exams.filter(e=>!e.task&&(!e.class_slot||e.previous_task)&&e.date===date);
      for(const e of exams)notices.push(`<button class="schedule-milestone" data-plan="exam" data-key="${esc(e.id)}">考试待核对 · ${esc(e.course)} ${esc(e.start_time||'')}</button>`);
      if(m.topic==='考试'){
        if(!m.exam_class&&!m.exams.length)for(const slot of (m.exam_sessions||[]).filter(s=>s.date===date))
          notices.push(`<button class="schedule-milestone" data-plan="matter" data-id="${m.id}">学院考试 · ${esc(slot.start_time)}–${esc(slot.end_time)}</button>`);
        continue;
      }
      const task=items.find(x=>x.matter?.id===m.id);
      const limit=task?task.deadline:m.suggested_deadline;
      for(const f of noticeMilestones(m,limit).filter(f=>f.value.startsWith(date))) {
        if(f.kind==='deadline'&&limits.some(x=>x.matter?.id===m.id && x.deadline===f.value))continue;
        notices.push(`<button class="${f.kind==='deadline'?'schedule-limit':'schedule-milestone'}" data-plan="matter" data-id="${m.id}"><b>${esc(f.kind==='deadline'?'截止':f.label)} ${esc(f.value.slice(11)||f.period||'')}</b> ${esc(m.personal_title||m.title)}</button>`);
      }
    }
    return events.filter(e=>!e.start||!e.end||!bands.some(b=>e.start<b.end&&b.start<e.end)).map(e=>scheduleEventHTML(e)).join('')+limits.map(x=>{
      const reminder=x.task?.remind_at && !x.task.planned_date?reminderInput(x.task):'';
      const times=[reminder.startsWith(date)?'提醒 '+reminder.slice(11):'','截止 '+x.deadline.slice(11)].filter(Boolean).join(' · ');
      return `<button class="schedule-limit" data-schedule-item="${x.key}"><b>${esc(times)}</b> ${esc(x.title)}</button>`;
    }).join('')+notices.join('');
  });
  return `<div class="schedule-scroll"><table class="schedule-table" aria-label="本周日程"><thead><tr><th scope="col">时间</th>${p.days.map(d=>`<th scope="col" class="${d.date===p.today?'schedule-today':''}">${weekdays[d.weekday-1]}<strong>${Number(d.date.slice(8))}</strong></th>`).join('')}</tr></thead><tbody><tr class="day-band"><th scope="row">当日事项</th>${markers.map(m=>'<td><div class="schedule-cell">'+m+'</div></td>').join('')}</tr>${rows}</tbody></table></div>`;
}

function examNotices(p) {
  const cards=Object.values(p.workflow.matters).filter(m=>m.topic==='考试' && !m.class_filtered && m.state!=='dismissed').map(m=>{
    const slots=m.exams.filter(e=>e.date);
    const sessions=slots.length?slots:(m.exam_class?[]:m.exam_sessions||[]);
    const ordered=[...sessions].sort((a,b)=>(a.date+(a.start_time||'')).localeCompare(b.date+(b.start_time||'')));
    const first=ordered[0]?.date||m.week_start;
    const last=ordered.at(-1)?.date||(m.week_start&&dayShift(m.week_start,6));
    return {m,slots,ordered,first,last};
  }).filter(x=>!x.last||x.last>=p.today||(x.last>=p.monday && x.first<=p.days[6].date))
    .sort((a,b)=>(a.first||'9999').localeCompare(b.first||'9999'));
  if(!cards.length)return '';
  return `<section class="schedule-queue exam-notices"><h3>${icon('calendar')}考试安排</h3>${cards.map(({m,slots,ordered,first})=>{
    const description=ordered.map(e=>[slots.length?e.course:'',dateCaption(e.date),[e.start_time,e.end_time].filter(Boolean).join('–'),slots.length?e.location:''].filter(Boolean).join(' ')).join('、');
    return `<div class="exam-notice"><button class="queue-item" data-plan="matter" data-id="${m.id}"><strong>${esc(m.title)}</strong><span>${esc(description||(first?'考试周从'+dateCaption(first)+'开始，具体场次待核对':'具体场次待核对'))}</span><small>${slots.length?esc([...new Set(slots.map(examStatus))].join(' · ')):'学院通知'}</small></button>${first?`<button data-plan="exam-week" data-date="${esc(first)}">查看该周</button>`:''}</div>`;
  }).join('')}</section>`;
}

function scheduleView() {
  const p=plannerState;
  if(!p||p.days.length!==7)return '<p>正在载入日程…</p>';
  const now=Date.now(),items=scheduleItems(now),waiting=waitingItems(items,now).filter(x=>!(x.task?.remind_at && x.deadline && !x.overdue));
  const {expired,nextChange:noticeChange}=workflowNoticeSections(now);
  const nextChange=waiting.reduce((next,x)=>{
    const at=x.task?x.deadlineAt:x.expiresAt;
    return at>now?Math.min(next,at):next;
  },noticeChange);
  return `<div class="schedule-heading"><div><h2>${p.monday.slice(5).replace('-','/')} — ${p.days[6].date.slice(5).replace('-','/')}</h2><p>${esc(p.config.name)} · ${p.config.configured?'第 '+p.week+' 周':p.monday.slice(0,4)+' 年'}</p></div>
    <div class="calendar-nav"><button data-plan="week" data-offset="-7" aria-label="上一周">‹</button><button data-plan="today">本周</button><button data-plan="week" data-offset="7" aria-label="下一周">›</button><button data-plan="manage-courses">管理课程</button><button data-view="overview">消息概览${p.workflow.updates.length?' · '+p.workflow.updates.length+' 项变化':''}</button></div></div>
    ${expiredNoticePanel('schedule-expired',expired)}
    ${changeInbox()}<div class="schedule-inboxes"><aside class="schedule-queue"${Number.isFinite(nextChange)?` data-queue-until="${nextChange}"`: ''}><h3>${icon('list')}待安排 <small>${waiting.length}</small></h3>
    ${waiting.map(x=>`<button class="queue-item" data-schedule-item="${x.key}"><strong>${esc(x.title)}</strong><div class="queue-dates">${scheduleFacts(x.matter,x.deadline)}</div><small>${esc(x.group||'个人待办')}</small></button>`).join('')||'<p class="help">待安排已清空</p>'}</aside>
    ${examNotices(p)}</div>
    <section class="schedule-week"><div class="section-title"><h3>${p.days.some(d=>d.date===p.today)?'本周日程':'周日程'}</h3><label class="schedule-break-toggle"><input type="checkbox" id="show-schedule-breaks" ${showScheduleBreaks?'checked':''}>显示课外空档</label></div>${scheduleGrid(items)}</section>
    ${weekMatters().filter(m=>m.topic!=='考试').map(matterCard).join('')}${courseManagement()}`;
}

// Native select menus also open with a mouse in the macOS desktop WebView.
function clockField(label,name,value='',required=false){
  const [hour,minute]=String(value||'').split(':');
  function select(part,caption,size,current){
    const options=Array.from({length:size},(_,index)=>{
      const number=String(index).padStart(2,'0');
      return `<option value="${number}"${number===current?' selected':''}>${number}</option>`;
    });
    return `<select name="${name}_${part}" data-clock="${part}" aria-label="${esc(label)}（${caption}）"${required?' required':''}><option value="">${caption}</option>${options.join('')}</select>`;
  }
  return `<div class="clock-field" role="group" aria-label="${esc(label)}"><span>${esc(label)}</span><div class="clock-selects">${select('hour','时',24,hour)}<span aria-hidden="true">:</span>${select('minute','分',60,minute)}</div></div>`;
}
document.addEventListener('change',event=>{
  const input=event.target;
  if(input.dataset.clock!=='hour')return;
  const minute=input.parentElement.querySelector('[data-clock="minute"]');
  if(!input.value || !minute.value)minute.value=input.value?'00':'';
});
function readClock(form,name,validate=true){
  const hour=form.elements[name+'_hour'].value, minute=form.elements[name+'_minute'].value;
  if(validate && !hour && minute)throw Error('请选择小时');
  return hour?hour+':'+(minute||'00'):'';
}

function deadlineFields(value) {
  return `<div class="form-pair"><label>截止日期<input type="date" name="deadline_date" value="${esc(value?.slice(0,10)||'')}"></label>${clockField('截止时间（可选）','deadline',value?.slice(11,16))}</div>`;
}
function readDeadline(form) {
  const day=form.elements.deadline_date.value, time=readClock(form,'deadline');
  if(time && !day)throw Error('请先选择截止日期');
  return day+(day&&time?'T'+time:'') || null;
}

function scheduleDialog(key,slot={},draft=null) {
  const items=scheduleItems();
  const choices=draft?[draft]:key?items.filter(x=>x.key===key):waitingItems(items);
  if(!draft && !key && slot.date)choices.unshift({key:'new',title:'',deadline:null});
  if(!choices.length){
    if(key?.startsWith('m') && workflowFor(key.slice(1)))return matterDialog(Number(key.slice(1)));
    openModal('安排待办','<p>当前没有待安排的事项。</p><button data-action="new-task">新建待办</button>');return;
  }
  const first=choices[0],picking=!draft&&!key&&choices.length>1;
  const option=picking?`<label>安排内容<select name="choice">${choices.map(x=>`<option value="${x.key}">${x.overdue?'已逾期 · ':''}${x.key==='new'?'新建待办':esc(x.title)}</option>`).join('')}</select></label>`:'';
  openModal(first.task?'编辑安排':first.key==='new'&&!first.insight?'新建待办':'安排待办',`<form id="schedule-form" class="form-grid">${option}
    <label>待办内容<input name="title" maxlength="300" required></label>
    <div id="schedule-facts" class="notice-date-facts"></div><p class="suggestion-note" hidden></p>
    <label>计划日期<input type="date" name="planned_date"></label>
    <div class="form-pair">${clockField('计划开始','planned_start')}${clockField('计划结束','planned_end')}</div>
    <details class="schedule-deadline"><summary>编辑截止与提醒</summary><div class="deadline-editor"></div>
    <div class="form-pair"><label>提醒日期（北京时间）<input type="date" name="reminder_date"></label>${clockField('提醒时间','reminder')}</div>
    <button type="button" class="clear-reminder">清除提醒</button></details><div id="schedule-source"></div>
    <p class="help" id="plan-warning" role="status" hidden></p><p class="form-error" role="alert"></p>
    <div class="modal-actions"><button type="button" id="unplan" hidden>移除计划时段</button>
    <button type="button" id="finish-plan" hidden></button><button class="primary" type="submit">保存安排</button></div></form>`);
  const form=document.querySelector('#schedule-form');
  const fields=form.elements;
  const selected=()=>choices.find(x=>x.key===fields.choice?.value)||first;
  function setClock(name,value=''){
    const [h,m]=value.split(':');
    fields[name+'_hour'].value=h||'';fields[name+'_minute'].value=m||'';
  }
  function compareDeadline() {
    const date=fields.planned_date.value;
    const limit=fields.deadline_date.value,limitTime=readClock(form,'deadline',false);
    const facts=form.querySelector('#schedule-facts');
    const reminder=[fields.reminder_date.value,readClock(form,'reminder',false)].filter(Boolean).join('T');
    facts.innerHTML=scheduleFacts(selected().matter,limit+(limit&&limitTime?'T'+limitTime:''))+
      (fields.reminder_date.value?`<span>提醒</span><b>${esc(dateCaption(reminder))}</b>`:'');
    facts.hidden=!facts.textContent;
    const warning=form.querySelector('#plan-warning');
    warning.hidden=!(date && limit && (date>limit || (date===limit && limitTime && readClock(form,'planned_end',false)>limitTime)));
    warning.textContent=warning.hidden?'':'计划排在截止时间之后，仍可保存。';
  }
  let followDeadline=false,followReminder=false;
  function populate() {
    const x=selected(),t=x.task;
    const picture=!t && (x.insight?.picture || isOcrHeading(x.insight?.title) || isOcrHeading(x.matter?.title));
    const day=x.deadline?.slice(0,10)||plannerState.today;
    followDeadline=picture&&!x.deadline;followReminder=picture&&!x.insight?.due&&!x.deadline;
    const limit=picture && (!x.deadline || x.deadline.length===10)?day+'T20:00':x.deadline;
    const reminder=t?reminderInput(t):datetimeInput(x.insight?.due)||(picture?day+'T08:00':'');
    const savedReminder=t && !t.planned_date && reminder;
    const suggestion=plannerState.suggestions?.[x.key]||{};
    let initial=t?.planned_date?{date:t.planned_date,start:t.planned_start,end:t.planned_end}:suggestion;
    if(t?.status!=='done' && !t?.planned_date && (savedReminder || picture)){
      const start=reminder.slice(11,16);
      const minutes=Number(start.slice(0,2))*60+Number(start.slice(3))+30;
      initial={date:reminder.slice(0,10),start:minutes<1440?start:'',end:minutes<1440?String(Math.floor(minutes/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0'):'',
        reason:'按提醒日期预填，可调整计划时段'};
    }
    if(slot.date)initial=slot;
    if(picking) {
      const dialog=form.closest('dialog');
      const title=x.key==='new'?'新建待办':t?'编辑安排':'安排待办';
      dialog.querySelector('.modal-head h2').textContent=title;
      dialog.setAttribute('aria-label',title);
    }
    fields.title.value=x.title;
    fields.planned_date.value=initial.date||'';
    setClock('planned_start',initial.start||'');setClock('planned_end',initial.end||'');
    fields.reminder_date.value=reminder.slice(0,10);setClock('reminder',reminder.slice(11,16));
    form.querySelector('.deadline-editor').innerHTML=deadlineFields(limit);
    const note=form.querySelector('.suggestion-note');
    note.hidden=!initial.reason;note.textContent=initial.reason||'';
    form.querySelector('#unplan').hidden=!initial.date;
    const finish=form.querySelector('#finish-plan');finish.hidden=!t;finish.textContent=t?.status==='done'?'重新打开':'标记完成';
    form.querySelector('#schedule-source').innerHTML=x.matter?`<button type="button" class="schedule-source-button" aria-expanded="false">${icon('message')}查看群通知与附件 ${icon('arrow')}</button><div class="schedule-originals" hidden></div>`:'';
    compareDeadline();
  }
  form.addEventListener('input',event=>{
    const name=event.target.name||'';
    if(name.startsWith('planned_')){
      form.querySelector('.suggestion-note').hidden=true;
      form.querySelector('#unplan').hidden=!fields.planned_date.value;
    }
    if(name.startsWith('deadline_'))followDeadline=false;
    if(name.startsWith('reminder_'))followReminder=false;
    if(name==='planned_date' && event.target.value){
      if(followDeadline)fields.deadline_date.value=event.target.value;
      if(followReminder)fields.reminder_date.value=event.target.value;
    }
    compareDeadline();
  });
  form.addEventListener('change',compareDeadline);
  form.querySelector('#schedule-source').addEventListener('click',async e=>{
    const button=e.target.closest('.schedule-source-button');
    if(!button)return;
    const panel=button.nextElementSibling;
    panel.hidden=!panel.hidden;
    button.setAttribute('aria-expanded',String(!panel.hidden));
    if(panel.hidden||panel.dataset.loaded)return;
    panel.textContent='正在读取原文…';
    try {
      const detail=await api('/api/matter/'+selected().matter.id);
      panel.innerHTML=detail.messages.map(m=>`<article><small>${esc(m.group_name)} · ${fmt(m.sent_at)}</small><p>${esc(m.content)}</p>${attachmentsHTML(m.attachments)}</article>`).join('');
      panel.querySelectorAll('button').forEach(b=>b.type='button');
      panel.dataset.loaded='1';
    } catch(err){panel.textContent=err.message;}
  });
  populate();
  if(first.key==='new' && !first.title)fields.title.focus();
  fields.choice?.addEventListener('change',populate);
  form.querySelector('.clear-reminder').onclick=()=>{followReminder=false;fields.reminder_date.value='';setClock('reminder');compareDeadline();};
  form.querySelector('#unplan').onclick=()=>{
    fields.planned_date.value='';setClock('planned_start');setClock('planned_end');
    form.querySelector('.suggestion-note').hidden=true;form.querySelector('#unplan').hidden=true;compareDeadline();
  };
  let saving=false;
  async function save(finish=false) {
    if(saving)return;
    const x=selected(),t=x.task;
    const err=form.querySelector('.form-error');err.textContent='';
    saving=true;
    try {
      if(finish){
        await api('/api/tasks/'+t.id,{status:t.status==='done'?'open':'done'});
      }else{
        if(!t && x.matter && workflowTiming(x.matter).expired)throw Error('通知已过期，请重新核对');
        const start=readClock(form,'planned_start'),end=readClock(form,'planned_end'),date=fields.planned_date.value;
        if((start||end) && (!date||!start||!end||start>=end))throw Error('请选择计划日期，结束时间须晚于开始时间');
        const clock=readClock(form,'reminder'),day=fields.reminder_date.value;
        if(Boolean(clock)!==Boolean(day))throw Error('请同时选择提醒日期和时间，或清除提醒');
        const reminder=day?day+'T'+clock:'';
        const body={plan:{planned_date:date,planned_start:start,planned_end:end},task_id:t?.id};
        if(!t || fields.title.value!==t.title)body.title=fields.title.value;
        if(!t || reminder!==reminderInput(t))body.due=reminder?reminder+':00+08:00':null;
        const limit=readDeadline(form);
        if(!t || limit!==(x.deadline||null))body.deadline=limit;
        if(t)body.expected=[t.planned_date,t.planned_start,t.planned_end,t.deadline];
        else if(x.matter){body.matter_id=x.matter.id;body.revision=x.matter.revision;}
        else if(x.insight?.id)body.insight_id=x.insight.id;
        await api('/api/tasks/arrange',body);
        if(date && view==='home')plannerDate=date;
      }
      modal.close();await refresh();toast(finish?(t.status==='done'?'待办已重新打开':'待办已完成'):'安排已保存');
    }catch(error){err.textContent=error.message;}
    finally{saving=false;}
  }
  form.onsubmit=e=>{e.preventDefault();e.stopPropagation();save();};
  form.querySelector('#finish-plan').onclick=()=>save(true);
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-schedule-item],[data-slot]');
  if(b)scheduleDialog(b.dataset.scheduleItem,{date:b.dataset.slot,start:b.dataset.start,end:b.dataset.end});
});

document.addEventListener('change',e=>{
  if(e.target.id!=='show-schedule-breaks')return;
  showScheduleBreaks=e.target.checked;
  document.querySelector('.schedule-scroll').outerHTML=scheduleGrid(scheduleItems());
});
