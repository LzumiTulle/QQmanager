'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function icon(name) {
  return `<svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#icon-${name}"></use></svg>`;
}
const labels = {home:'日程表',overview:'消息概览',insights:'事项',files:'资料库',tasks:'事项',messages:'资料库',connections:'设置',settings:'设置'};
let nativeState = null;
let settingsTab='model', connectionTab='account', sortOrder='latest', topicFilter='全部';
let groupFilter=localStorage.getItem('qqm-group-filter')||'', groupOptionsSignature='', groupSelecting=false;
const canonicalView = name => labels[name]?name:'home';
function applyTheme(theme){document.documentElement.dataset.theme=theme;localStorage.setItem('qqm-theme',theme);document.querySelectorAll('[data-theme-choice]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.themeChoice===theme));});}
$('#search-shortcut').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K';
applyTheme(localStorage.getItem('qqm-theme')==='dark'?'dark':'light');
let overviewState = null;
let connectorState = null, connectorSignature = '';
let csrf = '', state = null, view = 'home', category = '全部', taskFilter = 'open', searchTimer, toastTimer, loadSequence = 0;
const modal = $('#modal');
const fmt = value => value ? new Date(typeof value === 'number' ? value*1000 : value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '时间未知';
const bytes = n => n >= 1048576 ? (n/1048576).toFixed(1)+' MB' : n >= 1024 ? Math.round(n/1024)+' KB' : n+' B';
const badge = kind => `<span class="badge ${['待办','变更'].includes(kind)?'amber':kind==='问答'?'blue':''}">${esc(kind)}</span>`;
const textEmpty = (title, text, small=false) => `<div class="empty ${small?'small':''}"><div class="empty-icon">✧</div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;

async function api(path, body) {
  const response = await fetch(path, {method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data = await response.json();
  if (response.status===403 && body!==undefined && data.error==='会话已失效，请刷新页面') {
    csrf=(await (await fetch('/api/session')).json()).csrf;
    const retry=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
    const result=await retry.json();if(!retry.ok)throw new Error(result.error||'请求失败');return result;
  }
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function toast(message) {$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),5000);}
function notice(message='') {$('#notice').hidden=!message;$('#notice').textContent=message;}
let reviewPoll, reviewRequest=0, lastReview=null;
function showReview(result) {
  const {phase,reviewed,total,processed,queued,failed}=result;
  const scanning=phase==='scanning', analyzing=phase==='analyzing', busy=scanning||analyzing;
  $('#review-status').hidden=phase==='idle';
  const button=$('#review-button');
  button.disabled=busy&&!result.waiting_only;
  button.textContent=button.disabled?'刷新中…':'刷新';
  if(phase==='idle')return;
  const waiting=(result.waiting_model||0)+(result.waiting_quota||0);
  const paused=analyzing && queued-processed===waiting && waiting>0;
  const label=scanning?'正在复查历史消息':analyzing?(paused?'等待继续识别':'正在重新识别'):
    phase==='finished'?(failed?'刷新结束，部分消息识别失败':'刷新完成'):
    phase==='interrupted'?'上次刷新已中断':'刷新失败';
  $('#review-phase').textContent=label;
  const count=scanning?`${reviewed} / ${total} 条消息`:analyzing?`${processed} / ${queued} 条已处理`:
    phase==='finished'?fmt(result.finished):'';
  $('#review-count').textContent=count;
  const bar=$('#review-progress');bar.hidden=!busy;
  bar.max=Math.max(1,scanning?total:queued||0);
  if(scanning&&!total)bar.removeAttribute('value');else bar.value=scanning?reviewed:processed||0;
  const detail=[];
  if(!scanning)detail.push(`已复查 ${reviewed} 条历史消息`);
  if(analyzing){
    if(result.running)detail.push(`${result.running} 条识别中`);
    if(result.waiting_model)detail.push(`${result.waiting_model} 条等待启用模型`);
    if(result.waiting_quota)detail.push(`${result.waiting_quota} 条等待模型额度恢复`);
    if(result.retrying)detail.push(`${result.retrying} 条等待重试`);
    const pending=queued-processed-result.running-waiting-result.retrying;
    if(pending>0)detail.push(`${pending} 条排队中`);
  }
  if(phase==='finished')detail.push(queued?`重新识别成功 ${processed-failed-(result.skipped||0)} 条`:'没有需要重新识别的消息');
  if(failed)detail.push(`${failed} 条失败，可再次刷新重试`);
  if(result.skipped)detail.push(`${result.skipped} 条已跳过`);
  if(['interrupted','error'].includes(phase))detail.push('点击刷新可重试');
  $('#review-detail').textContent=detail.join(' · ');
}
async function trackReview(start=false){
  clearTimeout(reviewPoll);
  const request=++reviewRequest;
  if(start)showReview({phase:'scanning',total:0,reviewed:0});
  try{
    const result=await api('/api/review',start?{}:undefined);
    if(request!==reviewRequest)return;
    showReview(result);
    const completed=result.phase==='finished' && lastReview?.run===result.run && lastReview.phase!=='finished';
    lastReview=result;
    if(completed)refresh();
    reviewPoll=setTimeout(()=>trackReview(),['scanning','analyzing'].includes(result.phase)?1000:5000);
  }catch(error){
    if(request!==reviewRequest)return;
    $('#review-button').disabled=false;
    $('#review-button').textContent='刷新';
    $('#review-status').hidden=false;
    $('#review-phase').textContent='暂时无法获取刷新状态';
    $('#review-count').textContent='';
    $('#review-progress').hidden=true;
    $('#review-detail').textContent='连接恢复后会自动更新。';
    reviewPoll=setTimeout(()=>trackReview(),3000);
  }
}
async function refresh(renderPage=true) {
  const sequence=++loadSequence;
  try {
    const searchEnabled=['insights','files','tasks','messages'].includes(view);
    const rawQuery=$('#search').value, rawGroup=groupFilter;
    const query=searchEnabled?rawQuery:'', group=searchEnabled?rawGroup:'';
    const isLibrary = sectionOf(view) === 'messages';
    const context = JSON.stringify([view, query, group]);
    if (context !== libraryContext) {
      libraryContext = context;
      libraryOpen = ''; libraryOffset = 0; libraryYear = '';
    }
    const library = isLibrary ? api('/api/library?'+new URLSearchParams({type:view,q:query,group,day:libraryOpen,offset:libraryOffset})) : null;
    const [data,calendar,overview,archive]=await Promise.all([api('/api/state?'+new URLSearchParams({q:query,group})),api('/api/planner?'+new URLSearchParams({...plannerDate?{date:plannerDate}:{},span:view==='overview'&&homeScope==='month'?'month':'week'})),view==='overview'?api('/api/overview?'+new URLSearchParams({...plannerDay||plannerDate?{date:plannerDay||plannerDate}:{},span:homeScope})):Promise.resolve(null),library]);
    if(sequence!==loadSequence||rawGroup!==groupFilter||rawQuery!==$('#search').value)return;
    libraryState=archive;state=data;plannerState=calendar;overviewState=overview;notice();
    const validGroups=isLibrary?[...state.groups,...(state.history_groups||[])]:state.groups;
    if(groupFilter&&!validGroups.includes(groupFilter)){groupFilter='';localStorage.removeItem('qqm-group-filter');groupOptionsSignature='';return refresh(renderPage);}
    $('#notification-count').textContent=(state.notifications.length+(state.backfill_count||0))||'';
    if((state.backfill_count||0)>lastBackfillCount && lastBackfillCount!==null)toast('有新补收的群消息，点右上角提醒查看原文');
    lastBackfillCount=state.backfill_count||0;
    for (const [viewName,count] of [['messages',state.counts.unread_messages],['insights',state.counts.unread_matters]]) {
      const dot=document.querySelector(`.sidebar button[data-view="${viewName}"] .unread-dot`);
      if(dot) dot.hidden=!count;
    }
    updateGroupOptions();
    if(renderPage && !matterDragSource)render();
    else if($('#model-status'))$('#model-status').innerHTML=modelStatus();
    deliverBrowserNotifications();
  } catch(error) {notice('无法连接本地服务：'+error.message+'。请确认启动窗口仍在运行。');}
}
let lastBackfillCount=null;
function updateGroupOptions(){
 const names=[...new Set(state?.groups||[])].sort((a,b)=>a.localeCompare(b,'zh-CN'));
 const historical=sectionOf(view)==='messages'?(state?.history_groups||[]).filter(n=>!names.includes(n)):[];
 const signature=JSON.stringify([names,historical,groupFilter]);
 if(signature===groupOptionsSignature)return;
 const historyOpen=$('#group-options details')?.open;
 const option=g=>`<button role="option" aria-selected="${g===groupFilter}" data-group-value="${esc(g)}">${g===groupFilter?'✓ ':''}${esc(g||'全部群聊')}</button>`;
 $('#group-options').innerHTML=['',...names].map(option).join('')+(historical.length?`<details${historyOpen||historical.includes(groupFilter)?' open':''}><summary>历史群聊</summary>${historical.map(option).join('')}</details>`:'');
 $('#clear-group').hidden=!groupFilter;
 $('#group-filter').textContent=(groupFilter||'全部群聊')+' ▾';groupOptionsSignature=signature;
}
function closeGroupMenu(){groupSelecting=false;$('#group-options').hidden=true;$('#group-filter').setAttribute('aria-expanded','false');updateGroupOptions();}
function deliverBrowserNotifications(){
  if(!('Notification' in window)||Notification.permission!=='granted'||localStorage.getItem('qqm-browser-notifications')!=='on')return;
  for(const item of state.notifications){
    const key='qqm-notified-'+item.id+'-'+item.created;
    if(localStorage.getItem(key))continue;
    try {new Notification('QQmanager · 待办提醒',{body:item.title,tag:key});localStorage.setItem(key,'1');} catch(_) {/* In-page reminder stays available. */}
  }
}
const sectionOf = viewName => ({overview:'home',tasks:'insights', files:'messages', connections:'settings'})[viewName] || viewName;

function routeTaskFilter(route) {
  return ({'tasks/confirm':'confirm','tasks/done':'done'})[route] || 'open';
}

function setView(route) {
  const filter = routeTaskFilter(route);
  const target = filter === 'open' ? canonicalView(route) : 'tasks';
  if (target === view && (target !== 'tasks' || taskFilter === filter)) return;
  if (sectionOf(target) !== sectionOf(view)) {
    $('#search').value = '';
  }
  topicFilter = '全部';
  view = target;
  taskFilter = filter;
  category = '全部';
  location.hash = filter === 'open' ? view : route;
  render();
  refresh();
  window.scrollTo(0, 0);
}

function sectionNavigation() {
  const routes = {
    insights: [['insights','全部事项'], ['tasks/confirm','待确认'], ['tasks','待完成'], ['tasks/done','已完成']],
    messages: [['messages','消息'], ['files','文件']],
    settings: [['connections','账号与收集'], ['identity','学籍信息'], ['model','消息分析'], ['notifications','提醒']]
  };
  const section = sectionOf(view);
  const items = routes[section] || [];
  const active = view === 'settings' ? settingsTab : view === 'tasks' && taskFilter !== 'open' ? 'tasks/'+taskFilter : view;
  const nav = $('#section-nav');
  nav.hidden = !items.length;
  nav.innerHTML = items.length ? `<div class="tabs page-tabs" role="group" aria-label="${labels[view]}视图">${items.map(([route,name]) => {
    const attr = section === 'settings' && route !== 'connections' ? 'data-settings-tab' : 'data-route';
    const glyph = {insights:'list','tasks/confirm':'review',tasks:'pending','tasks/done':'done',messages:'message',files:'file'}[route];
    return `<button ${attr}="${route}" class="${active === route ? 'active' : ''}" aria-pressed="${active === route}">${glyph ? icon(glyph) : ''}${name}</button>`;
  }).join('')}</div>` : '';
}

function render(){
  if(!state)return;
  for (const button of document.querySelectorAll('.sidebar nav [data-view]')) {
    const active = button.dataset.view === sectionOf(view);
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  }
  sectionNavigation();
  document.body.dataset.view=view;
  $('#page-title').textContent=labels[view];
  $('#searchbar').hidden=['home','overview','connections','settings'].includes(view);
  $('#search').placeholder=({home:'搜索事项或相关消息',insights:'搜索事项或相关消息',files:'搜索文件名或正文',tasks:taskFilter==='confirm'?'搜索待确认事项或相关消息':'搜索待办',messages:'搜索原始消息'})[view]||'搜索';
  $('#import-button').hidden=!['files','messages'].includes(view);
  $('#import-label').textContent=view==='files'?'导入文件':'导入记录';
  $('#new-task-button').hidden=sectionOf(view)!=='insights' && view!=='home';
  $('#content').innerHTML=({home:scheduleView,overview:homeView,insights:insightsView,files:filesView,tasks:tasksView,messages:messagesView,connections:connectionsView,settings:settingsView})[view]();
  const m=state.llm||{},failed=m.counts?.failed||0;
  $('#analysis-status').textContent=failed?`${failed} 条分析失败`:m.enabled?'AI 已开启':'本地识别';
  $('#analysis-status').title=failed?'打开设置查看失败详情':'打开模型设置';
}

function insightCard(i){
  return `<button class="info-row ${i.status==='read'?'is-read':''}" data-action="source" data-id="${i.id}" aria-label="查看：${esc(i.title)}"><span class="row-mark" aria-hidden="true">${i.status==='saved'?'☆':i.status==='new'?'•':''}</span><span class="row-content"><span class="row-title">${esc(i.title)}</span><span class="row-meta"><span>${esc(i.kind)}</span><span>${esc(i.group_name)}</span>${i.status==='saved'?'<span>已收藏</span>':''}${i.merged_count>1?`<span>合并 ${i.merged_count} 条</span>`:''}</span></span><time>${fmt(i.sent_at)}</time><span class="row-arrow" aria-hidden="true">›</span></button>`;
}

function taskRow(t){
 const overdue=t.remind_at&&t.remind_at*1000<Date.now()&&t.status==='open';
 const fixed=plannerState.workflow.fixed_exams.find(e=>e.task_id===t.id);
 const plan=fixed?.outside_class?'原班级考试 · 提醒已暂停':fixed?'考试 '+[fixed.date,fixed.start_time].filter(Boolean).join(' '):planDescription(t);
 const mergeable=view==='insights' && t.sourceMatter;
 const meta=[view==='insights'?(t.status==='done'?'已完成':'待完成'):'',esc(plan)+taskExpiryTags(t,fixed),t.remind_at?'提醒 '+fmt(t.remind_at):'',overdue?'提醒时间已过':'',esc(t.group_name)].filter(Boolean).join(' · ');
 return `<div class="task-row ${t.status==='done'?'done':''}"><button class="task-body${mergeable?' matter-row':''}" ${mergeable?`data-merge-row="${t.sourceMatter.id}" title="拖到另一条事项上合并原文"`:''} data-action="task-edit" data-id="${t.id}"><span class="row-title">${esc(t.title)}</span><span class="row-meta">${meta}</span></button><div class="task-actions">${!fixed&&t.status==='open'?`<button data-schedule-item="t${t.id}">${taskTimeAction(t)}</button>`:''}${t.status==='open'&&!fixed?.outside_class?`<button data-action="snooze" data-id="${t.id}">稍后提醒</button>`:''}${t.sourceMatter?`<button data-plan="matter" data-id="${t.sourceMatter.id}">原事项</button>`:t.insight_id?`<button data-action="task-source" data-id="${t.id}">原文</button>`:''}</div></div>`;
}

function listSection(title, count, rows, listClass='info-list') {
  return `<section class="topic-section"><div class="topic-heading"><h2>${esc(title)}<span>${count}</span></h2></div><div class="${listClass}">${rows}</div></section>`;
}

function topicList(items, actions, empty) {
  const {regular,overdue,expired,nextChange}=matterListSections(items,view==='tasks'||category==='全部',Date.now(),view==='insights'&&['已读','已收藏','其他通知'].includes(category));
  const counts=new Map(QQFilters.group([...regular,...overdue]).map(g=>[g.name,g.items.length]));
  const archiveItems=(view==='tasks'&&taskFilter==='confirm')||(view==='insights'&&category==='全部')?[]:expired;
  const names=['全部',...QQFilters.topics.filter(name=>counts.has(name)||name===topicFilter||archiveItems.some(m=>QQFilters.topic(m)===name))];
  const inTopic=i=>topicFilter==='全部'||QQFilters.topic(i)===topicFilter;
  const archive=expiredNoticePanel('expired-'+view,archiveItems.filter(inTopic),true);
  const sections=archive?[archive]:[];
  const urgent=overdue.filter(inTopic);
  if(urgent.length)sections.push(listSection('已逾期',urgent.length,urgent.map(t=>taskRow(t)).join(''),'task-list'));
  for(const g of QQFilters.group(regular.filter(inTopic))){
    const row=i=>i.personal?taskRow(i):matterCard(i,'overview',true);
    sections.push(listSection(g.name,g.items.length,g.items.map(row).join('')));
  }
  const options = names.map(name => {
    const count = name === '全部' ? regular.length+overdue.length : counts.get(name) || 0;
    return `<option value="${esc(name)}" ${name===topicFilter?'selected':''}>${name==='全部'?'全部分类':esc(name)}（${count}）</option>`;
  });
  const topics=names.length>2 || topicFilter!=='全部'
    ? `<select id="topic-filter" aria-label="事项分类">${options.join('')}</select>`:'';
  return `<div class="toolbar topic-toolbar" ${topics||actions?'':'hidden'}${Number.isFinite(nextChange)?` data-list-until="${nextChange}"`:''}>${topics}${actions}</div>`
    + (sections.join('') || empty);
}

function taskSources() {
  const sources = new Map();
  for (const matter of Object.values(plannerState?.workflow?.matters || {})) {
    for (const task of matter.tasks) sources.set(task.id, matter);
  }
  for (const exam of plannerState.workflow.fixed_exams) {
    const source=workflowFor(exam.matter_id);
    if (source && !sources.has(exam.task_id)) sources.set(exam.task_id,source);
  }
  return sources;
}

function personalItems() {
  const sources = taskSources();
  const query = $('#search').value.trim().toLowerCase();
  const matchingSources = new Set(state.matters.map(m => m.id));
  return state.tasks.flatMap(t => {
    const source = sources.get(t.id);
    const groups = t.groups?.length ? t.groups : source?.groups || [t.group_name];
    if (groupFilter && !groups.includes(groupFilter)) return [];
    if (query && !t.title.toLowerCase().includes(query) && !matchingSources.has(source?.id)) return [];
    return [{...t, personal:true, sourceMatter:source, deadline:effectiveDeadline(t,source),
      kind:'待办', topic:source?.topic, group_name:t.group_name || source?.group_name, sent_at:t.created}];
  });
}

function needsConfirmation(m) {
  return ['confirm','changed'].includes(workflowFor(m.id)?.state);
}

function insightsView() {
  const matters = state.matters || [];
  let items;
  if (category === '全部') {
    items = [...matters.filter(needsConfirmation), ...personalItems()];
  } else {
    items = matters.filter(m => {
      if (category === '已忽略') return m.status === 'dismissed';
      if (m.status === 'dismissed') return false;
      if (category === '其他通知') return !workflowFor(m.id)?.tasks.length && (!needsConfirmation(m) || noticeTiming(workflowFor(m.id))?.expired);
      if (category === '已收藏') return m.saved || m.status === 'saved';
      const unread = m.unread ?? (m.status === 'new');
      return category === '未读' ? unread : !unread;
    });
  }
  const stamp = i => typeof i.sent_at === 'number' ? i.sent_at * 1000 : Date.parse(i.sent_at) || 0;
  items.sort((a,b) => (sortOrder === 'priority' ? (b.score || 0) - (a.score || 0) : 0)
    || stamp(b) - stamp(a) || b.id - a.id);
  const actions = `<div class="filter-actions"><select id="read-filter" aria-label="事项范围">${['全部','其他通知','未读','已读','已收藏','已忽略'].map(c=>`<option value="${c}" ${category===c?'selected':''}>${c==='全部'?'按处理状态':c}</option>`).join('')}</select><select id="sort-order" aria-label="信息排序"><option value="latest" ${sortOrder==='latest'?'selected':''}>最新优先</option><option value="priority" ${sortOrder==='priority'?'selected':''}>重要优先</option></select></div>`;
  return topicList(items, actions,
    textEmpty('没有符合条件的事项','可以切换分类、调整群聊筛选或搜索词。'));
}

function tasksView() {
  if(taskFilter==='confirm'){
    const items=state.matters.filter(needsConfirmation);
    return topicList(items,'',textEmpty('没有符合条件的待确认事项','新收到的事项需要你确认时，会出现在这里。'));
  }
  const tasks = personalItems().filter(t => t.status === taskFilter);
  const history = state.task_merges?.length
    ? `<button class="history-link" data-plan="merged-history">合并记录 ${state.task_merges.length}</button>` : '';
  const filtered = topicFilter !== '全部' || groupFilter || $('#search').value.trim();
  return topicList(tasks, history, filtered
    ? textEmpty('没有符合条件的待办','可以切换分类、调整群聊筛选或搜索词。')
    : taskFilter === 'open' ? textEmpty('暂无待完成事项','可以从事项详情添加，也可以新建待办。')
    : textEmpty('暂无已完成事项','在待办详情中标记完成后，可在这里查看。'));
}

const libraryDay = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'long', day:'numeric', weekday:'short'});
const libraryTime = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
let libraryState = null, libraryYear = '', libraryOpen = '', libraryOffset = 0, libraryContext = '';

function libraryCalendar(year, days, unit) {
  const dated = days.filter(d => d.day !== 'unknown');
  const counts = new Map(dated.map(d => [d.day, d.count]));
  const peak = dated.reduce((best, d) => Math.max(best, d.count), 0);
  function half(startMonth) {
    const start = Date.UTC(year, startMonth, 1);
    const end = Date.UTC(year, startMonth + 6, 1);
    const ms = 86400000;
    const monday = start - (new Date(start).getUTCDay() + 6) % 7 * ms;
    const weeks = [];
    for (let week = monday; week < end; week += 7 * ms) {
      let month = '';
      const cells = [];
      for (let n = 0; n < 7; n++) {
        const stamp = week + n * ms;
        if (stamp < start || stamp >= end) { cells.push('<i></i>'); continue; }
        const date = new Date(stamp), key = date.toISOString().slice(0,10);
        if (date.getUTCDate() === 1) month = date.getUTCMonth()+1+'月';
        const count = counts.get(key) || 0;
        const label = key+' · '+count+unit;
        const level = !count ? 0 : count === peak ? 4 : 1 + Math.floor(3 * (Math.log(count) / Math.log(peak)));
        cells.push(count
          ? `<button type="button" class="heat-cell level-${level}" data-heat-day="${key}" aria-label="${label}" aria-pressed="${libraryOpen===key}" title="${label}"></button>`
          : `<i class="heat-cell" aria-hidden="true" title="${label}"></i>`);
      }
      weeks.push(`<div class="heat-week"><small aria-hidden="true">${month}</small>${cells.join('')}</div>`);
    }
    return `<div class="heat-half"><div class="heat-week heat-weekdays" aria-hidden="true"><span>一</span><i></i><span>三</span><i></i><span>五</span><i></i><span>日</span></div>${weeks.join('')}</div>`;
  }
  const peaks = dated.filter(d => d.count === peak);
  const peakKey = peaks[0]?.day;
  const peakLabel = peakKey ? libraryDay.format(new Date(peakKey+'T12:00:00Z')) : '';
  const peakButton = peakKey ? `<button class="heat-peak" data-heat-day="${peakKey}"><span>全年最多</span> <strong>${peakLabel} · ${peak} ${unit}</strong>${peaks.length>1?' 等 '+peaks.length+' 天并列':''}</button>` : '';
  return `<div class="heat-chart">${half(0)}${half(6)}</div><div class="heat-footer">${peakButton}<span class="heat-legend" aria-label="每日数量，从少到多">少 ${[0,1,2,3,4].map(i=>`<i class="heat-cell level-${i}"></i>`).join('')} 多</span></div>`;
}

function libraryList(renderRow, empty) {
  const data = libraryState;
  if (!data || data.type !== view) return '<p class="help" role="status">正在读取资料库…</p>';
  if (!data.days.length) return groupFilter || $('#search').value.trim()
    ? textEmpty('没有匹配结果','可以更改搜索词或群聊筛选。') : empty;
  const dates = data.days.filter(d => d.day !== 'unknown');
  const years = [...new Set(dates.map(d => d.day.slice(0,4)))];
  const year = years.includes(libraryYear) ? libraryYear : years[0];
  const file = view === 'files', unit = file ? '个文件' : '条消息';
  const shown = data.days.filter(d => d.day === 'unknown' || d.day.slice(0,4) === year);
  const options = years.map(y => `<option ${y===year?'selected':''}>${y}</option>`).join('');
  const yearPicker = years.length>1 ? `<select id="library-year" aria-label="查看年份">${options}</select>` : `<span>${year||''}</span>`;
  const chart = year ? `<section class="heat-panel"><header><h2>${file?'文件收录':'消息活跃度'}</h2>${yearPicker}</header>${libraryCalendar(Number(year), shown, unit)}</section>` : '';
  return chart + shown.map(d => {
    const key = d.day, open = libraryOpen === key;
    const label = key==='unknown' ? '日期未知' : libraryDay.format(new Date(key+'T12:00:00Z'));
    const previews = [...new Set([d.preview, d.secondary].filter(Boolean).map(s=>(file?s:messageText(s)).replace(/\s+/g,' ').trim()))];
    const hint = previews.join(' · ');
    let body = '';
    if (open && data.day === key) {
      const rows = data.rows.map(item => {
        const date = file ? new Date(item.created * 1000) : new Date(item.sent_at);
        return renderRow(item, key === 'unknown' ? '' : libraryTime.format(date));
      });
      const next = data.offset + data.rows.length;
      const pager = d.count > data.page_size ? `<div class="library-pager"><button data-day-offset="${data.offset-data.page_size}" ${data.offset?'':'disabled'}>上一页</button><span>${data.offset+1}–${next} / ${d.count}</span><button data-day-offset="${next}" ${next<d.count?'':'disabled'}>下一页</button></div>` : '';
      body = rows.join('') + pager;
    } else if (open) body = '<p class="help" role="status">正在读取…</p>';
    return `<section class="library-group"><h3><button id="library-day-${key}" class="day-summary" data-library-day="${key}" aria-expanded="${open}" aria-controls="day-rows-${key}">${icon('arrow')}<span class="day-name">${label}${d.unread?unreadDot():''}</span><span class="day-count">${d.count} ${unit}</span><span class="day-hint" title="${esc(hint)}">${esc(hint)}</span></button></h3><div id="day-rows-${key}" class="library-day-rows" ${open?'':'hidden'}>${body}</div></section>`;
  }).join('');
}

async function openLibraryDay(day, offset, jump) {
  libraryOpen = day;
  libraryOffset = offset;
  const context = libraryContext;
  render();
  const heading = () => document.getElementById('library-day-'+day);
  if (jump) heading()?.scrollIntoView({block:'start'});
  if (day) await refresh();
  if (context === libraryContext && day === libraryOpen) heading()?.focus({preventScroll:true});
}

function libraryRow(action, id, title, meta, time, excerpt='', unread=false) {
  return `<button class="info-row library-row" data-action="${action}" data-id="${id}"><span class="row-content"><span class="row-title">${unread?unreadDot():''}${esc(title)}</span><span class="row-meta">${esc(meta.filter(Boolean).join(' · '))}</span>${excerpt ? `<span class="file-match">${esc(excerpt)}</span>` : ''}</span>${time ? `<time>${esc(time)}</time>` : ''}${icon('arrow')}</button>`;
}

function filesView() {
  return libraryList((f,time) =>
    libraryRow('file-detail', f.id, f.name, [f.group_name, bytes(f.size), f.parse_status], time,
      $('#search').value.trim() ? f.excerpt?.slice(0,110) : ''),
    textEmpty('暂无文件','可以导入文件，或在设置的「账号与收集」中开启文件夹监听。'));
}

function messageText(value) {
 const text=String(value||'').replace(/\[(?:face|mface|bface|sface|sticker)[：:]\s*未解析\]/gi,'[表情]');
 const cleaned=text.replace(/\[其他消息类型[：:]\s*未解析\]/g,'');
 return cleaned.trim()?cleaned:(text?'[非文字消息]':'');
}
function messagePreview(m){
 const images=(m.attachments||[]).filter(a=>a.kind==='image');
 if(!images.length)return messageText(m.content);
 const text=messageText(m.content.replace(/\[图片：未解析\]/g,'')).trim();
 if(text)return text;
 const first=images.find(a=>a.text?.trim());
 return first?'图片 · '+first.text.trim().split('\n')[0]:images.some(a=>a.file_id)?`图片 · ${images.length} 张（可查看原图）`:'图片 · 待获取';
}
function messagesView() {
  const linked=new Set(Object.values(plannerState?.workflow?.matters||{}).flatMap(m=>m.source_message_ids||[]));
  return libraryList((m,time) =>
    libraryRow('message-detail', m.id, messagePreview(m),
      [m.group_name, m.sender, linked.has(m.id) ? '已关联事项' : m.analysis_state === 'filtered' ? '已过滤' : ''], time, '', m.unread),
    textEmpty('暂无消息','所选群的新消息会自动保存在这里，也可以导入聊天记录。'));
}

function connectionsView(){const s=state.settings;
 const tabs='<button class="settings-back" data-connection-tab="account">‹ 返回账号与收集</button>';
 if(connectionTab==='qq')return tabs+nativePanel();
 if(connectionTab==='account')return accountPanel()+`<details class="panel settings-extra"><summary>其他数据来源</summary><div class="inline-actions"><button data-connection-tab="folder">文件夹监听</button><button data-connection-tab="qq">QQ 客户端插件</button><button data-connection-tab="other">高级接入</button></div></details>`;
 if(connectionTab==='folder')return tabs+`<section class="panel"><div class="section-title"><h2>文件夹自动监听</h2></div><p>选定本机资料目录。每 10 秒扫描一次，自动导入新文件及内容变化；相同内容不会重复收纳。仅扫描当前目录，不包含子文件夹。</p><form id="watch-form" class="form-grid"><label>文件夹完整路径<input name="path" placeholder="例如 /Users/你的用户名/Downloads/群资料" required></label><label>内容类型<select name="mode"><option value="file">资料文件：索引文件名与正文</option><option value="chat">聊天记录：提取消息与重要信息</option></select></label><label>归属群聊<input name="group_name" placeholder="例如 工作交流群" required></label><button class="primary" type="submit">开始监听</button></form><div class="help">支持 TXT、MD、JSON、CSV、LOG、PDF、DOCX；单文件 20 MB，目录最多 1,000 个支持的文件。不会读取 QQ 的加密聊天数据库。</div>${state.watches.map(w=>`<div class="watch-card"><strong>${esc(w.path)}</strong><p>${esc(w.group_name)} · ${w.mode==='chat'?'聊天记录':'资料文件'} · ${w.enabled?'监听中':'已暂停'} · 最近扫描 ${fmt(w.last_scan)}</p>${w.error?`<p class="warning">${esc(w.error)}</p>`:''}<button data-action="watch-toggle" data-id="${w.id}">${w.enabled?'暂停监听':'继续监听'}</button></div>`).join('')}</section>`;
 return tabs+`<p class="help">供已有外部适配端的用户配置；使用客户端插件时无需设置。</p><details class="panel"><summary>NapCat 连接</summary>${napcatPanel()}</details><details class="panel"><summary>OneBot HTTP 上报</summary><section class="panel"><div class="section-title"><h2>QQ 消息自动接入</h2><span class="badge amber">需外部适配端</span></div><div class="status-line"><span class="dot"></span>${s.bridge_enabled?'接收端已启用':'接收端未启用'} · ${s.last_event?'最近收到消息 '+fmt(s.last_event):'尚未收到群消息'}</div><p>支持 OneBot v11 HTTP 上报，例如已有的 NapCat 适配端。配置后只接收指定群的新消息，无需逐次导入。</p><form id="bridge-form" class="form-grid"><label>接入群号（英文逗号分隔）<input name="groups" value="${esc(s.bridge_groups.join(','))}" placeholder="123456789,987654321"></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${s.bridge_enabled?'checked':''}>启用自动接入</label><button class="primary" type="submit">保存接入配置</button></form><div class="inline-actions"><button data-action="bridge-details">显示接收地址与凭据</button></div><div id="bridge-details"></div></section></details>`;
}

let nativeDraft=null, nativeGroupSearch='';
function ensureNativeDraft(){
  const account=connectorState?.selected_account||'';
  if(!nativeDraft||nativeDraft.account!==account||!nativeDraft.dirty){
    const rows=nativeState?.groups||[];
    nativeDraft={account,selected:new Set(state.settings.bridge_groups),startDates:new Map(),names:new Map(rows.map(g=>[g.group_id,g.group_name])),dirty:false};
    state.settings.bridge_groups.forEach(g=>{if(!nativeDraft.names.has(g))nativeDraft.names.set(g,'群 '+g);});
  }
  return nativeDraft;
}
function nativeGroupRows(){
  const d=ensureNativeDraft(), search=nativeGroupSearch.toLowerCase().trim();
  const empty=new Map((nativeState?.empty_history||[]).map(g=>[g.group_id,g]));
  const today=datetimeInput(Date.now()).slice(0,10);
  return [...d.names].filter(([id,name])=>(id+' '+name).toLowerCase().includes(search)).map(([id,name])=>{
    const selected=d.selected.has(id), scope=empty.get(id), draft=d.startDates.get(id);
    const start=scope?datetimeInput(scope.since*1000).slice(0,10):'';
    const date=scope||!state.settings.bridge_groups.includes(id)?`<label class="native-start-date">补收起始日<input type="date" data-native-date="${esc(id)}" data-grant="${esc(scope?.grant_id||'')}" data-original="${start}" aria-label="${esc(name)}补收起始日" max="${start||today}" value="${esc(draft?.date??start)}"></label>`:'';
    return `<div class="native-group-row" data-selected="${selected}"><label><input type="checkbox" data-native-group="${esc(id)}" ${selected?'checked':''}><span>${esc(name)}<small>${esc(id)}</small></span></label>${date}<button type="button" data-remove-group="${esc(id)}" aria-label="移除 ${esc(name)}">移除</button><span class="group-unselected">未收集</span></div>`;
  }).join('')||'<p class="help">没有匹配的群聊。可清空搜索或刷新群列表。</p>';
}
function nativeCount(){
  $('#native-group-count').textContent=`已选 ${nativeDraft.selected.size} 个群${nativeDraft.dirty?' · 未保存':''}`;
}
function renderNativeGroups(){
  $('#native-group-list').innerHTML=nativeGroupRows();
  nativeCount();
}
function toggleNativeGroup(input,selected){
  const d=ensureNativeDraft(), id=input.dataset.nativeGroup;
  if(selected)d.selected.add(id);else d.selected.delete(id);
  d.dirty=true;
  input.checked=selected;
  input.closest('.native-group-row').dataset.selected=String(selected);
  nativeCount();
}
function sourceIssue() {
  const issues = [];
  if (connectorState && !connectorState.selected_account) {
    issues.push(['account', '绑定 QQ 账号']);
  } else if (connectorState?.enabled && !connectorState.selected.length && !nativeState?.connected) {
    issues.push(['account', '选择要收集消息的群']);
  }
  if (nativeState?.enabled && state.settings.bridge_groups.length) {
    if (!nativeState.connected) issues.push(['qq', 'QQ 客户端插件未连接']);
    else if (Object.keys(nativeState.signals || {}).some(k => k.startsWith('unwritable:')) &&
             !nativeState.signals?.['callback_hooked:onRecvMsg']) issues.push(['qq', 'QQ 消息监听异常']);
  }
  if (connectorState?.enabled && connectorState.status !== 'online') issues.push(connectorState.managed ? ['account', 'QQ 账号正在重新连接'] : ['other', '备用接入尚未连接']);
  if (state.watches.some(w => w.enabled && w.error)) issues.push(['folder', '文件夹扫描失败']);
  return issues.map(([tab,message]) => `<button class="pending-banner" ${tab==='account'&&!connectorState?.selected_account?'data-action="qq-scan"':'data-source-issue="'+tab+'"'}>${message}<span>${tab==='account'?'继续 →':'查看数据来源 →'}</span></button>`).join('');
}

function nativePanel(){
  const n=nativeState||{}, signals=n.signals||{}, hooked=!!signals['callback_hooked:onRecvMsg'];
  const d=ensureNativeDraft();
  const incompatible=!hooked&&Object.keys(signals).some(key=>key.startsWith('unwritable:'));
  const phase=!n.enabled?'客户端插件已暂停':!n.connected?'等待 QQ 客户端插件连接':incompatible?'当前 QQ 接口不兼容，消息尚未开始收集':!hooked?'插件已连接，正在验证消息监听':!n.account_matches?'监听已就绪，等待新消息确认绑定账号':'客户端消息监听已连接';
  const historyPhase=!n.enabled?'已暂停':!n.connected?'等 QQ 登录后补收':!n.history_state?'需要更新 QQ 客户端插件至 0.1.6 或更高版本':({waiting:'等待绑定账号登录',syncing:'正在核对群消息',checked:'已核对 QQ 可提供的记录',retry:'部分记录暂未补齐，将自动重试'})[n.history_state];
  return `<section id="native-panel" class="panel"><div class="section-title"><h2>选择收集的群</h2><span id="native-group-count" class="badge">已选 ${d.selected.size} 个群${d.dirty?' · 未保存':''}</span></div><div class="status-line"><span class="dot"></span>${phase}</div><p class="connection-note">使用当前 QQ 客户端的轻量插件，无需 NapCat 或再次扫码登录。</p><p><strong>离线消息补收</strong> · ${esc(historyPhase)}${n.history?.saved?' · 已补收 '+n.history.saved+' 条':''}</p><p>勾选群后可选补收起始日。保存时，已选群会排到列表顶部。</p><input id="native-group-search" type="search" aria-label="搜索群聊" placeholder="搜索群名或群号" value="${esc(nativeGroupSearch)}"><div id="native-group-list" class="native-group-list">${nativeGroupRows()}</div><div class="inline-actions"><button type="button" data-action="native-refresh-groups">刷新 QQ 群列表</button><button type="button" data-action="native-clear-groups">取消全选</button><button type="button" class="primary" data-action="native-save-groups">保存群聊选择</button></div><p class="help">${n.groups_updated?`QQ 群列表已同步 ${n.groups_count} 个 · ${fmt(n.groups_updated)}。未勾选的群不收集消息。`:['0.1.1','0.1.2','0.1.3'].includes(n.version)?'完整列表需要更新 QQ 插件。请从应用菜单打开插件安装助手，更新后重启 QQ。':'等待 QQ 同步群列表。请保持 QQ 登录，也可点击刷新。'}</p><details ${d.account?'':'open'}><summary>账号与备用添加方式</summary><button type="button" data-action="native-add-group">按群号添加</button><p>使用你正常登录的 QQ，无需重复登录。QQ 重新登录后会按群补收缺少的消息；可补收的范围取决于 QQ 当前可读取的群记录。</p><form id="native-groups-form" class="form-grid"><label>日常使用的 QQ 账号<input name="account" inputmode="numeric" pattern="[0-9]{5,20}" value="${esc(d.account)}" required></label><button type="submit">保存绑定账号</button></form><p>本次保存 ${n.saved||0} 条 · 最近收到 ${fmt(n.last_event)} · 最近心跳 ${fmt(n.last_seen)}</p><small>客户端排队 ${n.queued||0} 条${n.dropped?' · 队列满已跳过 '+n.dropped+' 条':''}</small>${n.error?`<p class="warning">${esc(n.error)}</p>`:''}</details><div class="inline-actions"><button data-action="native-toggle" ${state.settings.bridge_groups.length?'':'disabled'}>${n.enabled?'暂停插件收集':'启用已配对插件'}</button></div></section>`;
}
function openQQScan(managed = true) {
  openModal(managed ? '绑定 QQ 账号' : '扫码登录 QQ', `<section id="qq-scan" class="qq-scan">
    <p id="scan-state" role="status">正在准备登录二维码…</p>
    <img hidden alt="使用手机 QQ 扫描此登录二维码">
    <form hidden class="form-grid">
      <label>NapCat WebUI Token<input name="token" type="password" autocomplete="off" required maxlength="1024"></label>
      <label hidden>身份验证器验证码<input name="otp" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6"></label>
      <button type="submit" class="primary">验证并继续</button>
    </form>
    <button hidden type="button" id="scan-next"></button>
  </section>`);
  const revision = connectorState?.login_revision || '';
  const panel = $('#qq-scan'), status = $('#scan-state'), image = panel.querySelector('img');
  const form = panel.querySelector('form'), next = $('#scan-next');
  const messages = {
    preparing: '正在准备 QQ 登录组件，首次使用可能需要几分钟…',
    waiting_account: '手机已确认，正在绑定 QQ 账号…',
    unavailable: '未连接到 NapCat。启动本机 NapCat 后，点击重新连接。',
    authorize: '请输入本机 NapCat 的 WebUI Token 以获取登录码。',
    two_factor: '此 NapCat 已启用两步验证，请输入身份验证器的六位验证码。',
    waiting: '正在等待 NapCat 生成二维码…',
    ready: '打开手机 QQ 的扫一扫，扫码后在手机上确认登录。',
    scanned: '已收到扫码确认，正在等待 QQ 登录完成…',
    reconnecting: 'QQ 正在重新连接…',
    online: 'QQ 已登录。接下来可以选择要收集消息的群。',
    refresh_needed: '当前二维码不可用，请获取新的二维码。'
  };
  let timer, busy = false;
  const active = () => modal.open && panel.isConnected;
  modal.addEventListener('close', () => {
    clearTimeout(timer);
    form.reset();
    image.removeAttribute('src');
  }, {once:true});

  async function update(body = {}) {
    if (!active() || busy) return;
    clearTimeout(timer);
    busy = true;
    next.disabled = true;
    form.querySelector('button').disabled = true;
    try {
      const result = await api(managed ? '/api/qq/bind' : '/api/connector/qq-login', managed ? {...body,revision} : body);
      if (!active()) return;
      const state = result.state;
      if (managed && state === 'bound') {
        modal.close();
        await refreshConnector(true);
        if(result.select_groups){
          connectionTab=nativeState?.connected&&nativeState.account_matches?'qq':'account';
          setView('connections');
        }else setView('home');
        render();
        toast(result.select_groups?'QQ 已绑定，选择要收集的群即可开始':'QQ 账号已绑定');
        return;
      }
      status.textContent = state === 'confirm_account' ? `确认使用 QQ ${result.account} 登录此资料库？` : result.message || messages[state] || '请重新连接 NapCat。';
      image.hidden = state !== 'ready';
      if (image.hidden) image.removeAttribute('src');
      else if (image.getAttribute('src') !== result.image) image.src = result.image;
      form.hidden = !['authorize','two_factor'].includes(state);
      if (form.hidden) form.reset();
      const otp = form.elements.otp;
      form.elements.token.parentElement.hidden = managed;
      form.elements.token.required = !managed;
      otp.parentElement.hidden = !(state === 'two_factor' || (state === 'authorize' && otp.required));
      otp.required = !otp.parentElement.hidden;
      if (state === 'two_factor') otp.focus();
      const labels = {confirm_account:'确认登录',expired:'关闭',failed:'重试', ready:'刷新二维码',waiting:'刷新二维码',refresh_needed:'刷新二维码',online:'返回选群',unavailable:'重新连接'};
      next.hidden = !labels[state];
      next.textContent = labels[state] || '';
      next.onclick = async () => {
        if (state === 'confirm_account') return update({confirm_account:result.account});
        if (state === 'expired') {modal.close();return;}
        if (state === 'online') {
          modal.close();
          if (connectorState?.enabled) await api('/api/connector/refresh', {}).catch(e => toast(e.message));
          await refreshConnector(true);
        } else update(state === 'failed' ? {retry:true} : state === 'unavailable' ? {} : {refresh:true});
      };
      if (['preparing','ready','waiting','waiting_account','scanned','reconnecting'].includes(state)) timer = setTimeout(update, 2500);
    } catch (error) {
      if (!active()) return;
      image.hidden = true;
      image.removeAttribute('src');
      status.textContent = error.message;
      next.hidden = false;
      next.textContent = '重试';
      next.onclick = () => update(managed ? {retry:true} : {});
    } finally {
      busy = false;
      next.disabled = false;
      form.querySelector('button').disabled = false;
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    event.stopPropagation();
    update({token:form.elements.token.value, otp:form.elements.otp.value});
  });
  update();
}

function accountGroups(c, selected) {
  const saved=new Set(selected);
  const rows=[...c.groups].sort((a,b)=>Number(saved.has(b.group_id))-Number(saved.has(a.group_id))).map(g=>{
    const id=esc(g.group_id), checked=selected.includes(g.group_id);
    const date=c.managed&&!checked?`<label hidden data-start-for="${id}">首次补收起始日（可选）<input type="date" aria-label="${esc(g.group_name)}首次补收起始日" name="start-${id}"></label>`:'';
    return `<div><label class="checkbox-label"><input type="checkbox" name="group" value="${id}" ${checked?'checked':''}><span>${esc(g.group_name)}<small>${id} · ${esc(g.member_count)} 人</small></span></label>${date}</div>`;
  });
  return `<form id="groups-form" class="form-grid"><div class="group-picker">${rows.join('')||'<p class="help">等待 QQ 返回群列表。</p>'}</div><button class="primary" type="submit" ${c.status==='online'?'':'disabled'}>保存所选群并自动收集</button><small>${c.managed?'已有记录的群从上次位置补收。首次收集可指定日期，留空从启用时开始。补收结果可在提醒中查看。':'保存后自动收集所选群的新消息。'}</small></form>`;

}
function accountPanel() {
  const c = connectorState;
  if (!c) return '<section class="panel" id="account-panel">正在读取 QQ 账号…</section>';
  const account = c.selected_account;
  const picker = c.enabled && c.account?.user_id === account && account;
  return `<section class="panel" id="account-panel"><div class="section-title"><h2>QQ 账号</h2>${account ? '<button data-action="qq-logout">退出登录</button>' : ''}</div>
    ${account ? `<p>已绑定 · ${esc(account)}</p>` : '<p>用手机 QQ 扫一扫，绑定后即可使用。</p>'}
    ${!account || (!picker && !nativeState?.connected) ? `<button class="primary" data-action="qq-scan">${account?'重新扫码登录':'扫码绑定 QQ'}</button>` : ''}
    ${account && c.managed && !picker && !nativeState?.connected ? '<p class="help">正在尝试重新连接。如果 QQ 要求重新登录，扫码后会继续使用已选群和上次补收位置。</p>' : ''}
    ${picker && c.managed && c.selected.length ? `<p id="managed-history-status" class="help">${esc(c.history_status||'等待核对所选群')}</p>` : ''}
    ${picker ? '<h3>选择要收集的群</h3>'+accountGroups(c,c.selected) : ''}
    ${account && (nativeState?.enabled||nativeState?.connected&&nativeState.account_matches) ? `<button data-connection-tab="qq">${nativeState.enabled?'管理已接入的群':'选择要收集的群'}</button>` : ''}
    </section>`;
}
function napcatPanel(){
  const c=connectorState;
  if(!c)return '<div id="napcat-panel" class="panel"><p>正在检查本机 NapCat 连接…</p></div>';
  const current=c.account&&c.selected_account===c.account.user_id;
  const selected=current?c.selected:[];
  return `<section id="napcat-panel" class="panel"><div class="section-title"><h2>NapCat 实时连接</h2><span class="badge ${c.status==='online'?'':'amber'}">${esc(({disabled:'未启用',connecting:'连接中',waiting_login:'等待 QQ 登录',online:'已连接 QQ',disconnected:'等待重连'})[c.status])}</span></div><p id="connector-message">${esc(c.message)}</p><p class="help">已保存 ${c.selected.length} 个目标群 · ${c.collecting?'收集配置已启用':'收集已暂停'}。QQ 登录恢复后会自动继续。</p><div class="two-columns"><div><div class="connection-steps"><div class="step"><div><strong>登录 QQ</strong><button class="primary" data-action="qq-scan">扫码绑定 QQ</button></div></div><div class="step"><div><strong>勾选需要收集的群</strong><p>保存后持续收集新消息，断线自动重连。${c.managed?'重连后会核对 QQ 可提供的历史消息。':'此外部连接仅收集在线期间的新消息。'}</p></div></div></div><details><summary>连接设置</summary><button data-action="qq-advanced-scan">连接自有 NapCat 实例</button><form id="connector-form" class="form-grid"><label>本机 WebSocket 地址<input name="url" value="${esc(c.url)}" required></label><label>OneBot Token<input type="password" name="token" autocomplete="off" placeholder="${c.configured?'已保存，留空保持不变':'填写 NapCat WebSocket Token'}"></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${c.enabled?'checked':''}>启用持续连接</label><button type="submit" class="primary">保存连接</button></form></details><p class="help">本次连接已保存 ${c.saved} 条记录 · 最近收到所选群消息 ${fmt(c.last_saved)}。未选群的消息不入库。</p></div><div><div class="section-title"><h2>${c.account?esc(c.account.nickname)+' · '+esc(c.account.user_id):'扫码后在这里选择群'}</h2><button data-action="refresh-groups">刷新群列表 ↻</button></div>${c.account&&!current&&c.selected.length?'<p class="warning">当前账号与原选择不同，请重新选择要收集的群。</p>':''}${accountGroups(c, selected)}</div></div></section>`;
}
async function refreshConnector(force=false){
  try{
    const [c,n]=await Promise.all([api('/api/connector'),api('/api/native')]);
    nativeState=n;updateGroupOptions();
    if(c.status==='online'&&c.collecting&&c.account?.user_id===c.selected_account&&c.selected.length){
      c.message='正在自动收集所选群的新消息';
    }
    const signature=JSON.stringify([c.status,c.managed,c.account,c.groups,c.selected,c.selected_account,c.enabled,c.url,c.saved,c.last_saved]);
    if($('#managed-history-status') && c.history_status)$('#managed-history-status').textContent=c.history_status;
    const sameAccount=connectorState?.account?.user_id===c.account?.user_id;
    const draft=new Map(sameAccount&&!force?Array.from(document.querySelectorAll('#groups-form input[name=group]'),input=>[input.value,input.checked]):[]);
    const dates=new Map(sameAccount&&!force?Array.from(document.querySelectorAll('#groups-form input[type=date]'),input=>[input.name,input.value]):[]);
    connectorState=c;
    if(view==='overview' && $('#source-issues')) $('#source-issues').innerHTML=sourceIssue();
    if(view==='connections'&&$('#native-panel')&&!nativeDraft?.dirty&&(force||(!document.activeElement.closest?.('#native-panel')&&!$('#native-panel details[open]')))){
      const top=$('#native-group-list').scrollTop;
      $('#native-panel').outerHTML=nativePanel();$('#native-group-list').scrollTop=top;
    }
    const active=document.activeElement;
    const busy=active.closest?.('#account-panel details[open], #napcat-panel details[open], #connector-form') || (active.closest?.('#groups-form') && ['INPUT','TEXTAREA','SELECT'].includes(active.tagName));
    if(view==='connections'&&(!busy||force)&&signature!==connectorSignature){
      const pickerTop=sameAccount&&!force?$('#groups-form .group-picker')?.scrollTop:0;
      const accountPanelEl = $('#account-panel');
      if (accountPanelEl) accountPanelEl.outerHTML = accountPanel();
      const panel=$('#napcat-panel');
      if(panel){
        panel.outerHTML=napcatPanel();
      }
      if(panel || accountPanelEl){
        document.querySelectorAll('#groups-form input[name=group]').forEach(input=>{if(draft.has(input.value)){input.checked=draft.get(input.value);const label=input.closest('label').nextElementSibling;if(label)label.hidden=!input.checked;}});
      }
      document.querySelectorAll('#groups-form input[type=date]').forEach(input=>{if(dates.has(input.name))input.value=dates.get(input.name);});
      const picker=$('#groups-form .group-picker');
      if(picker && pickerTop)picker.scrollTop=pickerTop;
      connectorSignature=signature;
    }else if(view==='connections'&&$('#connector-message'))$('#connector-message').textContent=c.message;
  }catch(error){if(view==='connections'&&$('#connector-message'))$('#connector-message').textContent='连接状态读取失败：'+error.message;}
}
function settingsView(){
 if(settingsTab==='identity'){
   const p=state.identity;
   return `<section class="panel"><h2>学籍信息</h2><p>按 QQ 账号保存在本机，用于筛选考试。</p>
     <form id="identity-form" data-account="${esc(p.account)}" class="form-grid">
     <div class="form-pair"><label>入学年份<input name="grade" value="${esc(p.grade)}" placeholder="例如 2024" inputmode="numeric" pattern="[0-9]{4}" maxlength="4"></label>
     <label>专业<input name="major" value="${esc(p.major)}" maxlength="80" placeholder="专业全称"></label></div>
     <label>班级<input name="class_name" value="${esc(p.class_name)}" maxlength="80" placeholder="例如 B计算机241"></label>
     <button type="submit" class="primary">保存学籍信息</button></form></section>`;
 }

 if(settingsTab==='model')return modelPanel()+`<details class="panel"><summary>关注词</summary><p>优先展示与这些词相关的有效通知、待办和资料；闲聊不会仅因命中关键词进入列表。</p><form id="settings-form" class="form-grid"><label>关键词<textarea name="interests" placeholder="用逗号或换行分隔">${esc(state.settings.interests)}</textarea><small>保存后更新本地规则结果；已有 AI 结果和待办保留。</small></label><button class="primary" type="submit">保存关注词</button></form></details>`;
 if (settingsTab === 'notifications') {
   const browserSupported = 'Notification' in window;
   const permission = browserSupported ? {default:'尚未授权', granted:'已允许', denied:'已阻止'}[Notification.permission] : '当前环境不支持';
   const macOption = /Mac/.test(navigator.platform)
     ? `<label class="checkbox-label"><input type="checkbox" id="native-notifications" ${state.settings.native_notifications?'checked':''}>兼容旧服务的 macOS 通知</label><small>桌面版已有内置提醒，无需重复开启。</small>` : '';
   return `<section class="panel"><h2>到期提醒</h2><p>桌面应用运行时会发送到期提醒，关闭窗口后仍在后台运行。请在系统设置中允许 QQmanager 通知。</p><p class="help">提醒时间按北京时间显示。休眠或关机期间无法即时通知。</p><details><summary>其他提醒方式</summary><div class="form-grid"><button data-action="enable-browser" ${browserSupported?'':'disabled'}>开启浏览器通知</button><small>浏览器通知：${permission}</small>${macOption}${state.settings.notification_error?`<p class="warning">${esc(state.settings.notification_error)}</p>`:''}</div></details></section>`;
 }

}

function modelStatus(){
  const m=state.llm||{}, c=m.counts||{};
  const phase=!m.configured?'等待配置模型':m.enabled?'自动分析已开启':!m.tested?'等待连接测试':'自动分析已暂停';
  return `<div class="section-title"><h2>${phase}</h2></div><p>已分析 ${c.done||0} 条 · 排队 ${c.pending||0} 条 · 分析中 ${c.running||0} 条 · 过滤 ${c.filtered||0} 条 · 失败 ${c.failed||0} 条${c.skipped?' · 已取消群跳过 '+c.skipped+' 条':''}</p><small>过去 24 小时已调用 ${m.calls_today||0} / ${m.daily_limit||300} 次${m.model?' · '+esc(m.model):''}。${m.calls_today>=m.daily_limit?'已达上限，剩余消息保留排队。':'规则识别和原文保存持续运行。'}</small>${m.error?`<p class="warning">${esc(m.error)}</p>`:''}`;
}
function modelPanel(){
  const m=state.llm||{};
  return `<section class="panel"><div class="section-title"><h2>模型分析</h2><span class="badge">通知 · 待办 · 资料</span></div><p>明确的通知优先在本机识别。启用 AI 后，需要判断的消息会发送脱敏片段；引用前文时，最多补充一条同群同发送者的相关片段。事项归并和课表导入也使用脱敏文字。原文留在本机供核对，调用按所选服务计费。</p><div id="model-status" class="model-status">${modelStatus()}</div><details ${m.configured?'':'open'}><summary>${m.configured?'修改模型配置':'配置模型'}</summary><form id="model-form" class="form-grid"><label>快速填写<select id="model-preset"><option value="">选择服务，或手动填写</option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="local">本机兼容接口</option></select></label><label>接口基础地址<input name="url" type="url" required value="${esc(m.url)}" placeholder="https://api.deepseek.com"><small>填写基础地址，不包含 /chat/completions。服务需支持 Chat Completions 与 JSON 输出。</small></label><label>模型名称<input name="model" required value="${esc(m.model)}" placeholder="填写服务提供的模型 ID"></label><label>API Key<input name="key" type="password" autocomplete="new-password" placeholder="${m.has_key?'已保存，留空则保持原密钥':'仅保存在本机，不会回显'}"><small>请直接在这里填写；使用免密本机服务可留空。更换服务地址时需重新填写密钥。</small></label><label class="checkbox-label"><input name="clear_key" type="checkbox">清除已保存的密钥（例如切换本机模型）</label><label>过去 24 小时调用上限<input name="daily_limit" type="number" min="1" max="5000" value="${m.daily_limit||300}" required><small>新消息分析、事项归并、课表导入和测试均计入调用上限；消息失败最多尝试 3 次。这是次数限制，不是金额预算。</small></label><div class="inline-actions"><button type="submit" class="primary">保存、测试并启用</button><button type="button" data-action="model-pause">暂停 AI 分析</button><button type="button" data-action="model-retry">重试失败消息</button><button type="button" data-action="model-review">按新规则整理已有消息</button></div><p id="model-test-result" role="status">${esc(m.test_error||'测试只发送内置的虚构通知，成功后开始分析排队的新消息。')}</p></form></details><p class="help">队列从本功能安装后接收的新消息开始；暂停期间继续排队，恢复后处理。取消勾选群后，尚未发送的消息会跳过。密钥保存在本机数据库，请勿分享 data 目录。</p></section>`;
}
// A QQ message can support several extracted facts. Keep one original per message ID.
function originalMessages(messages, items, includeReferenced=false) {
  const sources = new Map();
  function add(message) {
    const key = String(message.message_id ?? message.id);
    if (!sources.has(key)) sources.set(key, {message, quotes:new Set(), models:new Set()});
    return sources.get(key);
  }
  for (const message of messages) add(message);
  for (const item of items) {
    let refs;
    try { refs = JSON.parse(item.evidence || '[]'); } catch { continue; }
    if (!Array.isArray(refs)) continue;
    for (const ref of refs) {
      if (!ref?.message_id) continue;
      const entry = sources.get(String(ref.message_id)) || (includeReferenced && add(ref));
      if (!entry) continue;
      if (typeof ref.quote==='string' && ref.quote.trim()) entry.quotes.add(ref.quote);
      if (item.model) entry.models.add(item.model);
    }
  }
  return [...sources.values()];
}

function originalHTML({message, quotes, models}) {
  const content = messageText(message.content);
  const ranges = [], extra = [];
  for (const raw of quotes) {
    const quote=messageText(raw);
    if (!quote || (quote==='[非文字消息]' && content!==quote)) continue;
    const pos = content.indexOf(quote);
    if (pos < 0) extra.push(quote);
    else ranges.push([pos, pos+quote.length]);
  }
  ranges.sort((a,b)=>a[0]-b[0]);
  const spans = [];
  for (const range of ranges) {
    const last = spans.at(-1);
    if (last && range[0]<=last[1]) last[1]=Math.max(last[1],range[1]);
    else spans.push(range);
  }
  let text='', end=0;
  for (const [from,to] of spans) {
    text+=esc(content.slice(end,from))+'<mark>'+esc(content.slice(from,to))+'</mark>';
    end=to;
  }
  const author=[message.group_name,message.sender,message.sent_at && fmt(message.sent_at)].filter(Boolean).join(' · ');
  return `<article class="original-message"><header>${esc(author)}</header><div class="source-text">${text}${esc(content.slice(end))}</div>
    ${extra.length?`<div class="source-quotes"><strong>补充引用</strong>${extra.map(q=>`<blockquote>${esc(q)}</blockquote>`).join('')}</div>`:''}
    ${models.size?`<small>识别方式 · ${esc([...models].join('、'))}</small>`:''}</article>`;
}

function unreadDot() {
  return '<i class="unread-dot" role="img" aria-label="未读消息" title="有未读消息"></i>';
}
async function readOriginals(ids) {
  if (!state?.counts?.unread_messages) return;
  const unique=[...new Set(ids.filter(Number.isInteger))];
  for (let offset=0; offset<unique.length; offset+=500)
    await api('/api/messages/read',{ids:unique.slice(offset,offset+500)});
  if (unique.length) await refresh();
}

function openModal(title, body, detail=false){modal.classList.toggle('detail-drawer',detail);modal.setAttribute('aria-label',title);$('#modal-content').innerHTML=`<div class="modal-head"><h2>${esc(title)}</h2><button data-action="close-modal" aria-label="关闭">×</button></div><div class="modal-body">${body}</div>`;if(!modal.open)modal.showModal();}

function importDialog(mode='chat'){
  openModal(mode==='file'?'导入文件':'导入记录',`<form id="import-form" class="form-grid"><input type="hidden" name="mode" value="${mode}"><label>归属群聊<input name="group_name" placeholder="例如 学习群 / 项目群 / 兴趣社群" value="${esc(groupFilter)}" required></label><div class="dropzone" id="dropzone"><strong>拖入文件，或点击选择</strong><p>${mode==='chat'?'TXT / JSON / CSV':'PDF / DOCX / 文本文件'} · 单文件不超过 20 MB</p><input type="file" name="files" id="import-files" multiple aria-label="选择导入文件"></div>${mode==='chat'?`<div class="divider">也可以直接粘贴聊天内容</div><label><textarea name="text" id="paste-text" placeholder="2026-11-14 09:00:00 张同学\n通知：请于明天 18:00 前提交报名表。\n\n或直接粘贴一段消息（无发送时间的相对日期需手动确认）"></textarea></label><p class="help">JSON 使用 messages 数组，消息字段为 content、sender、timestamp、group_name。CSV 使用同名表头。</p>`:''}<div id="import-result" class="progress-log" hidden></div><div class="modal-actions"><button type="button" data-action="close-modal">取消</button><button class="primary" type="submit">开始导入</button></div></form>`);
  const zone=$('#dropzone');
  zone.addEventListener('dragover',event=>{event.preventDefault();zone.classList.add('drag');});
  zone.addEventListener('dragleave',()=>zone.classList.remove('drag'));
  zone.addEventListener('drop',event=>{event.preventDefault();zone.classList.remove('drag');$('#import-files').files=event.dataTransfer.files;});
}
function datetimeInput(value){if(!value)return '';const d=new Date(value);return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d).replace(' ','T');}
function reminderInput(task){
  return datetimeInput(task.remind_at ? task.remind_at*1000 : task.due);
}
function taskDialog(insight=null, task=null){
  const fixedExam=task && plannerState.workflow.fixed_exams.find(e=>e.task_id===task.id);
  if(!fixedExam && !insight?.exam_id){
    if(task)return scheduleDialog('t'+task.id);
    const matter=insight?.matter_id?workflowFor(insight.matter_id):null;
    return scheduleDialog(null,{},{
      key:matter?'m'+matter.id:'new',title:matter?.personal_title||matterTitle(insight||{})||'',
      matter,insight,deadline:insight?.suggested_deadline||null
    });
  }
  const reminder=task?reminderInput(task):datetimeInput(insight?.due);
  const exam=fixedExam||plannerState.exams.events.find(e=>e.id===insight.exam_id);
  const when=[exam?.date,[exam?.start_time,exam?.end_time].filter(Boolean).join('–')].filter(Boolean).join(' ');
  openModal(task?'编辑考试提醒':'确认考试提醒',`<form id="task-form" class="form-grid" data-task-id="${task?.id||''}" data-exam-id="${esc(insight?.exam_id||'')}">
    <label>待办内容<input name="title" required maxlength="300" value="${esc(task?.title||insight?.title)}"></label>
    ${when?`<div class="notice-date-facts"><span>考试时间</span><b>${esc(when)}</b></div>`:''}
    <div class="form-pair"><label>提醒日期（北京时间）<input type="date" name="reminder_date" value="${reminder.slice(0,10)}"></label>${clockField('提醒时间','reminder',reminder.slice(11))}</div>
    <small>${esc(insight?.date_note||'考试时间以群通知为准。')}</small>
    <button class="clear-reminder" type="button" data-action="clear-reminder">清除提醒时间</button>
    <div id="task-error" class="form-error" role="alert"></div>
    <div class="modal-actions"><button type="button" data-action="close-modal">取消</button>
    <button type="submit" class="primary">${task?'保存修改':'确认添加'}</button>
    ${task?`<button type="submit" name="status" value="${task.status==='done'?'open':'done'}">${task.status==='done'?'重新打开':'标记完成'}</button>`:''}</div></form>`);
}
function attachmentsHTML(items=[]){
 return items.map(a=>`<section class="attachment"><div class="section-title"><strong>${esc(a.name)}</strong><small>${bytes(a.size||0)}</small></div>${a.file_id?`${a.kind==='image'?`<a href="/api/preview/${a.file_id}" target="_blank" rel="noopener"><img class="attachment-image" src="/api/preview/${a.file_id}" alt="${esc(a.name)}" loading="lazy"></a>`:''}<button data-action="file-detail" data-id="${a.file_id}">${a.kind==='image'?'查看识别文字':'查看文件正文'}</button> <a href="/api/download/${a.file_id}" download>下载原文件</a><p class="help">${esc(a.parse_status||'正在解析')}</p>${a.status==='error'?`<button data-action="attachment-retry" data-id="${a.id}">重试文字识别</button>`:''}`:`<p class="help">${a.status==='unavailable'?'QQ 暂未取得附件。请在 QQ 下载后重试。':'尚未取得附件内容，需要插件 0.1.5。旧记录可尝试重新获取。'}</p><button data-action="media-refresh">重新获取附件</button>`}</section>`).join('');
}
function relatedAttachments(i){
 const mid=i.message_id||i.id,t=Date.parse(i.sent_at);
 return state.messages.filter(m=>m.id!==mid&&m.sender===i.sender&&m.live_account===i.live_account&&m.live_group===i.live_group&&m.group_name===i.group_name&&Math.abs(Date.parse(m.sent_at)-t)<=600000&&m.attachments?.length).slice(0,4);
}
async function sourceDialog(item) {
  const insight=Boolean(item.kind);
  const messageId=item.message_id ?? item.id;
  const records=[item,...(item.merged_sources||[])];
  const originals=originalMessages(records,records,true);
  const related=Object.values(plannerState?.workflow?.matters||state.matters||[])
    .filter(m=>m.source_message_ids?.includes(messageId))
    .map(m=>`<button data-plan="matter" data-id="${m.id}">${esc(matterTitle(m))} →</button>`).join('');
  const neighbors=relatedAttachments(item).map(m=>`<details class="related-media" open>
    <summary>同发送者相邻附件 · ${fmt(m.sent_at)}（请核对是否属于本通知）</summary>${attachmentsHTML(m.attachments)}</details>`).join('');
  const hasTask=insight && state.tasks.some(t=>(item.merged_ids||[item.id]).includes(t.insight_id));
  const actions=insight?`<details class="detail-explanation"><summary>识别详情</summary><p>${esc(item.reason||'')}</p>
    ${item.date_note?`<p>${esc(item.date_note)}</p>`:''}<small>来源 · ${esc(item.source||'关联的原始消息')}</small></details>
    <div class="detail-actions"><button class="primary" data-action="task-from" data-id="${item.id}" ${hasTask?'disabled':''}>${hasTask?'已设待办':'添加待办'}</button>
      <button data-action="save" data-id="${item.id}">${item.status==='saved'?'取消收藏':'收藏'}</button>
      <button data-action="read" data-id="${item.id}">标记已读</button>
      <button data-action="dismiss" data-id="${item.id}">${item.status==='dismissed'?'恢复':'忽略'}</button></div>`:'';
  openModal(insight?'信息详情':'原始消息',`
    ${insight?`${badge(item.kind)}<h2 class="detail-title">${esc(item.title)}</h2>`:''}
    <section class="source-section">${originals.map(originalHTML).join('')}</section>
    ${related?`<section><h3>关联事项</h3>${related}</section>`:''}
    ${attachmentsHTML(item.attachments)}${neighbors}
    ${item.analysis_reason?`<p class="help">${esc(item.analysis_reason)}</p>`:''}
    ${actions}${item.analysis_error?`<p class="warning">${esc(item.analysis_error)}</p>`:''}`,true);
  if (item.message_id || !item.status)
    await readOriginals(originals.map(({message})=>Number(message.message_id ?? message.id)));
}

async function handleAction(action,id){
  const i=state.insights.find(x=>x.id===id),t=state.tasks.find(x=>x.id===id),f=state.files.find(x=>x.id===id);
  switch(action){
    case 'qq-logout':
      openModal('退出 QQmanager？', `<p>将停止收集此 QQ 账号的新消息。已保存的事项、课表和文件仍可在本机查看。</p><div class="modal-actions"><button data-action="close-modal">取消</button><button id="logout-confirm" class="primary" data-action="confirm-logout" data-account="${esc(connectorState.selected_account)}" data-revision="${esc(connectorState.login_revision)}">退出登录</button></div>`);
      return;
    case 'confirm-logout': {
      const button = $('#logout-confirm');
      if (!button || button.disabled) return;
      button.disabled = true;
      try {
        await api('/api/qq/logout', {account:button.dataset.account, revision:button.dataset.revision});
        nativeDraft=null;nativeGroupSearch='';groupFilter='';localStorage.removeItem('qqm-group-filter');
        connectionTab='account';modal.close();
        await refreshConnector(true);await refresh();toast('已退出 QQmanager');
      } finally {button.disabled=false;}
      return;
    }
    case 'qq-scan':openQQScan();return;
    case 'qq-advanced-scan':openQQScan(false);return;
    case 'close-modal':modal.close();return;
    case 'import':importDialog();return;
    case 'import-files':importDialog('file');return;
    case 'source':return sourceDialog(i);
    case 'message-detail':return sourceDialog(await api('/api/message/'+id));
    case 'task-source':return sourceDialog(t.message_id?await api('/api/message/'+t.message_id):t);
    case 'read-backfill':await api('/api/backfill/read',{ids:displayedReceipts});await refresh(false);return showNotifications();
    case 'older-backfill':return showNotifications(id);
    case 'new-task':taskDialog();return;
    case 'task-from':taskDialog(i);return;
    case 'clear-reminder':{const form=$('#task-form');for(const name of ['reminder_date','reminder_hour','reminder_minute'])form.elements[name].value='';return;}
    case 'task-edit':taskDialog(null,t);return;
    case 'snooze':await api('/api/tasks/'+id,{snooze:true});toast('将在 30 分钟后再次提醒');break;
    case 'save':await api('/api/insight',{id,status:i.status==='saved'?'new':'saved'});await refresh();if(modal.open)return sourceDialog(state.insights.find(x=>x.id===id));return;
    case 'read':await api('/api/insight',{id,status:'read'});modal.close();break;
    case 'dismiss':await api('/api/insight',{id,status:i.status==='dismissed'?'new':'dismissed'});modal.close();break;
    case 'file-detail':{const file=await api('/api/file/'+id);openModal(file.name,`${currentMatter?.attachments.some(a=>a.file_id===id)?`<button data-plan="matter" data-tab="files" data-id="${currentMatter.id}">‹ 返回事项</button>`:''}<p class="help">${esc(file.parse_status)} · ${bytes(file.size)}</p>${file.kind==='image'?`<a href="/api/preview/${id}" target="_blank" rel="noopener"><img class="attachment-image" src="/api/preview/${id}" alt="${esc(file.name)}"></a>`:''}<div class="source-text">${esc(file.body?.slice(0,100000)||'暂未提取到文字。请查看原图或下载原文件。')}</div>${file.body?.length>100000?'<p class="help">仅预览前 10 万字；搜索覆盖提取全文。</p>':''}<div class="modal-actions">${/\.pdf$/i.test(file.name)?`<a href="/api/preview/${id}" target="_blank" rel="noopener">打开 PDF</a>`:''}<a class="primary" href="/api/download/${id}" download>下载原文件</a></div>`,true);return;}
    case 'media-refresh':{const r=await api('/api/native/media/refresh',{});toast(`已请求补取 ${r.messages} 条旧消息附件；需要 QQ 保持登录及插件 0.1.5`);return;}
    case 'attachment-retry':await api('/api/attachment/retry',{id});toast('已安排重新识别');return;
    case 'watch-toggle':{const w=state.watches.find(x=>x.id===id);await api('/api/watch-toggle',{id,enabled:!w.enabled});break;}
    case 'refresh-groups':await api('/api/connector/refresh',{});toast('正在刷新 QQ 登录状态与群列表');return;
    case 'bridge-details':{const b=await api('/api/bridge');$('#bridge-details').innerHTML=`<p class="help">Webhook 地址</p><div class="code-box">${esc(b.url)}</div><p class="help">Token / Secret（不要公开分享）</p><div class="code-box">${esc(b.token)}</div><p class="help">支持 Authorization: Bearer Token 或 OneBot X-Signature（HMAC-SHA1，Secret 使用上述凭据）。</p>`;return;}
    case 'notification-read':await api('/api/notifications/read',{id});await refresh();showNotifications();return;
    case 'enable-browser':if(!('Notification' in window))throw Error('当前浏览器不支持系统通知');{const p=await Notification.requestPermission();if(p==='granted')localStorage.setItem('qqm-browser-notifications','on');toast(p==='granted'?'浏览器通知已启用':'通知未获授权，站内提醒仍会保留');render();return;}
    case 'native-refresh-groups':await api('/api/native/groups/refresh',{});await refreshConnector(true);toast(['0.1.1','0.1.2','0.1.3'].includes(nativeState?.version)?'请先更新 QQ 插件以读取完整群列表':'已请求 QQ 刷新群列表，约 10 秒内同步');return;
    case 'native-add-group':openModal('添加群聊',`<form id="native-add-form" class="form-grid"><label>群号<input name="group" inputmode="numeric" pattern="[0-9]{5,20}" required placeholder="在 QQ 群资料里查看群号"></label><label>群名称（可选）<input name="name" maxlength="100" placeholder="便于辨认"></label><p class="help">添加后，点击“保存群聊选择”开始收集新消息。</p><button type="submit" class="primary">添加到列表</button></form>`);return;
    case 'native-clear-groups':ensureNativeDraft();nativeDraft.selected.clear();nativeDraft.dirty=true;renderNativeGroups();return;
    case 'native-save-groups':{
      const d=ensureNativeDraft(), dates=[...d.startDates].filter(([g])=>d.selected.has(g));
      await api('/api/native/groups',{groups:[...d.selected],names:Object.fromEntries([...d.names].filter(([g])=>d.selected.has(g))),
        start_dates:Object.fromEntries(dates.map(([g,v])=>[g,v.date])),start_grants:Object.fromEntries(dates.filter(([,v])=>v.grant).map(([g,v])=>[g,v.grant]))});
      nativeDraft=null;nativeGroupSearch='';
      await refresh(false);await refreshConnector(true);render();window.scrollTo(0,0);
      toast(state.settings.bridge_groups.length?'群聊选择已保存，QQ 将检查所选群。点「提醒」查看新补收的群名和消息原文':'已停止收集所有群；原始记录和分析结果仍保留');return;
    }
    case 'native-toggle':await api('/api/native/settings',{enabled:!nativeState?.enabled});await refreshConnector();return;
    case 'model-pause':await api('/api/llm/pause',{});toast('AI 分析已暂停；新消息继续入库并排队');break;
    case 'review-history': return trackReview(true);
    case 'model-review':{const result=await api('/api/llm/review',{});toast(`已整理本地规则，${result.queued} 条已选群消息进入重新分析队列；按现有模型调用上限处理`);break;}
    case 'model-retry':await api('/api/llm/retry',{});toast('失败消息已重新排队；需启用模型才会处理');break;
    case 'demo':await importDemo();break;
  }
  await refresh();
}
let displayedReceipts=[];
async function showNotifications(before=0){
  const batch=state.backfill_count?await api('/api/backfill?before='+before):[];
  const rows=batch.slice(0,50);
  displayedReceipts=rows.map(m=>m.id);
  const tasks=state.notifications.map(n=>`<div class="notification-item"><p>${esc(n.title)}<small>${fmt(n.created)}</small></p><button data-action="notification-read" data-id="${n.id}">知道了</button></div>`).join('');
  const history=rows.length?`<section><div class="section-title"><h3>补收消息 · ${state.backfill_count}</h3><button data-action="read-backfill">本页标记已读</button></div>${rows.map(m=>libraryRow('message-detail',m.id,m.preview,[m.group_name,'发送于 '+fmt(m.sent_at)],'')).join('')}${batch.length>50?`<button data-action="older-backfill" data-id="${rows.at(-1).id}">查看更早补收</button>`:''}</section>`:'';
  openModal('提醒',history+tasks||'<p>暂无未读提醒</p>');
}
async function importDemo(){
  const rows=[{group_name:'示例 · 设计协作群',sender:'林晓',timestamp:'2026-11-14T09:30:00+08:00',content:'项目通知：请于 2026年11月20日 18:00 前提交新版本界面设计稿，并在群里确认。'},{group_name:'示例 · 技术交流群',sender:'阿泽',timestamp:'2026-11-14T10:12:00+08:00',content:'分享一份 Python 数据分析入门资料，包含数据清洗、可视化和常见问题解答。'},{group_name:'示例 · 城市摄影群',sender:'小雨',timestamp:'2026-11-14T11:08:00+08:00',content:'周末摄影活动改期：因天气原因，集合时间调整为 2026年11月22日 09:00，地点仍在公园南门。'},{group_name:'示例 · 技术交流群',sender:'陈同学',timestamp:'2026-11-14T11:40:00+08:00',content:'解决方法：如果 CSV 打开后中文乱码，请尝试使用 UTF-8 编码重新导出。'},{group_name:'示例 · 设计协作群',sender:'林晓',timestamp:'2026-11-14T12:00:00+08:00',content:'大家中午好！'}];
  await api('/api/import',{name:'示例记录.json',text:JSON.stringify(rows),mode:'chat',group_name:'示例'});
  await api('/api/import',{name:'Python 数据分析学习路线.md',text:'# Python 数据分析学习路线\n\n第一阶段：Python 基础与 CSV 文件读取。\n第二阶段：使用 pandas 清洗数据，处理缺失值与重复记录。\n第三阶段：使用 matplotlib 做数据可视化。\n建议先完成一个自己的学习项目。',mode:'file',group_name:'示例 · 技术交流群'});
  toast('示例已导入；示例群均有明确标记，未自动创建提醒。');
}
async function fileBase64(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=()=>reject(Error('文件读取失败'));r.readAsDataURL(file);});}
document.addEventListener('click',async event=>{
  if(!$('#group-filter-wrap').contains(event.target))closeGroupMenu();
  const button=event.target.closest('button,a');if(!button)return;
  if(button.id==='group-filter'){if(groupSelecting)closeGroupMenu();else{updateGroupOptions();groupSelecting=true;$('#group-options').hidden=false;button.setAttribute('aria-expanded','true');}return;}
  if(button.hasAttribute('data-group-value')){groupFilter=button.dataset.groupValue;localStorage.setItem('qqm-group-filter',groupFilter);closeGroupMenu();$('#group-filter').focus();await refresh();return;}
  if(button.dataset.removeGroup){const input=button.closest('.native-group-row').querySelector('[data-native-group]');toggleNativeGroup(input,false);input.focus({preventScroll:true});return;}
  if(button.dataset.view==='settings' && button.id!=='analysis-status'){
    connectionTab='account';
    if(view==='connections')render();else setView('connections');
    return;
  }
  if(button.dataset.view){if(button.id==='analysis-status'){settingsTab='model';if(view==='settings')render();}event.preventDefault();setView(button.dataset.view);return;}
  if(button.dataset.themeChoice){applyTheme(button.dataset.themeChoice);return;}
  if(button.dataset.jump){
    const section=document.getElementById(button.dataset.jump);
    section?.focus({preventScroll:true});
    section?.scrollIntoView({block:'start'});
    return;
  }
  if(button.dataset.sourceIssue){connectionTab=button.dataset.sourceIssue;setView('connections');return;}
  if(button.dataset.route){if(button.dataset.route==='connections'){connectionTab='account';if(view==='connections')render();}setView(button.dataset.route);return;}
  if(button.dataset.settingsTab){settingsTab=button.dataset.settingsTab;if(view==='settings')render();else setView('settings');return;}
  if(button.dataset.connectionTab){connectionTab=button.dataset.connectionTab;if(view==='connections')render();else setView('connections');return;}
  if (button.dataset.heatDay || button.dataset.libraryDay) {
    const day = button.dataset.heatDay || button.dataset.libraryDay;
    if (button.dataset.libraryDay && libraryOpen === day) {
      libraryOpen = ''; libraryOffset = 0; render();
      document.getElementById('library-day-'+day)?.focus({preventScroll:true});
    } else await openLibraryDay(day, 0, !!button.dataset.heatDay);
    return;
  }
  if (button.hasAttribute('data-day-offset')) {
    await openLibraryDay(libraryOpen, Number(button.dataset.dayOffset), true);
    return;
  }
  if(button.dataset.action){try{await handleAction(button.dataset.action,Number(button.dataset.id));}catch(error){toast(error.message);}}
});
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target, data=new FormData(form),submit=event.submitter||form.querySelector('[type=submit]');
  if(submit)submit.disabled=true;
  try{
    if(form.id==='native-add-form'){
      const group=String(data.get('group')).trim(),name=String(data.get('name')||'').trim();
      if(!/^[0-9]{5,20}$/.test(group))throw Error('群号应为 5–20 位数字');
      ensureNativeDraft();nativeDraft.selected.add(group);nativeDraft.names.set(group,name||nativeDraft.names.get(group)||'群 '+group);nativeDraft.dirty=true;
      modal.close();renderNativeGroups();toast('已添加到列表，请保存群聊选择');
    }else if(form.id==='import-form'){
      const files=Array.from($('#import-files').files),text=String(data.get('text')||'').trim(),mode=data.get('mode');
      if(!files.length&&!text)throw Error('请选择文件或粘贴聊天内容');
      if(text&&mode!=='chat')throw Error('粘贴内容用于聊天记录，请切换为聊天类型或清空粘贴区');
      const log=$('#import-result');log.hidden=false;log.textContent='正在导入…\n';let successes=0;
      const upload=async body=>{try{const r=await api('/api/import',body);successes++;log.textContent+=`${body.name}：新增 ${r.messages??r.files??0} 条，重复 ${r.duplicates||0} 条${r.insights!==undefined?'，重要信息 '+r.insights+' 条':''}${r.parse_status?'，'+r.parse_status:''}\n`;}catch(error){log.textContent+=body.name+'：失败 · '+error.message+'\n';}};
      for(const file of files){if(file.size>20*1024*1024){log.textContent+=file.name+'：超过 20 MB，已跳过\n';continue;}await upload({name:file.name,data:await fileBase64(file),mode,group_name:data.get('group_name')});}
      if(text)await upload({name:'粘贴记录.txt',text,mode:'chat',group_name:data.get('group_name')});
      await refresh();toast(`导入处理结束，${successes} 份内容成功。详情见导入结果。`);
    }else if(form.id==='task-form'){
      const day=data.get('reminder_date'), clock=readClock(form,'reminder');
      if(Boolean(day)!==Boolean(clock))throw Error('请同时选择提醒日期和时间，或清除提醒时间');
      const due=day?day+'T'+clock:'';
      const body={title:data.get('title'),due:due?due+':00+08:00':null};
      const original=state.tasks.find(t=>t.id===Number(form.dataset.taskId));
      if(original && event.submitter?.name==='status')body.status=event.submitter.value;
      if(original && reminderInput(original)===due)delete body.due;
      const examId=form.dataset.examId;
      if(examId&&!form.dataset.taskId)body.exam_id=examId;
      await api(examId&&!form.dataset.taskId?'/api/planner/exam/confirm':'/api/tasks'+(form.dataset.taskId?'/'+form.dataset.taskId:''),body);
      modal.close();await refresh();
      if(body.status){toast(body.status==='done'?'待办已完成':'待办已重新打开');return;}
      if(examId){await examDialog(examId);toast(due?'已保存考试提醒，可在待办中查看':'已保存考试待办，尚未设置提醒时间');}else toast('待办已保存');
    }else if(form.id==='watch-form'){
      await api('/api/watches',Object.fromEntries(data));await refresh();toast('监听已添加，将在 10 秒内扫描');
    }else if(form.id==='bridge-form'){
      const groups=data.get('groups').split(/[,，\s]+/).filter(Boolean);if(data.get('enabled')&&!groups.length)throw Error('请先填写至少一个接入群号');
      await api('/api/settings',{bridge_enabled:!!data.get('enabled'),bridge_groups:groups});await refresh();toast('接入配置已保存');
    }else if(form.id==='connector-form'){
      await api('/api/connector/settings',{url:data.get('url'),token:data.get('token'),enabled:!!data.get('enabled')});
      toast('连接设置已保存');await refreshConnector(true);
    }else if(form.id==='groups-form'){
      const groups=data.getAll('group');
      const start_dates=Object.fromEntries(groups.filter(id=>data.get('start-'+id)).map(id=>[id,data.get('start-'+id)]));
      await api('/api/connector/groups',{groups,start_dates});
      await refresh();await refreshConnector(true);window.scrollTo(0,0);toast(groups.length?(connectorState.managed?'已启用补收和日程识别，点「提醒」查看补收原文':'已保存所选群，开始收集新消息'):'已暂停群消息收集');
    }else if(form.id==='native-groups-form'){
      await api('/api/native/pair',{account:data.get('account').trim(),groups:[...ensureNativeDraft().selected]});nativeDraft=null;await refresh();await refreshConnector(true);toast('绑定账号已保存');
    }else if(form.id==='model-form'){
      $('#model-test-result').textContent='正在保存配置并用虚构通知测试，最长约 45 秒…';
      await api('/api/llm/config',{url:data.get('url'),model:data.get('model'),key:data.get('key'),clear_key:!!data.get('clear_key'),daily_limit:Number(data.get('daily_limit'))});
      form.querySelector('[name=key]').value='';
      const result=await api('/api/llm/test',{enable:true});
      await refresh();toast(result.message);
    }else if(form.id==='identity-form'){
      await api('/api/identity',{account:form.dataset.account,grade:data.get('grade'),major:data.get('major'),class_name:data.get('class_name')});
      await refresh();toast('学籍信息已保存，考试日程已更新');
    }else if(form.id==='settings-form'){
      await api('/api/settings',{interests:data.get('interests')});await refresh();toast('关注词已保存，历史消息已重新分析');
    }
  }catch(error){if(form.id==='task-form')$('#task-error').textContent=error.message;else if(form.id==='model-form'){if($('#model-test-result'))$('#model-test-result').textContent='未启用：'+error.message;await refresh(false);}else toast(error.message);}
  finally{if(submit)submit.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.id==='native-group-search'){nativeGroupSearch=event.target.value;renderNativeGroups();}});
document.addEventListener('change',async event=>{
 if(event.target.matches('#groups-form input[name=group]')){const start=event.target.closest('.checkbox-label')?.nextElementSibling;if(start?.hasAttribute('data-start-for'))start.hidden=!event.target.checked;}
if(event.target.dataset.nativeGroup){toggleNativeGroup(event.target,event.target.checked);return;}
if(event.target.dataset.nativeDate){
  const input=event.target, d=ensureNativeDraft();
  if(input.value && input.value!==input.dataset.original)d.startDates.set(input.dataset.nativeDate,{date:input.value,grant:input.dataset.grant});
  else d.startDates.delete(input.dataset.nativeDate);
  d.dirty=true;nativeCount();return;
}
if(event.target.id==='library-year'){
  libraryYear=event.target.value;libraryOpen='';libraryOffset=0;render();$('#library-year')?.focus();return;
}
if(event.target.id==='topic-filter'){topicFilter=event.target.value;render();document.querySelector('#topic-filter')?.focus();return;}
if(event.target.id==='read-filter'){category=event.target.value;render();return;}
if(event.target.id==='sort-order'){sortOrder=event.target.value;render();return;}
if(event.target.id==='model-preset'){
  const presets={deepseek:['https://api.deepseek.com','deepseek-flash'],openai:['https://api.openai.com/v1',''],local:['http://127.0.0.1:11434/v1','']};
  const p=presets[event.target.value];if(p){const f=$('#model-form');f.elements.url.value=p[0];f.elements.model.value=p[1];}
}
if(event.target.id==='native-notifications'){try{await api('/api/settings',{native_notifications:event.target.checked});toast('系统通知设置已保存');}catch(error){event.target.checked=!event.target.checked;toast(error.message);}}});
$('#import-button').addEventListener('click',()=>importDialog(view==='files'?'file':'chat'));
$('#new-task-button').addEventListener('click',()=>taskDialog());
$('#notifications-button').addEventListener('click',()=>showNotifications().catch(e=>toast(e.message)));
$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>refresh(),220);});
$('#group-filter-wrap').addEventListener('keydown',event=>{
 if(event.key==='Escape'){closeGroupMenu();$('#group-filter').focus();return;}
 if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
 event.preventDefault();
 if(!groupSelecting){updateGroupOptions();groupSelecting=true;$('#group-options').hidden=false;$('#group-filter').setAttribute('aria-expanded','true');}
 const options=Array.from($('#group-options').querySelectorAll('button')),current=options.indexOf(document.activeElement);
 const next=event.key==='Home'?0:event.key==='End'?options.length-1:event.key==='ArrowUp'?(current<=0?options.length-1:current-1):(current+1)%options.length;
 options[next]?.focus();
});
window.addEventListener('hashchange',()=>setView(location.hash.slice(1)));
window.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){if(!$('#searchbar').hidden){e.preventDefault();$('#search').focus();}}});
async function boot(){try{csrf=(await api('/api/session')).csrf;trackReview();const initial=location.hash.slice(1);taskFilter=routeTaskFilter(initial);view=taskFilter==='open'?canonicalView(initial):'tasks';await refresh();await refreshConnector(true);if(view==='home' && connectorState && !connectorState.selected_account && !connectorState.login_revision && !sessionStorage.getItem('qq-binding-seen')){sessionStorage.setItem('qq-binding-seen','1');openQQScan();}setInterval(()=>{document.querySelectorAll('[data-expires]').forEach(tag=>{tag.hidden=Date.now()<Number(tag.dataset.expires);});if(groupSelecting){refresh(false);return;}refreshConnector();const busy=modal.open||['connections','settings'].includes(view)||$('#content').contains(document.activeElement)||$('#content details[open]:not(.future-tasks)')||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName);const expiredConfirmation=view==='overview' && $('#work-confirm [data-expires]:not([hidden])');
    const queueUntil=view==='home' && $('[data-queue-until]')?.dataset.queueUntil;
    const listUntil=$('[data-list-until]')?.dataset.listUntil;
    refresh(!busy || Boolean(expiredConfirmation) || Boolean(queueUntil && Number(queueUntil)<=Date.now()) || Boolean(listUntil && Number(listUntil)<=Date.now()));},5000);}catch(error){notice(error.message);}}
boot();
