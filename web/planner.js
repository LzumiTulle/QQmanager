'use strict';
let plannerState=null,plannerDate='',plannerDay='',plannerDraft=null,currentMatter=null,currentChange=null,matterTab='overview',homeScope='day';
const weekdays=['周一','周二','周三','周四','周五','周六','周日'];
function dayShift(d,n){const x=new Date(d+'T12:00:00Z');x.setUTCDate(x.getUTCDate()+n);return x.toISOString().slice(0,10);}
function weekLabel(ws){if(!ws?.length)return '';let out=[],start=ws[0],last=start;for(const n of ws.slice(1)){if(n===last+1){last=n;continue;}out.push(start===last?start:start+'-'+last);start=last=n;}out.push(start===last?start:start+'-'+last);return out.join(',');}
function isOcrHeading(title) {
 return /^【?图片文字识别/.test(title||'');
}
function matterTitle(matter) {
 if(!isOcrHeading(matter.title)) return matter.title;
 const schoolwork=/作业|习题|练习题|试问|课后题|\b\d{1,2}[-－]\d{1,2}\s*[|｜]/;
 return schoolwork.test([matter.title,matter.summary,...(matter.items||[]).map(i=>i.title)].join(' '))?'作业截图':'截图待核对';
}
function pictureSummary(e) {
 const images=e.attachments.filter(a=>a.kind==='image');
 const rows=images.map((a,n)=>a.file_id
   ? `<button data-plan="picture" data-id="${a.id}"><img loading="lazy" src="/api/preview/${a.file_id}" alt=""><span>查看原图 ${n+1}</span></button>`
   : `<button data-plan="matter-tab" data-tab="files">截图 ${n+1} · 查看获取状态</button>`);
 return `<p class="matter-summary">请对照截图核对具体要求。</p>
   <div class="picture-strip">${rows.join('')||'<button data-plan="matter-tab" data-tab="messages">查看群消息</button>'}</div>`;
}
function splitControls(e) {
 const links=[];
 if(e.split_parent) links.push(`<button data-plan="join-split" data-source="${e.id}" data-target="${e.split_parent.id}">合回原事项「${esc(matterTitle(e.split_parent))}」</button>`);
 for(const child of e.split_children||[]) links.push(`<button data-plan="join-split" data-source="${child.id}" data-target="${e.id}">收回「${esc(matterTitle(child))}」 · #${child.id}</button>`);
 if(e.items.length>1){
   const items=e.items.map(i=>{
     const image=e.attachments.find(a=>a.kind==='image'&&a.message_id===i.message_id);
     const n=e.attachments.filter(a=>a.kind==='image').indexOf(image)+1;
     const title=isOcrHeading(i.title)&&n?'截图 '+n:matterTitle(i);
     return `<button data-plan="split-matter" data-id="${i.id}">单独列出「${esc(title)}」</button>`;
   });
   links.push(...items);
 }
 return links.length?`<div class="split-items"><h3>拆分与合回</h3>${links.join('')}</div>`:'';
}
function matterCard(matter,tab='overview',allowMerge=false) {
 const flow = workflowFor(matter.id);
 const glyph = {confirm:'review', changed:'change', scheduled:'calendar', done:'done', acknowledged:'read'}[flow?.state] || 'file';
 const original=tab==='messages';
 const label = original?'已过期':workflowLabel(matter.id);
 const facts=original && flow?displayFacts(flow,effectiveDeadline(flow.tasks[0],flow)):[];
 const when=facts.length===1?dateCaption(facts[0].value)+(facts[0].period||'')+(facts[0].end?'–'+facts[0].end:''):'';
 return `<button class="info-row matter-row" ${allowMerge===true?'data-merge-row title="拖到另一条事项上合并"':''} data-plan="matter" ${original?'data-tab="messages"':''} data-id="${matter.id}" aria-label="查看${original?'原文':'事项'}：${esc(matterTitle(matter))}${label?'，'+esc(label):''}${matter.unread?'，有未读消息':''}">
   <span class="state-icon state-${esc(flow?.state || 'new')}">${icon(glyph)}</span>
   <span class="row-content"><span class="row-title">${matter.unread?unreadDot():''}${esc(matterTitle(matter))}</span><span class="row-meta">${esc(label)}${when?' · '+esc(when):''} · ${esc(matter.group_name)}${matter.message_count?' · '+matter.message_count+' 条消息':''}${matter.attachment_count?' · '+matter.attachment_count+' 个附件':''}${matter.saved?' · 已收藏':''}</span></span>
   ${matter.sent_at?`<time>${fmt(matter.sent_at)}</time>`:''}${icon('arrow')}</button>`;
}
function expiredNoticePanel(id,matters,allowMerge=false) {
 if(!matters.length)return '';
 const open=document.querySelector('#'+id)?.open;
 return `<details class="expired-notices" id="${id}"${open?' open':''}>
   <summary>${icon('pending')}<strong>已过期通知</strong><span class="expired-count">${matters.length}</span>
     <span class="expired-toggle" aria-hidden="true"><span>展开</span><span>收起</span>${icon('arrow')}</span></summary>
   <div class="info-list">${matters.map(m=>matterCard(m,'messages',allowMerge)).join('')}</div></details>`;
}
function weekMatters(){if(!plannerState)return [];return (state.matters||[]).filter(m=>m.status!=='dismissed'&&m.week_starts?.includes(plannerState.monday));}
function datedMatters(day){return (state.matters||[]).filter(m=>m.status!=='dismissed'&&!m.week_starts?.length&&m.dates.includes(day));}
function changeStatus(c){return {pending:'待确认调课',applied:'已调整',error:'识别待重试',unmatched:'需核对通知'}[c.state]||'调课';}
function changeButton(c){return `<button class="change-chip ${c.state==='applied'?'change-applied':'change-pending'}" data-plan="review-change" data-id="${c.id}">${changeStatus(c)} · ${esc(c.title)}</button>`;}
function changesForDay(day){return (plannerState?.changes||[]).filter(c=>['pending','applied'].includes(c.state)&&c.dates.includes(day));}
function changeInbox(){const rows=(plannerState?.changes||[]).filter(c=>c.state!=='applied');return rows.length?`<button class="pending-banner" data-plan="changes"><span><i aria-hidden="true">•</i> ${rows.length} 项调课等待确认</span><span>查看 →</span></button>`:'';}
function lessonAction(c){return `data-plan="${c.adjusted?'review-change':'edit-course'}" data-id="${c.adjusted?c.change_id:c.id}"`;}
function changeTime(row,prefix){const date=row[prefix+'_date'],start=row[prefix+'_start_period'],end=row[prefix+'_end_period'];if(!date||!start||!end)return '日期或节次待补充';const day=new Date(date+'T12:00:00Z').getUTCDay();const blocks=plannerState.config.blocks;return `${date} ${weekdays[(day+6)%7]} · ${start}–${end} 节<br>${blocks[Math.floor((start-1)/2)][0]}–${blocks[Math.floor((end-1)/2)][1]}`;}
async function changeDialog(id){const c=await api('/api/planner/change/'+id);currentChange=c;
 openModal('调课确认',`<div class="detail-meta"><span class="change-chip ${c.state==='applied'?'change-applied':'change-pending'}">${changeStatus(c)}</span><span>${esc(c.group_name)}</span></div><h2 class="detail-title">${esc(c.title)}</h2><p class="help">${c.state==='applied'?'日程表已更新，本次调整的课程如下。':'仅在你确认后更新以下课程日期，其他周次保持原安排。'}</p><div class="detail-actions">${c.state==='pending'?`<button class="primary" data-plan="confirm-change" data-id="${c.id}" ${c.ready?'':'disabled'}>确认并更新日程表</button>`:''}${c.state==='applied'?`<button data-plan="undo-change" data-id="${c.id}" ${c.can_undo?'':'disabled'}>撤销这次调课</button>`:`<button data-plan="retry-change" data-id="${c.id}">重新识别</button><button data-plan="dismiss-change" data-id="${c.id}">忽略这次调课</button>`}<button data-plan="matter" data-id="${c.matter_id}">相关消息与附件</button></div>${c.rows.map(r=>`<section class="change-row"><h3>${esc(r.course||'课程待匹配')}${r.already_applied?' · 已同步':r.expired?' · 已过期':''}</h3><div class="change-comparison"><div><small>原安排</small><p>${changeTime(r,'from')}</p><span>${esc(r.from_location||'教室待匹配')}</span></div><div class="change-applied"><small>新安排</small><p>${changeTime(r,'to')}</p><span>${esc(r.location??'沿用原教室')}</span></div></div>${r.issue?`<p class="warning">${esc(r.issue)}</p>`:''}<details><summary>通知依据</summary>${(r.evidence||[]).map(e=>`<blockquote class="source-text">${esc(e.quote)}</blockquote>`).join('')}</details></section>`).join('')}${c.note?`<details class="secondary-notices"><summary>识别补充</summary><p class="help">${esc(c.note)}</p></details>`:''}${c.error?`<p class="warning">${esc(c.error)}</p>`:''}${(c.issues||[]).length?`<p class="warning">${[...new Set(c.issues)].map(esc).join('<br>')}</p>`:''}${c.conflicts?.length?`<p class="warning">${c.conflicts.map(esc).join('<br>')}</p><label class="checkbox-label"><input type="checkbox" id="allow-change-conflicts">我已核对这些时间冲突，仍使用新安排</label>`:''}<details><summary>查看完整原文</summary>${c.messages.map(m=>`<p class="help">${fmt(m.sent_at)}</p><div class="source-text">${esc(m.content)}</div>`).join('')}</details>`,true);
}
function homeDay(){const p=plannerState;return p.days.find(d=>d.date===plannerDay)||p.days.find(d=>d.date===p.today)||p.days[0];}
function examStatus(e){return !e.task?(e.previous_task?'有新变化 · 待确认':e.class_slot?'已列入日程':'待核对原图'):e.task.status==='done'?'已完成':(e.task.remind_at||e.task.due)?'已添加提醒':'已添加待办 · 未设提醒';}
function relevantExams(){return (plannerState.exams?.events||[]).filter(e=>e.personal).map(e=>({...e,display_course:e.matches[0]||e.course}));}
function eventRow(e){return `<button class="event-row tone-${e.type}" ${e.action}><span class="event-marker" aria-hidden="true"></span><span class="event-body"><strong>${esc(e.title)}</strong><span class="event-top"><span class="event-kind">${({task:'待办',exam:'考试',change:'调课',notice:'通知'})[e.type]}</span>${e.time&&!['通知','调课'].includes(e.time)?`<time>${esc(e.time)}</time>`:''}${e.tags||''}${e.meta?`<span class="event-meta">${esc(e.meta)}</span>`:''}</span></span><span class="event-arrow" aria-hidden="true">›</span></button>`;}
function workflowFor(id){return plannerState?.workflow?.matters?.[String(id)];}
function workflowLabel(id){
 const flow=workflowFor(id);
 if(flow?.state==='confirm')return '待确认';
 if(flow?.label==='待安排' && flow.tasks.some(t=>t.status==='open' && t.deadline && t.remind_at))return '待完成';
 return flow?.label||'';
}
function workflowTiming(m,now=Date.now()){
 const facts=displayFacts(m,effectiveDeadline(m.tasks[0],m));
 const cutoffs=facts.map(QQPlanner.expiryFor);
 // A grouped notice stays current while any of its dated events is still ahead.
 const expiry=cutoffs.length && cutoffs.every(Boolean) && !m.recognizing && !m.exams.some(e=>!e.date)
   ? cutoffs.reduce((latest,next)=>next.at>latest.at?next:latest) : null;
 return {matter:m,facts,expiry,expired:Boolean(expiry && expiry.at<=now)};
}
function noticeTiming(m,now){
 if(!m || m.tasks.length || m.state==='changed' || m.state==='dismissed')return null;
 return workflowTiming(m,now);
}
function matterListSections(items,prioritizeTasks,now=Date.now(),includeRead=false){
 const regular=[],overdue=[],expired=[];
 let nextChange=Infinity;
 for(const item of items){
   if(item.personal){
     if(prioritizeTasks && item.status==='open'){
       const deadline=QQPlanner.expiryFor({kind:'deadline',value:item.deadline});
       if(deadline && deadline.at<=now){
         overdue.push({item,at:deadline.at});
         continue;
       }
       if(deadline)nextChange=Math.min(nextChange,deadline.at);
     }
   }else{
     const timing=noticeTiming(workflowFor(item.id),now);
     if(timing){
       if(timing.expired){
         if(includeRead || !timing.matter.read)expired.push(item);
         continue;
       }
       if(timing.expiry)nextChange=Math.min(nextChange,timing.expiry.at);
     }
   }
   regular.push(item);
 }
 overdue.sort((a,b)=>a.at-b.at || a.item.title.localeCompare(b.item.title,'zh-CN'));
 return {regular,expired,overdue:overdue.map(entry=>entry.item),nextChange};
}
function workflowNoticeSections(now){
 return matterListSections(Object.values(plannerState.workflow.matters),false,now);
}
function workflowRow({matter:m,facts,expiry}){
 const reason=m.recognizing?'正在核对附件中的安排，可先查看原文':m.exams.some(e=>e.previous_task&&!e.task)?'考试安排有变更，原提醒保留，确认后更新':m.changes.some(c=>c.state!=='applied')?'请核对调课前后时间，确认后同步课表':m.state==='changed'?'收到后续消息，请核对是否影响已有安排':m.exams.some(e=>!e.task)?'核对考试日期、班级及考场': '确认是否需要行动，或标记无需行动';
 const fact=facts.length===1?facts[0]:null;
 const when=fact?[fact.kind==='deadline'?'截止':fact.label,dateCaption(fact.value)+(fact.period||'')+(fact.end?'–'+fact.end:'')].filter(Boolean).join(' '):'';
 return eventRow({type:m.topic==='考试'?'exam':m.changes.length?'change':'notice',title:m.title,time:when,tags:expiryTag(expiry),meta:m.group_name+' · '+reason,action:`data-plan="matter" data-id="${m.id}"`});
}
function workModule(title,key,items,empty){return `<section class="overview-block work-module work-${key}" aria-label="${title}" id="work-${key}" tabindex="-1"><div class="section-title"><h2>${icon(key==='confirm'?'review':'change')}${title} <span class="section-count">${items.length}</span></h2></div>${items.length?items.map(item=>workflowRow(item)).join(''):`<p class="day-clear">${empty}</p>`}</section>`;}
function nextTaskRow(task,matter,fixedExam){
 const current=relevantExams().find(e=>e.task?.id===task.id);
 const exam=current||fixedExam;
 const when=exam?[exam.date,exam.start_time].filter(Boolean).join(' '):planDescription(task);
 const action=current?`data-plan="exam" data-key="${current.id}"`:matter?`data-plan="matter" data-id="${matter.id}"`:`data-action="task-edit" data-id="${task.id}"`;
 return `<div class="next-task">${eventRow({type:exam?'exam':'task',title:task.title,time:when,
   tags:taskExpiryTags(task,exam,effectiveDeadline(task,matter)),meta:exam?examStatus({...exam,task}):'',action})}</div>`;
}
function homeView(){
 const p=plannerState;if(!p)return '<p>正在载入…</p>';
 const d=homeDay(),days=homeScope==='day'?[d]:p.days,label={day:'当天',week:'本周',month:'本月'}[homeScope];
 const flow=p.workflow;
 const clock=Date.now(),{expired,nextChange}=workflowNoticeSections(clock);
 const pending=flow.confirm.filter(m=>!noticeTiming(m,clock)?.expired).map(m=>workflowTiming(m,clock));
 const updating=new Set(flow.updates.flatMap(m=>m.tasks.map(t=>t.id)));
 const fixedExams=new Map(flow.fixed_exams.map(e=>[e.task_id,e]));
 const taskMatters=new Map();
 for(const m of Object.values(flow.matters))for(const t of m.tasks)taskMatters.set(t.id,m);
 const end=days[days.length-1].date,now=[],later=[];
 for(const t of state.tasks){
   const exam=fixedExams.get(t.id);
   if(t.status!=='open'||exam?.outside_class)continue;
   const limit=effectiveDeadline(t,taskMatters.get(t.id));
   const cutoff=QQPlanner.expiryFor({kind:'deadline',value:limit});
   const overdue=cutoff && cutoff.at<=clock;
   if(updating.has(t.id)&&!overdue)continue;
   const day=(exam?.date||t.planned_date||limit||'').slice(0,10);
   if(overdue||day<=end)now.push(t);
   else later.push(t);
 }
 const nextCount=now.length+later.length;
 const renderTask=t=>nextTaskRow(t,taskMatters.get(t.id),fixedExams.get(t.id));
 const o=overviewState?.counts||{received:0,important:0};
 const shortDate=date=>Number(date.slice(5,7))+'月'+Number(date.slice(8))+'日';
 const title=homeScope==='day'?shortDate(d.date):homeScope==='month'?Number(days[0].date.slice(5,7))+'月':shortDate(days[0].date)+' — '+shortDate(days[days.length-1].date);
 const subtitle=[homeScope==='day'?weekdays[d.weekday-1]:'',p.config.name,homeScope!=='month'?'第 '+p.week+' 周':''].filter(Boolean).join(' · ');
 const applied=(p.changes||[]).filter(c=>c.state==='applied');
 return `<div class="home-overview"${Number.isFinite(nextChange)?` data-list-until="${nextChange}"`:''}><header class="overview-date-header"><div class="overview-date-heading"><h2>${title}</h2><p>${esc(subtitle)}</p></div><div class="overview-date-controls"><div class="scope-switch" role="group" aria-label="安排范围">${[['day','今天'],['week','本周'],['month','本月']].map(([v,t])=>`<button data-plan="scope" data-scope="${v}" aria-pressed="${homeScope===v}">${t}</button>`).join('')}</div><div class="calendar-nav"><button data-plan="period" data-offset="-1" aria-label="上一段时间">‹</button><button data-plan="today">回到今天</button><button data-plan="period" data-offset="1" aria-label="下一段时间">›</button></div></div></header>
 <div class="overview-summary" aria-label="事项处理进度"><button data-jump="work-confirm" aria-controls="work-confirm" title="查看需要确认"><strong>${pending.length}</strong><span>需要确认</span></button><button data-jump="work-next" aria-controls="work-next" title="查看待完成"><strong>${nextCount}</strong><span>待完成</span></button><button data-jump="work-updates" aria-controls="work-updates" title="查看有新变化"><strong>${flow.updates.length}</strong><span>有新变化</span></button></div>
 ${expiredNoticePanel('work-expired',expired)}
 <div id="source-issues">${sourceIssue()}</div>
 ${workModule('需要我确认','confirm',pending,'暂时没有需要确认的事项')}
 <section class="overview-block work-module work-next" aria-label="接下来要做" id="work-next" tabindex="-1"><div class="section-title"><h2>${icon('list')}接下来要做 <span class="section-count">${nextCount}</span></h2><small>${label}及未完成事项</small></div>${now.length?now.map(renderTask).join(''):`<p class="day-clear">${label}没有到期或逾期的待办</p>`}${later.length?`<details class="future-tasks" ${now.length?'':'open'}><summary>后续已安排 · ${later.length}</summary>${later.map(renderTask).join('')}</details>`:''}</section>
 ${workModule('已有安排的新变化','updates',flow.updates.map(m=>workflowTiming(m,clock)),'没有尚未处理的新变化')}
 ${applied.length?`<details class="synced-history"><summary>最近已同步的调课 · ${applied.length}</summary>${applied.map(c=>eventRow({type:'change',title:c.title,meta:'已同步到课表 · '+c.dates.join(' / '),action:`data-plan="review-change" data-id="${c.id}"`})).join('')}</details>`:''}
 <footer class="receipt-footer"><span>${label}收到 ${o.received} 条消息 · 筛出 ${o.important} 条重要消息${o.pending?' · '+o.pending+' 条分析中':''}${o.failed?' · '+o.failed+' 条分析失败':''}</span><div><button class="text-link" data-plan="important-receipts">查看重要事项</button><button class="text-link" data-plan="receipts" data-offset="0">查看全部消息 →</button></div></footer></div>`;
}
async function examDialog(key){const e=(plannerState.exams?.events||[]).find(e=>e.id===key);if(!e)return;openModal('考试安排 · '+examStatus(e),`<span class="exam-label">${e.by_class?'与我的班级匹配':'与课表课程匹配'}</span><h2>${esc(e.matches[0]||e.course||'考试科目待补充')}</h2>${e.approximate?`<p class="warning">科目按近似文字匹配，原识别为“${esc(e.course)}”，请对照原图确认。</p>`:''}${e.previous_event?`<p class="warning">原安排：${esc(e.previous_event.date)} ${esc(e.previous_event.start_time||'')} · ${esc(e.previous_event.location)}。下方为新通知的安排，确认前原提醒保持不变。</p>`:''}<div class="exam-facts"><div><small>日期</small><strong>${esc(e.date||'未确定')}</strong></div><div><small>时段</small><strong>${esc([e.start_time,e.end_time].filter(Boolean).join(' — ')||'未确定')}</strong></div><div><small>考场</small><strong>${esc(e.location||'未确定')}</strong></div><div><small>适用对象</small><strong>${esc(e.audience||'原文未明确')}</strong></div></div><p class="help">${e.task?`已关联待办。${e.task.status==='done'?'此待办已完成。':(e.task.remind_at||e.task.due)?'提醒时间：'+esc(fmt(e.task.remind_at||e.task.due))+'（北京时间）。':'尚未设置提醒时间。'}可在下方编辑，或到事项页查看。`:'来自群消息和附件识别。请核对科目、班级、日期及考场，确认后添加到个人待办。'}</p>${e.note?`<details class="secondary-notices"><summary>识别补充</summary><p class="help">${esc(e.note)}</p></details>`:''}<details ${e.needs_review?'open':''}><summary>日期与科目的原文依据</summary>${e.evidence.map(r=>`<blockquote class="source-text">${esc(r.quote)}</blockquote>`).join('')}</details><div class="detail-actions"><button class="primary" data-plan="exam-task" data-key="${e.id}">${e.task?'查看 / 编辑待办':e.previous_task?'确认变更并更新提醒':'核对后添加提醒'}</button>${e.task?`<button data-plan="exam-complete" data-key="${e.id}">${e.task.status==='done'?'重新打开待办':'标记完成'}</button>`:''}<button data-plan="matter" data-tab="files" data-id="${e.matter_id}">查看原图和文件</button><button data-plan="matter" data-tab="messages" data-id="${e.matter_id}">相关消息</button></div>`,true);}
async function receiptDialog(offset=0){const o=await api('/api/overview?'+new URLSearchParams({date:homeDay().date,span:homeScope,offset}));openModal('收到的消息',`<p class="help">${o.start === o.end ? o.start : o.start+' — '+o.end} · 共 ${o.counts.received} 条，重要 ${o.counts.important} 条。按收集时间排序。</p><div class="receipt-list">${o.messages.map(m=>`<button class="receipt-preview" data-action="message-detail" data-id="${m.id}"><span>${esc(m.content)}</span><small>${esc(m.group_name)} · ${esc(m.sender)} · 收到于 ${fmt(m.created)}${m.analysis_state==='filtered'?' · 已过滤':''}</small></button>`).join('')||'<p>暂无消息</p>'}</div><div class="modal-actions"><button data-plan="receipts" data-offset="${Math.max(0,offset-20)}" ${offset?'':'disabled'}>上一页</button><span>${o.counts.received?offset+1:0}–${Math.min(offset+20,o.counts.received)} / ${o.counts.received}</span><button data-plan="receipts" data-offset="${offset+20}" ${offset+20<o.counts.received?'':'disabled'}>下一页</button></div>`,true);}
function examSummaryCard(e){return eventRow({type:'exam',matterId:e.matter_id,title:e.display_course||e.matches[0]||e.course,tags:e.task?.status==='done'?'':expiryTag(QQPlanner.expiryFor({value:e.date,end:e.end_time})),time:e.date?Number(e.date.slice(5,7))+'月'+Number(e.date.slice(8))+'日':'日期待确定',meta:[e.start_time,e.location,e.audience,examStatus(e)].filter(Boolean).join(' · '),action:`data-plan="exam" data-key="${e.id}"`});}
function courseManagement(){const p=plannerState;return `<details class="course-management"><summary>管理课程与校历 <span>${p.courses.length} 条安排</span></summary><div class="management-actions"><button data-plan="upload-courses">导入课表</button><button data-plan="add-course">添加课程</button><button data-plan="calendar-config">校历与作息</button><button data-plan="upload-calendar">导入校历</button></div><p class="help">${p.config.configured?esc(p.config.anchor_date)+' 为第 '+p.config.anchor_week+' 周':'尚未设置校历'} · 个人选修课可自行添加。</p><div class="course-list">${p.courses.map(c=>`<div><button data-plan="edit-course" data-id="${c.id}"><strong>${esc(c.title)}</strong><small>${c.weekday?weekdays[c.weekday-1]+' '+c.start_period+'-'+c.end_period+'节':'待安排'} · 第 ${weekLabel(c.weeks)} 周 · ${esc(c.location)}${c.enabled?'':' · 已停用'}</small></button><button data-plan="toggle-course" data-id="${c.id}">${c.enabled?'停用':'启用'}</button></div>`).join('')}</div></details>${p.unscheduled.length?`<details class="secondary-notices"><summary>本周待安排 · ${p.unscheduled.length}</summary>${p.unscheduled.map(c=>`<button class="brief-matter" data-plan="edit-course" data-id="${c.id}"><span>${esc(c.title)}</span><small>缺少上课时间</small></button>`).join('')}</details>`:''}`;}
function calendarDialog(){const c=plannerState.config;openModal('校历与作息',`<form data-planner="calendar" class="form-grid"><label>学期名称<input name="name" value="${esc(c.name)}" required></label><div class="form-pair"><label>已知日期<input type="date" name="anchor_date" value="${c.anchor_date}" required></label><label>这天是第几周<input type="number" name="anchor_week" min="1" max="60" value="${c.anchor_week}" required></label></div><label>学期总周数<input type="number" name="total_weeks" min="1" max="60" value="${c.total_weeks}" required></label><p class="help">按周一到周日计算。调整这里会更新课程日期和考试周标记，不会改变原始通知中的日期。</p><details><summary>上课时间</summary>${c.blocks.map((b,i)=>`<div class="form-pair">${clockField('第 '+(i*2+1)+'–'+(i*2+2)+' 节开始','start'+i,b[0],true)}${clockField('第 '+(i*2+1)+'–'+(i*2+2)+' 节结束','end'+i,b[1],true)}</div>`).join('')}</details><button type="submit" class="primary">保存校历</button><p class="form-error" role="status"></p></form>`);}
function courseDialog(c={}){openModal(c.id?'调整课程':'添加课程',`<form data-planner="course" data-id="${c.id||''}" class="form-grid"><label>课程名称<input name="title" value="${esc(c.title||'')}" required maxlength="160"></label><div class="form-pair"><label>星期<select name="weekday">${[0,1,2,3,4,5,6,7].map(d=>`<option value="${d}" ${d===(c.weekday??1)?'selected':''}>${d?weekdays[d-1]:'待安排'}</option>`).join('')}</select></label><label>上课周次<input name="weeks" value="${weekLabel(c.weeks)||'1-'+plannerState.config.total_weeks}" placeholder="1-16 或 1-16单 或 3,5,7" required></label></div><div class="form-pair"><label>开始节次<input type="number" name="start_period" min="0" max="10" value="${c.start_period??1}" required></label><label>结束节次<input type="number" name="end_period" min="0" max="10" value="${c.end_period??2}" required></label></div><label>教室<input name="location" value="${esc(c.location||'')}"></label><label>教师<input name="teacher" value="${esc(c.teacher||'')}"></label><label>备注<textarea name="note">${esc(c.note||'')}</textarea></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${c.enabled===false?'':'checked'}>启用这门课</label><div class="inline-actions"><button type="submit" class="primary">保存课程</button>${c.id?`<button type="button" data-plan="delete-course" data-id="${c.id}">移除课程</button>`:''}</div><p class="form-error" role="status"></p></form>`);}
function uploadPlanner(kind){openModal(kind==='courses'?'导入课表':'导入校历',`<form data-planner="import" data-kind="${kind}" class="form-grid"><label>选择${kind==='courses'?'课表':'校历'}文件<input type="file" name="file" accept=".png,.jpg,.jpeg,.webp,.xls,.xlsx,.csv,.txt,.pdf,.docx" required></label><p class="help">支持图片、Excel、文本和文档，最大 20 MB。图片在本机识别文字，再交给已配置模型整理；先预览，再保存。课程默认全部启用，个人选修课可另行添加。</p><label class="checkbox-label"><input type="checkbox" name="allow_model" required>允许将脱敏后的课表文字与表格结构发送到 ${esc(state.llm.url)} 的 ${esc(state.llm.model)} 进行整理，原文件不发送。</label><button type="submit" class="primary">识别并预览</button><p class="form-error" role="status"></p></form>`);}
function previewPlanner(draft,kind){plannerDraft={...draft,kind};openModal('导入预览',`<p class="help">${kind==='courses'?`识别到 ${draft.courses.length} 条课程安排，保存后可逐门调整。`:'识别到以下校历关系，保存后将替换当前周数设置。'}</p>${draft.warnings.map(w=>`<p class="warning">${esc(w)}</p>`).join('')}${kind==='courses'?`<div class="import-course-preview">${draft.courses.map(c=>`<div><strong>${esc(c.title)}</strong><small>${c.weekday?weekdays[c.weekday-1]+' '+c.start_period+'-'+c.end_period+'节':'待安排'} · ${weekLabel(c.weeks)} 周<br>${esc(c.location)} · ${esc(c.teacher)}</small></div>`).join('')}</div>`:`<p>${esc(draft.calendar.name)} · ${esc(draft.calendar.anchor_date)} 为第 ${draft.calendar.anchor_week} 周 · 共 ${draft.calendar.total_weeks} 周</p>`}<p><a href="/api/download/${draft.file_id}" download>下载导入原件</a></p><div class="modal-actions"><button data-plan="apply-import" class="primary">${kind==='courses'?'保存全部课程':'使用此校历'}</button><button data-action="close-modal">暂不保存</button></div>`);}
function matterWorkflowHTML(e,f,expired) {
  if(!f || f.state==='dismissed')return '';
  const specialized=f.exams.length || f.changes.length;
  const parts=[];
  if(f.state==='changed')parts.push('<p class="warning">有后续通知，请核对已有安排。</p>');
  parts.push(...f.exams.map(examSummaryCard),...f.changes.map(changeButton));
  if(f.class_filtered){
    parts.push(`<p class="help">已识别的考试场次中未找到 ${esc(f.exam_class)}。可在附件中查看学院原表。</p>`);
  }
  if(!specialized && !f.class_filtered) {
    for(const t of f.tasks) {
      const when=t.planned_date?[t.planned_date,[t.planned_start,t.planned_end].filter(Boolean).join('–')].filter(Boolean).join(' '):'';
      parts.push(`<div class="matter-task-plan">${t.title!==e.title?`<strong>${esc(t.title)}</strong>`:''}
        ${when?`<p>计划 · ${esc(when)}</p>`:''}
        <div class="matter-buttons">${t.status==='open'?`<button class="primary" data-schedule-item="t${t.id}">${taskTimeAction(t)}</button>`:''}
        ${t.status==='done'?`<button data-plan="matter-primary" data-id="${e.id}" data-task-id="${t.id}">编辑待办</button>`:''}
        <button data-plan="matter-complete" data-id="${t.id}">${t.status==='done'?'重新打开':'标记完成'}</button></div></div>`);
    }
    if(!f.tasks.length) {
      const add=f.candidate?'':`<button class="primary" data-plan="matter-primary" data-id="${e.id}">添加待办</button>`;
      const schedule=f.candidate?`<button class="primary" data-schedule-item="m${e.id}">${icon('calendar')}安排时间</button>`:'';
      const acknowledge=f.state==='confirm'?`<button data-plan="workflow-decision" data-id="${e.id}" data-decision="acknowledged">无需行动</button>`:'';
      if(!expired) parts.push(`<div class="matter-buttons">${schedule}${add}${acknowledge}</div>`);
    }
  }
  if(f.state==='changed'&&!f.recognizing&&!f.exams.some(x=>!x.task)&&!f.changes.some(x=>x.state!=='applied'))
    parts.push(`<button data-plan="workflow-decision" data-id="${e.id}" data-decision="reviewed">保留现有安排</button>`);
  if(f.recognizing)parts.push('<p class="help">正在识别考试附件</p>');
  return parts.join('');
}

async function matterDialog(id,tab='overview') {
  await refresh(false);
  const e=await api('/api/matter/'+id), f=workflowFor(e.id);
  currentMatter=e;currentMatter.workflow=f;
  const expired=noticeTiming(f)?.expired;
  const unread=state.matters.find(m=>m.id===e.id)?.unread ?? (e.status==='new');
  matterTab=['overview','files','messages'].includes(tab)?tab:'overview';
  let body='';
  if(matterTab==='files') {
    body=(e.groups||[e.group_name]).map(g=>{
      const files=e.attachments.filter(a=>a.group_name===g);
      return files.length?`<section class="source-group"><h3>${esc(g)}</h3>${attachmentsHTML(files)}</section>`:'';
    }).join('')||'<p>暂无附件</p>';
    body+=(e.documents||[]).map(doc=>`<details class="panel"><summary>附件中的分工与条件 · ${esc(doc.name)}</summary>
      ${doc.scope?`<p>核查对象 · ${esc(doc.scope)}</p>`:''}
      ${doc.duties.map(d=>`<p><strong>${esc(d.actor)}</strong>${d.condition?` · ${esc(d.condition)}`:''}<br>${esc(d.action||d.quote)}</p>`).join('')}</details>`).join('');
  } else if(matterTab==='messages') {
    body=originalMessages(e.messages,e.items).map(originalHTML).join('');
  } else {
    const summary=f?.candidate && f.personal_title ? f.personal_title : e.summary;
    const showPictures=isOcrHeading(summary)||isOcrHeading(e.title);
    const limit=effectiveDeadline(f?.tasks[0],f);
    body=`<section class="matter-overview">
      ${showPictures?pictureSummary(e):summary&&summary!==e.title?`<p class="matter-summary">${esc(summary)}</p>`:''}
      <div class="notice-date-facts">${scheduleFacts(f,limit)}</div>
      ${e.date_conflict?'<p class="warning">通知日期与教学周不一致，请核对原文。</p>':''}
      ${matterWorkflowHTML(e,f,expired)}</section>`;
  }
  const tabs=[['overview','概览'],['messages','原文 '+e.messages.length]];
  if(e.attachments.length)tabs.push(['files','附件 '+e.attachments.length]);
  const tools=matterTab!=='overview'?'':`<section class="matter-tools"><h3>整理事项</h3><div class="matter-buttons">
    ${!expired&&unread?'<button data-plan="matter-status" data-status="read">标记已读</button>':''}
    <button data-plan="matter-status" data-status="${e.saved?'new':'saved'}">${e.saved?'取消收藏':'收藏'}</button>
    <button data-plan="merge-matter" data-direction="in">将其他事项合入这里…</button>
    <button data-plan="merge-matter" data-direction="out">合并到其他事项…</button>
    <button data-plan="matter-status" data-status="${e.status==='dismissed'?'new':'dismissed'}">${e.status==='dismissed'?'恢复事项':'忽略事项'}</button></div>
    ${splitControls(e)}</section>`;
  openModal('事项详情',`<h2 class="detail-title">${esc(matterTitle(e))}</h2><div class="detail-meta"><strong class="workflow-status">${esc(expired?'已过期':workflowLabel(e.id))}</strong><span>${esc(e.group_name)}</span></div>
    <div class="tabs detail-tabs" role="tablist" aria-label="事项内容">${tabs.map(([key,label])=>`<button role="tab" aria-selected="${matterTab===key}" class="${matterTab===key?'active':''}" data-plan="matter-tab" data-tab="${key}">${label}</button>`).join('')}</div>${expired&&e.status==='new'?'<button class="primary" data-plan="matter-status" data-status="read">标记已读并收起</button>':''}${body}${tools}`,true);
  if(matterTab==='messages') await readOriginals(e.messages.map(m=>m.id));
}
async function plannerRefresh(){await refresh();}
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-plan]');if(!b)return;const action=b.dataset.plan,id=Number(b.dataset.id);b.disabled=true;
 try{
  if(action==='workflow-decision'){const f=currentMatter?.id===id?currentMatter.workflow:workflowFor(id);await api('/api/workflow/decision',{id,revision:f.revision,decision:b.dataset.decision});await plannerRefresh();await matterDialog(id);toast('处理状态已同步');return;}
  if(action==='matter-complete'){const t=state.tasks.find(t=>t.id===id);await api('/api/tasks/'+id,{status:t.status==='done'?'open':'done'});await plannerRefresh();return matterDialog(currentMatter.id);}
  if(action==='picture'){
    const image=currentMatter.attachments.find(a=>a.id===id && a.kind==='image' && a.file_id);
    if(image) openModal('截图原图',`<button data-plan="matter" data-id="${currentMatter.id}">‹ 返回事项</button>
      <a href="/api/preview/${image.file_id}" target="_blank" rel="noopener"><img class="attachment-image" src="/api/preview/${image.file_id}" alt="群消息原图"></a>`,true);
    return;
  }
  if(action==='matter-primary'){const f=currentMatter?.id===id?currentMatter.workflow:workflowFor(id);if(f?.exams.length||f?.changes.length)return matterDialog(id);const task=f?.tasks.find(t=>t.id===Number(b.dataset.taskId))||f?.tasks[0];const matter=currentMatter?.id===id?currentMatter:f;return taskDialog({matter_id:id,revision:f?.revision,suggested_deadline:f?.suggested_deadline,picture:isOcrHeading(matter.title)||isOcrHeading(matter.summary),title:matterTitle(matter)},task||null);}
  if(action==='merged-history'){openModal('合并记录',`<p class="help">这些重复待办已合并，不会重复提醒。原记录保留，可单独恢复。</p>${(state.task_merges||[]).map(m=>`<div class="merge-record"><strong>${esc(m.title)}</strong><small>${esc(fmt(m.due))}</small><button data-plan="restore-merge" data-id="${m.duplicate_id}">恢复这条记录</button></div>`).join('')}`);return;}
  if(action==='restore-merge'){await api('/api/tasks/restore-merge',{id});modal.close();await plannerRefresh();toast('原待办已恢复');return;}
  if(action==='exam')return await examDialog(b.dataset.key);
  if(action==='exam-complete'){const e=plannerState.exams.events.find(e=>e.id===b.dataset.key);await api('/api/tasks/'+e.task.id,{status:e.task.status==='done'?'open':'done'});await plannerRefresh();return examDialog(e.id);}
  if(action==='exam-task'){const e=plannerState.exams.events.find(e=>e.id===b.dataset.key);return taskDialog({exam_id:e.id,title:(e.matches[0]||e.course)+'考试'+(e.location?' · '+e.location:''),due:e.date&&e.start_time?e.date+'T'+e.start_time+':00+08:00':null,date_note:e.task?'修改提醒时间不会改变考试时间。':e.previous_task?'确认后更新原考试待办，不会新建重复提醒。':'请先核对原图中的日期、时段、班级及考场，再设置提醒。'},e.task||null);}
  if(action==='receipts')return await receiptDialog(Number(b.dataset.offset)||0);
  if(action==='important-receipts'){openModal('筛选出的重要事项',`<p class="help">${overviewState?.counts.important||0} 条重要消息合并为 ${overviewState?.important.length||0} 个事项。</p>${(overviewState?.important||[]).map(matterCard).join('')||'<p>当前时段暂无重要事项</p>'}`,true);return;}
  if(action==='scope'){homeScope=b.dataset.scope;plannerDate='';plannerDay='';return await plannerRefresh();}
 if(action==='open-day'){homeScope='day';plannerDate=b.dataset.date;plannerDay=b.dataset.date;return await plannerRefresh();}
 if(action==='period'){const d=homeDay().date,n=Number(b.dataset.offset);plannerDate=QQPlanner.shift(d,n,homeScope);plannerDay=plannerDate;return await plannerRefresh();}
 if(action==='matter-tab')return await matterDialog(currentMatter.id,b.dataset.tab);
  if(action==='changes'){const rows=(plannerState.changes||[]).filter(c=>c.state!=='applied');openModal('待确认调课',`<div class="change-queue">${rows.map(changeButton).join('')||'<p class="help">没有待确认的调课。</p>'}</div>`);return;}
  if(action==='review-change')return await changeDialog(id);
  if(action==='confirm-change'){await api('/api/planner/change/confirm',{id,token:currentChange.token,confirmed:true,allow_conflicts:!!document.querySelector('#allow-change-conflicts')?.checked});await plannerRefresh();await changeDialog(id);toast('日程表已更新');return;}
  if(['undo-change','retry-change','dismiss-change'].includes(action)){await api('/api/planner/change/action',{id,action:action.split('-')[0]});modal.close();await plannerRefresh();toast(action==='undo-change'?'已恢复之前的课程安排':action==='retry-change'?'已安排重新识别':'已忽略，课表未改动');return;}
  if(action==='matter')return await matterDialog(id,b.dataset.tab||'overview');
  if(action==='manage-courses'){
    const panel=document.querySelector('.course-management');
    panel.open=true;panel.querySelector('summary').focus();panel.scrollIntoView({block:'start'});return;
  }
  if(action==='exam-week'){
    plannerDate=b.dataset.date;plannerDay=plannerDate;
    await plannerRefresh();document.querySelector('.schedule-week')?.scrollIntoView({block:'start'});return;
  }
  if(action==='week'||action==='today'){plannerDate=action==='today'?'':dayShift(plannerState.monday,Number(b.dataset.offset));plannerDay='';return await plannerRefresh();}
  if(action==='day') {
    const day = plannerState.days.find(d => d.date === b.dataset.date);
    if (!day) return;
    const rows = day.courses.map(c => `<button class="info-row" ${lessonAction(c)}><span class="row-content"><span class="row-title">${esc(c.title)}</span><span class="row-meta">${esc(c.location || '教室未定')}${c.adjusted ? ' · 已调课' : ''}</span></span><time>${esc(c.start_time)}–${esc(c.end_time)}</time>${icon('arrow')}</button>`);
    openModal(day.date+' '+weekdays[day.weekday-1]+'的课程', rows.length ? rows.join('') : textEmpty('当天没有课程','可在课表中选择其他日期。'));
    return;
  }
  if(action==='calendar-config')return calendarDialog();
  if(action==='add-course')return courseDialog();
  if(action==='course-notices'){
    const lesson=plannerState.days.find(day=>day.date===b.dataset.date)?.courses.find(c=>c.id===id);
    if(lesson)openModal(lesson.title+' · 课前提醒',lesson.notices.map(n=>`<article class="course-notice-detail"><small>${esc(n.group_name)} · ${esc(n.sender)} · ${fmt(n.sent_at)}</small><p>${esc(n.content)}</p><button data-action="message-detail" data-id="${n.id}">查看原文</button></article>`).join(''));
    return;
  }
  if(action==='edit-course')return courseDialog(plannerState.courses.find(c=>c.id===id));
  if(action==='upload-courses'||action==='upload-calendar')return uploadPlanner(action==='upload-courses'?'courses':'calendar');
  if(action==='apply-import'){const r=await api('/api/planner/apply',{id:plannerDraft.id});modal.close();await plannerRefresh();toast(plannerDraft.kind==='courses'?`已保存 ${r.added} 条课程安排`:'校历已更新');return;}
  if(action==='toggle-course'){const c=plannerState.courses.find(c=>c.id===id);await api('/api/planner/course',{...c,enabled:!c.enabled});return await plannerRefresh();}
  if(action==='delete-course'){await api('/api/planner/course/delete',{id});await plannerRefresh();openModal('课程已移除',`<p>该课程已从个人课表移除。</p><button data-plan="restore-course" data-id="${id}">撤销移除</button>`);return;}
  if(action==='restore-course'){await api('/api/planner/course/restore',{id});modal.close();return await plannerRefresh();}
  if(action==='matter-status'){const ids=currentMatter.messages.map(m=>m.id);await api('/api/matter',{id:currentMatter.id,status:b.dataset.status});if(b.dataset.status==='read')await readOriginals(ids);modal.close();return await plannerRefresh();}
  if(action==='matter-task')return matterDialog(currentMatter.id);
  if(action==='split-matter'){
    const parent=currentMatter.id;
    const result=await api('/api/matter',{id:parent,split_insight:id});
    await plannerRefresh();await matterDialog(result.id||parent);toast('已拆成独立事项');return;
  }
  if(action==='join-split'){
    const target=Number(b.dataset.target);
    await api('/api/matter',{id:Number(b.dataset.source),rejoin_parent:target});
    await plannerRefresh();await matterDialog(target);toast('已合回原事项');return;
  }
  if(action==='merge-matter')return await openMergePicker(currentMatter,b.dataset.direction==='in');
 }catch(e){toast(e.message);}finally{b.disabled=false;}
});
document.addEventListener('submit',async event=>{
 const f=event.target;if(!f.dataset.planner)return;event.preventDefault();event.stopImmediatePropagation();const d=new FormData(f),b=f.querySelector('[type=submit]'),err=f.querySelector('.form-error');if(b.disabled)return;b.disabled=true;if(err)err.textContent='';
 try{
  if(f.dataset.planner==='course'){const value=Object.fromEntries(d);value.id=Number(f.dataset.id)||undefined;value.enabled=d.has('enabled');if(value.weekday==='0')value.start_period=value.end_period=0;await api('/api/planner/course',value);modal.close();await plannerRefresh();toast('课程已保存');}
  if(f.dataset.planner==='calendar'){await api('/api/planner/calendar',{...Object.fromEntries(d),blocks:[0,1,2,3,4].map(i=>[readClock(f,'start'+i),readClock(f,'end'+i)])});modal.close();await plannerRefresh();toast('校历已更新');}
  if(f.dataset.planner==='merge'){
    const result=await api('/api/matter',{id:Number(f.dataset.source),merge_into:Number(f.dataset.target)});
    modal.close();
    await plannerRefresh();
    if(f.dataset.detail==='true')await matterDialog(result.id);
    toast('事项已合并');
  }
  if(f.dataset.planner==='import'){const file=d.get('file');if(file.size>20*1024*1024)throw Error('文件不能超过20 MB');err.textContent='正在识别并整理，通常需要几十秒…';const result=await api('/api/planner/import',{name:file.name,data:await fileBase64(file),kind:f.dataset.kind,allow_model:d.has('allow_model')});previewPlanner(result,f.dataset.kind);}
 }catch(e){if(err)err.textContent=e.message;else toast(e.message);}finally{b.disabled=false;}
});

function mergeItemLabel(matter) {
  const meta=[matter.group_name, matter.sent_at && fmt(matter.sent_at),
    matter.message_count && matter.message_count+' 条消息',
    matter.attachment_count && matter.attachment_count+' 个附件'].filter(Boolean).join(' · ');
  return `<strong>${esc(matterTitle(matter))}</strong><small>${esc(meta)}</small>`;
}

function confirmMatterMerge(source,target,fromDetail=false) {
  openModal('确认合并事项',`<form data-planner="merge" data-source="${source.id}" data-target="${target.id}" data-detail="${fromDetail}">
    <dl class="merge-pair"><dt>将此项并入</dt><dd>${mergeItemLabel(source)}</dd>
    <dt>保留此项</dt><dd>${mergeItemLabel(target)}</dd></dl>
    <p class="form-error" role="alert"></p>
    <div class="modal-actions"><button type="button" data-action="close-modal">取消</button>
    <button type="submit" class="primary">确认合并</button></div></form>`);
}

async function openMergePicker(anchor,inward) {
  // The normal unfiltered snapshot already contains every matter.
  const matters=state.matters.length===state.counts.matters?state.matters:(await api('/api/state')).matters;
  if(!modal.open || currentMatter!==anchor)return;
  const account=JSON.parse(anchor.scope)[0];
  const groups=new Set(anchor.groups||[anchor.group_name]);
  const relatives=new Set([anchor.split_parent?.id,...(anchor.split_children||[]).map(m=>m.id)]);
  const time=Date.parse(anchor.sent_at);
  const options=matters.filter(m=>m.id!==anchor.id && JSON.parse(m.scope)[0]===account).map(m=>{
    const stamp=Date.parse(m.sent_at);
    return {matter:m,relative:relatives.has(m.id),sameGroup:(m.groups||[m.group_name]).some(g=>groups.has(g)),
      distance:Number.isFinite(stamp-time)?Math.abs(stamp-time):Infinity,
      search:[matterTitle(m),m.group_name,'#'+m.id].join(' ').toLowerCase()};
  }).sort((a,b)=>b.relative-a.relative || b.sameGroup-a.sameGroup || a.distance-b.distance || b.matter.id-a.matter.id);

  openModal(inward?'将其他事项合入这里':'合并到其他事项',`<div class="merge-picker">
    <p class="help">${inward?'保留':'并入其他事项'} · ${esc(matterTitle(anchor))}</p>
    <label>${inward?'选择要并入的事项':'选择要保留的事项'}<input type="search" placeholder="搜索事项或群聊"></label>
    <div class="merge-options"></div><div class="merge-pages"></div></div>`);
  const picker=modal.querySelector('.merge-picker'),search=picker.querySelector('input');
  const pageSize=5;
  let matches=options,page=0;
  function showPage() {
    const start=page*pageSize;
    picker.querySelector('.merge-options').innerHTML=matches.slice(start,start+pageSize).map(({matter:m})=>
      `<button type="button" data-option="${m.id}">${mergeItemLabel(m)}</button>`).join('') ||
      `<p class="help">${options.length?'没有匹配的事项':'当前账号暂无其他事项可合并'}</p>`;
    picker.querySelector('.merge-pages').innerHTML=matches.length<=pageSize?'':`
      <button type="button" data-page="-1" ${page===0?'disabled':''}>上一页</button>
      <span>${page+1} / ${Math.ceil(matches.length/pageSize)}</span>
      <button type="button" data-page="1" ${start+pageSize>=matches.length?'disabled':''}>下一页</button>`;
  }
  search.addEventListener('input',()=>{
    const words=search.value.trim().toLowerCase().split(/\s+/);
    matches=options.filter(option=>words.every(word=>option.search.includes(word)));
    page=0;showPage();
  });
  picker.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button)return;
    if(button.dataset.page){page+=Number(button.dataset.page);showPage();return;}
    const selected=matches.find(o=>o.matter.id===Number(button.dataset.option))?.matter;
    if(selected)confirmMatterMerge(inward?selected:anchor,inward?anchor:selected,true);
  });
  showPage();search.focus();
}

// Pointer gestures stay inside the list even in desktop shells that intercept native file drops.
let matterDragSource=null, suppressMergeClick=false;
const mergeRowSelector='.matter-row[data-merge-row]';

function mergeRowAt(drag,x,y) {
  const row=document.elementFromPoint(x,y)?.closest(mergeRowSelector);
  return row && drag.targets.has(Number(row.dataset.mergeRow||row.dataset.id))?row:null;
}
function updateDropTarget(drag) {
  const target=mergeRowAt(drag,drag.x,drag.y);
  if(target===drag.target)return;
  drag.target?.classList.remove('merge-drop-target');
  target?.classList.add('merge-drop-target');
  drag.target=target;
}
function scrollDuringMerge(drag) {
  const edge=48;
  const delta=drag.y<edge?-12:drag.y>innerHeight-edge?12:0;
  if(delta){window.scrollBy(0,delta);updateDropTarget(drag);}
  drag.frame=requestAnimationFrame(()=>scrollDuringMerge(drag));
}
function endMatterDrag() {
  const drag=matterDragSource;
  if(!drag)return;
  matterDragSource=null;
  cancelAnimationFrame(drag.frame);
  drag.row.classList.remove('merge-drag-source');
  drag.target?.classList.remove('merge-drop-target');
  if(drag.row.hasPointerCapture(drag.pointer))drag.row.releasePointerCapture(drag.pointer);
}
document.addEventListener('pointerdown',event=>{
  if(!event.isPrimary || event.button!==0)return;
  endMatterDrag();suppressMergeClick=false;
  const row=event.target.closest(mergeRowSelector);
  if(!row || modal.open || (event.pointerType==='touch' && !event.target.closest('.state-icon')))return;
  row.setPointerCapture(event.pointerId);
  matterDragSource={row,pointer:event.pointerId,startX:event.clientX,startY:event.clientY};
});
document.addEventListener('pointermove',event=>{
  const drag=matterDragSource;
  if(!drag || drag.pointer!==event.pointerId)return;
  drag.x=event.clientX;drag.y=event.clientY;
  if(!drag.source){
    if(Math.hypot(drag.x-drag.startX,drag.y-drag.startY)<6)return;
    drag.source=state.matters.find(m=>m.id===Number(drag.row.dataset.mergeRow||drag.row.dataset.id));
    if(!drag.source){endMatterDrag();return;}
    const account=JSON.parse(drag.source.scope)[0];
    drag.targets=new Map(state.matters.filter(m=>m.id!==drag.source.id && JSON.parse(m.scope)[0]===account).map(m=>[m.id,m]));
    drag.row.classList.add('merge-drag-source');suppressMergeClick=true;
    scrollDuringMerge(drag);
  }
  updateDropTarget(drag);
});
document.addEventListener('pointerup',event=>{
  const drag=matterDragSource;
  if(!drag || drag.pointer!==event.pointerId)return;
  const row=drag.source && mergeRowAt(drag,event.clientX,event.clientY);
  endMatterDrag();
  if(row)confirmMatterMerge(drag.source,drag.targets.get(Number(row.dataset.mergeRow||row.dataset.id)));
});
document.addEventListener('click',event=>{
  if(suppressMergeClick && event.detail && event.target.closest(mergeRowSelector)){
    event.preventDefault();event.stopImmediatePropagation();
  }
},true);
document.addEventListener('pointercancel',event=>{if(event.pointerId===matterDragSource?.pointer)endMatterDrag();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')endMatterDrag();});
document.addEventListener('lostpointercapture',event=>{
  if(event.target===matterDragSource?.row && event.pointerId===matterDragSource.pointer)endMatterDrag();
});
document.defaultView?.addEventListener('blur',endMatterDrag);
document.addEventListener('visibilitychange',()=>{if(document.hidden)endMatterDrag();});
