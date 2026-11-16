'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {home:'主页',timetable:'我的课表',insights:'事项',files:'文件资料',tasks:'待办提醒',messages:'原始记录',connections:'自动接入',settings:'设置'};
let nativeState = null;
let settingsTab='model', connectionTab='qq', sortOrder='latest', topicFilter='全部';
let groupFilter=localStorage.getItem('qqm-group-filter')||'', groupOptionsSignature='', groupSelecting=false;
const canonicalView = name => labels[name]?name:'home';
function applyTheme(theme){document.documentElement.dataset.theme=theme;localStorage.setItem('qqm-theme',theme);document.querySelectorAll('[data-theme-choice]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.themeChoice===theme));});}
applyTheme(localStorage.getItem('qqm-theme')==='dark'?'dark':'light');
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
async function refresh(renderPage=true) {
  const sequence=++loadSequence;
  try {
    const query=$('#search').value, group=groupFilter;
    const [data,calendar]=await Promise.all([api('/api/state?'+new URLSearchParams({q:query,group})),api('/api/planner?'+new URLSearchParams(plannerDate?{date:plannerDate}:{}))]);
    if(sequence!==loadSequence||group!==groupFilter||query!==$('#search').value)return;
    state=data;plannerState=calendar;notice();
    for(const k of ['insights','files','tasks']) $('#nav-'+k).textContent=state.counts[k];
    $('#nav-insights').textContent=state.counts.matters;
    $('#notification-count').textContent=state.notifications.length||'';
    updateGroupOptions();
    if(renderPage)render();
    else if($('#model-status'))$('#model-status').innerHTML=modelStatus();
    deliverBrowserNotifications();
  } catch(error) {notice('无法连接本地服务：'+error.message+'。请确认启动窗口仍在运行。');}
}
function updateGroupOptions(){
 if(groupSelecting)return;
 const names=[...new Set([...(state?.groups||[]),...(nativeState?.groups||[]).filter(g=>g.selected).map(g=>g.group_name),...(groupFilter?[groupFilter]:[])])].sort((a,b)=>a.localeCompare(b,'zh-CN'));
 const signature=JSON.stringify([names,groupFilter]);
 if(signature===groupOptionsSignature)return;
 $('#group-options').innerHTML=['',...names].map(g=>`<button role="option" aria-selected="${g===groupFilter}" data-group-value="${esc(g)}">${g===groupFilter?'✓ ':''}${esc(g||'全部群聊')}</button>`).join('');
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
function setView(next){
  const target=canonicalView(next);if(target===view)return;
  view=target;category='全部';topicFilter='全部';$('#search').value='';
  location.hash=view;render();refresh();window.scrollTo(0,0);
}

function render(){
  if(!state)return;
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('#page-title').textContent=labels[view];
  $('#searchbar').hidden=['connections','settings','timetable'].includes(view);
  $('#search').placeholder=({home:'搜索事项或相关消息',insights:'搜索事项或相关消息',files:'搜索文件名或正文',tasks:'搜索待办',messages:'搜索原始消息'})[view]||'搜索';
  $('#import-button').hidden=!['files','messages'].includes(view);
  $('#import-button').textContent=view==='files'?'导入文件':'导入记录';
  $('#new-task-button').hidden=view!=='tasks';
  $('#content').innerHTML=({home:homeView,timetable:timetableView,insights:insightsView,files:filesView,tasks:tasksView,messages:messagesView,connections:connectionsView,settings:settingsView})[view]();
  const m=state.llm||{},failed=m.counts?.failed||0;
  $('#analysis-status').textContent=failed?`${failed} 条分析失败`:m.enabled?'AI 已开启':'本地识别';
  $('#analysis-status').title=failed?'打开设置查看失败详情':'打开模型设置';
}

function insightCard(i){
  return `<button class="info-row ${i.status==='read'?'is-read':''}" data-action="source" data-id="${i.id}" aria-label="查看：${esc(i.title)}"><span class="row-mark" aria-hidden="true">${i.status==='saved'?'☆':i.status==='new'?'•':''}</span><span class="row-content"><span class="row-title">${esc(i.title)}</span><span class="row-meta"><span>${esc(i.kind)}</span><span>${esc(i.group_name)}</span>${i.status==='saved'?'<span>已收藏</span>':''}${i.merged_count>1?`<span>合并 ${i.merged_count} 条</span>`:''}</span></span><time>${fmt(i.sent_at)}</time><span class="row-arrow" aria-hidden="true">›</span></button>`;
}

function taskRow(t){
 const overdue=t.remind_at&&t.remind_at*1000<Date.now()&&t.status==='open';
 return `<div class="task-row ${t.status==='done'?'done':''}"><button class="task-check" data-action="task-toggle" data-id="${t.id}" aria-label="${t.status==='done'?'重新打开':'完成待办'}：${esc(t.title)}">${t.status==='done'?'✓':''}</button><button class="task-body" data-action="task-edit" data-id="${t.id}"><span class="row-title">${esc(t.title)}</span><span class="row-meta">${t.remind_at?fmt(t.remind_at):t.due?fmt(t.due):'无提醒时间'}${overdue?' · 已到期':''}${t.group_name?' · '+esc(t.group_name):''}</span></button><div class="task-actions">${t.status==='open'?`<button data-action="snooze" data-id="${t.id}">稍后提醒</button>`:''}${t.insight_id?`<button data-action="task-source" data-id="${t.id}">原文</button>`:''}</div></div>`;
}

function insightsView(){
 let items=(state.matters||[]).filter(i=>category==='已忽略'?i.status==='dismissed':i.status!=='dismissed'&&(category==='全部'||category==='未读'&&i.status==='new'||category==='已收藏'&&(i.saved||i.status==='saved')));
 const stamp=i=>typeof i.sent_at==='number'?i.sent_at*1000:Date.parse(i.sent_at)||0;
 if(sortOrder==='latest')items.sort((a,b)=>stamp(b)-stamp(a)||b.id-a.id);
 const grouped=QQFilters.group(items), counts=new Map(grouped.map(g=>[g.name,g.items.length]));
 const topics=['全部',...QQFilters.topics];
 const selected=topicFilter==='全部'?items:items.filter(i=>QQFilters.topic(i)===topicFilter);
 const toolbar=`<div class="toolbar topic-toolbar"><div class="tabs topic-tabs" aria-label="关键词分类">${topics.filter(t=>t==='全部'||counts.has(t)||t===topicFilter).map(t=>`<button aria-pressed="${topicFilter===t}" class="${topicFilter===t?'active':''}" data-topic="${t}">${t}<span class="topic-count">${t==='全部'?items.length:counts.get(t)||0}</span></button>`).join('')}</div><div class="filter-actions"><select id="read-filter" aria-label="阅读状态">${['全部','未读','已收藏','已忽略'].map(c=>`<option value="${c}" ${category===c?'selected':''}>${c==='全部'?'全部状态':c}</option>`).join('')}</select><select id="sort-order" aria-label="信息排序"><option value="latest" ${sortOrder==='latest'?'selected':''}>最新优先</option><option value="priority" ${sortOrder==='priority'?'selected':''}>重要优先</option></select></div></div>`;
 const body=topicFilter==='全部'?grouped.map(g=>`<section class="topic-section"><div class="topic-heading"><h2>${g.name}<span>${g.items.length}</span></h2>${g.items.length>3?`<button data-topic="${g.name}">查看全部 ${g.items.length} 项 ›</button>`:''}</div><div class="info-list">${g.items.slice(0,3).map(matterCard).join('')}</div></section>`).join(''):`<div class="list-caption">${esc(topicFilter)} · ${selected.length} 项${groupFilter?' · '+esc(groupFilter):''}</div><div class="info-list">${selected.map(matterCard).join('')}</div>`;
 return toolbar+(selected.length?body:textEmpty('当前分类暂无信息','可切换分类、群聊或搜索关键词。'));
}

function filesView(){return `<div class="list-caption">${state.files.length} 个文件</div>${state.files.length?`<table class="file-table"><thead><tr><th>文件</th><th class="hide-mobile">来源</th><th>状态</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>${state.files.map(f=>`<tr><td><button class="file-open" data-action="file-detail" data-id="${f.id}"><span class="row-title">${esc(f.name)}</span><span class="row-meta">${bytes(f.size)} · ${fmt(f.created)}</span></button>${$('#search').value&&f.excerpt?`<p class="file-match">${esc(f.excerpt.slice(0,110))}</p>`:''}</td><td class="hide-mobile">${esc(f.group_name)}</td><td>${esc(f.parse_status)}</td><td><button data-action="file-detail" data-id="${f.id}">查看</button></td></tr>`).join('')}</tbody></table>`:textEmpty('暂无文件','导入文件，或在接入页设置资料文件夹。')}`;}

function tasksView(){let tasks=state.tasks.filter(t=>t.status===taskFilter);const q=$('#search').value.toLowerCase(),group=groupFilter;tasks=tasks.filter(t=>(!q||t.title.toLowerCase().includes(q))&&(!group||t.group_name===group));return `<div class="toolbar"><div class="tabs"><button class="${taskFilter==='open'?'active':''}" aria-pressed="${taskFilter==='open'}" data-task-filter="open">待处理</button><button class="${taskFilter==='done'?'active':''}" aria-pressed="${taskFilter==='done'}" data-task-filter="done">已完成</button></div><span class="result-count">${tasks.length} 项 · 北京时间</span></div>${tasks.length?'<div class="task-list">'+tasks.map(t=>taskRow(t)).join('')+'</div>':textEmpty(taskFilter==='open'?'暂无待办':'暂无已完成事项','从信息详情添加，或点击右上角新建。')}`;}

function messagePreview(m){
 const images=(m.attachments||[]).filter(a=>a.kind==='image');
 if(!images.length)return m.content;
 const text=m.content.replace(/\[图片：未解析\]/g,'').trim();
 if(text)return text;
 const first=images.find(a=>a.text?.trim());
 return first?'图片 · '+first.text.trim().split('\n')[0]:images.some(a=>a.file_id)?`图片 · ${images.length} 张（可查看原图）`:'图片 · 待获取';
}
function messagesView(){return `<div class="list-caption">${state.messages.length} 条记录 · 最近 500 条</div>${state.messages.length?`<div class="info-list">${state.messages.map(m=>`<button class="info-row" data-action="message-detail" data-id="${m.id}"><span class="row-content"><span class="row-title">${esc(messagePreview(m))}</span><span class="row-meta">${esc(m.group_name)} · ${esc(m.sender)}${m.analysis_state==='filtered'?' · 已过滤':''}</span></span><time>${fmt(m.sent_at)}</time><span class="row-arrow" aria-hidden="true">›</span></button>`).join('')}</div>`:textEmpty('暂无记录','所选群的新消息会自动保存在这里，也可以导入聊天记录。')}`;}

function connectionsView(){const s=state.settings;const tabs=`<div class="tabs page-tabs">${[['qq','QQ 插件'],['folder','文件夹'],['other','其他接入']].map(([key,title])=>`<button data-connection-tab="${key}" class="${connectionTab===key?'active':''}" aria-pressed="${connectionTab===key}">${title}</button>`).join('')}</div>`;
 if(connectionTab==='qq')return tabs+nativePanel();
 if(connectionTab==='folder')return tabs+`<section class="panel"><div class="section-title"><h2>文件夹自动监听</h2><span class="badge">开箱可用</span></div><p>选定本机资料目录。每 10 秒扫描一次，自动导入新文件及内容变化；相同内容不会重复收纳。仅扫描当前目录，不包含子文件夹。</p><form id="watch-form" class="form-grid"><label>文件夹完整路径<input name="path" placeholder="例如 /Users/你的用户名/Downloads/群资料" required></label><label>内容类型<select name="mode"><option value="file">资料文件：索引文件名与正文</option><option value="chat">聊天记录：提取消息与重要信息</option></select></label><label>归属群聊<input name="group_name" placeholder="例如 工作交流群" required></label><button class="primary" type="submit">开始监听</button></form><div class="help">支持 TXT、MD、JSON、CSV、LOG、PDF、DOCX；单文件 20 MB，目录最多 1,000 个支持的文件。不会读取 QQ 的加密聊天数据库。</div>${state.watches.map(w=>`<div class="watch-card"><strong>${esc(w.path)}</strong><p>${esc(w.group_name)} · ${w.mode==='chat'?'聊天记录':'资料文件'} · ${w.enabled?'监听中':'已暂停'} · 最近扫描 ${fmt(w.last_scan)}</p>${w.error?`<p class="warning">${esc(w.error)}</p>`:''}<button data-action="watch-toggle" data-id="${w.id}">${w.enabled?'暂停监听':'继续监听'}</button></div>`).join('')}</section>`;
 return tabs+`<p class="help">供已有外部适配端的用户配置；使用客户端插件时无需设置。</p><details class="panel"><summary>NapCat 连接</summary>${napcatPanel()}</details><details class="panel"><summary>OneBot HTTP 上报</summary><section class="panel"><div class="section-title"><h2>QQ 消息自动接入</h2><span class="badge amber">需外部适配端</span></div><div class="status-line"><span class="dot"></span>${s.bridge_enabled?'接收端已启用':'接收端未启用'} · ${s.last_event?'最近收到消息 '+fmt(s.last_event):'尚未收到群消息'}</div><p>支持 OneBot v11 HTTP 上报，例如已有的 NapCat 适配端。配置后只接收指定群的新消息，无需逐次导入。</p><div class="connection-steps"><div class="step"><div><strong>准备并登录外部 QQ 适配端</strong><p>本软件不包含 QQ 登录组件；第三方适配端的稳定性和账号限制需自行评估。</p></div></div><div class="step"><div><strong>配置 HTTP Webhook 上报</strong><p>使用下方接收地址与凭据，消息格式选择 Array。仅连接本机适配端。</p></div></div><div class="step"><div><strong>填入要接入的群号</strong><p>只保存这些群的新消息。历史聊天不会自动补齐；群文件事件只登记文件名，正文需实际导入。</p></div></div></div><form id="bridge-form" class="form-grid"><label>接入群号（英文逗号分隔）<input name="groups" value="${esc(s.bridge_groups.join(','))}" placeholder="123456789,987654321"></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${s.bridge_enabled?'checked':''}>启用自动接入</label><button class="primary" type="submit">保存接入配置</button></form><div class="inline-actions"><button data-action="bridge-details">显示接收地址与凭据</button></div><div id="bridge-details"></div><p class="help">QQ 官方机器人接入另有权限要求，第一版尚未实现官方 SDK 连接。此处是独立接收适配器，不是 QQ 官方客户端插件。</p></section></details>`;
}

let nativeDraft=null, nativeGroupSearch='';
function ensureNativeDraft(){
  const account=connectorState?.selected_account||'';
  if(!nativeDraft||nativeDraft.account!==account||!nativeDraft.dirty){
    const rows=nativeState?.groups||[];
    nativeDraft={account,selected:new Set(state.settings.bridge_groups),names:new Map(rows.map(g=>[g.group_id,g.group_name])),dirty:false};
    state.settings.bridge_groups.forEach(g=>{if(!nativeDraft.names.has(g))nativeDraft.names.set(g,'群 '+g);});
  }
  return nativeDraft;
}
function nativeGroupRows(){
  const d=ensureNativeDraft();
  return [...d.names].filter(([id,name])=>(id+' '+name).toLowerCase().includes(nativeGroupSearch.toLowerCase().trim())).sort((a,b)=>Number(d.selected.has(b[0]))-Number(d.selected.has(a[0]))).map(([id,name])=>`<div class="native-group-row"><label><input type="checkbox" data-native-group="${esc(id)}" ${d.selected.has(id)?'checked':''}><span>${esc(name)}<small>${esc(id)}</small></span></label>${d.selected.has(id)?`<button type="button" data-remove-group="${esc(id)}" aria-label="移除 ${esc(name)}">移除</button>`:'<span class="group-unselected">未收集</span>'}</div>`).join('')||'<p class="help">没有匹配的群聊。可清空搜索或刷新群列表。</p>';
}
function renderNativeGroups(){
  $('#native-group-list').innerHTML=nativeGroupRows();
  $('#native-group-count').textContent=`已选 ${nativeDraft.selected.size} 个群${nativeDraft.dirty?' · 未保存':''}`;
}
function nativePanel(){
  const n=nativeState||{}, signals=n.signals||{}, hooked=!!signals['callback_hooked:onRecvMsg'];
  const d=ensureNativeDraft();
  const incompatible=!hooked&&Object.keys(signals).some(key=>key.startsWith('unwritable:'));
  const phase=!n.enabled?'客户端插件已暂停':!n.connected?'等待 QQ 客户端插件连接':incompatible?'当前 QQ 接口不兼容，消息尚未开始收集':!hooked?'插件已连接，正在验证消息监听':!n.account_matches?'监听已就绪，等待新消息确认绑定账号':'客户端消息监听已连接';
  return `<section id="native-panel" class="panel"><div class="section-title"><h2>收集群聊</h2><span id="native-group-count" class="badge">已选 ${d.selected.size} 个群${d.dirty?' · 未保存':''}</span></div><div class="status-line"><span class="dot"></span>${phase}</div><p>勾选要收集的群，取消勾选或移除即可停止收集。保存后约 10 秒生效，已有记录保留。</p><input id="native-group-search" type="search" aria-label="搜索群聊" placeholder="搜索群名或群号" value="${esc(nativeGroupSearch)}"><div id="native-group-list" class="native-group-list">${nativeGroupRows()}</div><div class="inline-actions"><button type="button" data-action="native-refresh-groups">刷新 QQ 群列表</button><button type="button" data-action="native-clear-groups">取消全选</button><button type="button" class="primary" data-action="native-save-groups">保存群聊选择</button></div><p class="help">${n.groups_updated?`QQ 群列表已同步 ${n.groups_count} 个 · ${fmt(n.groups_updated)}。未勾选的群不收集消息。`:['0.1.1','0.1.2','0.1.3'].includes(n.version)?'完整列表需要更新 QQ 插件。请从应用菜单打开插件安装助手，更新后重启 QQ。':'等待 QQ 同步群列表。请保持 QQ 登录，也可点击刷新。'}</p><details ${d.account?'':'open'}><summary>账号与备用添加方式</summary><button type="button" data-action="native-add-group">按群号添加</button><p>使用你正常登录的 QQ，无需重复登录。QQ 需保持运行；离线历史不自动补齐。</p><form id="native-groups-form" class="form-grid"><label>日常使用的 QQ 账号<input name="account" inputmode="numeric" pattern="[0-9]{5,20}" value="${esc(d.account)}" required></label><button type="submit">保存绑定账号</button></form><p>本次保存 ${n.saved||0} 条 · 最近收到 ${fmt(n.last_event)} · 最近心跳 ${fmt(n.last_seen)}</p><small>客户端排队 ${n.queued||0} 条${n.dropped?' · 队列满已跳过 '+n.dropped+' 条':''}</small>${n.error?`<p class="warning">${esc(n.error)}</p>`:''}</details><div class="inline-actions"><button data-action="native-toggle" ${state.settings.bridge_groups.length?'':'disabled'}>${n.enabled?'暂停插件收集':'启用已配对插件'}</button></div></section>`;
}
function napcatPanel(){
  const c=connectorState;
  if(!c)return '<div id="napcat-panel" class="panel"><p>正在检查本机 NapCat 连接…</p></div>';
  const current=c.account&&c.selected_account===c.account.user_id;
  const selected=current?c.selected:[];
  return `<section id="napcat-panel" class="panel"><div class="section-title"><h2>NapCat 实时连接</h2><span class="badge ${c.status==='online'?'':'amber'}">${esc(({disabled:'未启用',connecting:'连接中',waiting_login:'等待 QQ 登录',online:'已连接 QQ',disconnected:'等待重连'})[c.status])}</span></div><p id="connector-message">${esc(c.message)}</p><p class="help">已保存 ${c.selected.length} 个目标群 · ${c.collecting?'收集配置已启用':'收集已暂停'}。QQ 登录恢复后会自动继续。</p><div class="two-columns"><div><div class="connection-steps"><div class="step"><div><strong>启动并登录 NapCat</strong><p><a href="http://127.0.0.1:6099/webui" target="_blank" rel="noopener">打开本机 NapCat 登录页 ↗</a>，用手机 QQ 扫码。登录会话仅保存在本机容器数据目录。</p></div></div><div class="step"><div><strong>勾选需要收集的群</strong><p>保存后持续收集新消息，断线自动重连。历史记录不自动补齐，电脑休眠或 QQ 掉线时无法收集。</p></div></div></div><details><summary>连接设置</summary><form id="connector-form" class="form-grid"><label>本机 WebSocket 地址<input name="url" value="${esc(c.url)}" required></label><label>OneBot Token<input type="password" name="token" autocomplete="off" placeholder="${c.configured?'已保存，留空保持不变':'填写 NapCat WebSocket Token'}"></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${c.enabled?'checked':''}>启用持续连接</label><button type="submit" class="primary">保存连接</button></form></details><p class="help">本次连接已保存 ${c.saved} 条记录 · 最近收到所选群消息 ${fmt(c.last_saved)}。未选群的消息不入库。</p></div><div><div class="section-title"><h2>${c.account?esc(c.account.nickname)+' · '+esc(c.account.user_id):'扫码后在这里选择群'}</h2><button data-action="refresh-groups">刷新群列表 ↻</button></div>${c.account&&!current&&c.selected.length?'<p class="warning">当前账号与原选择不同，请重新选择要收集的群。</p>':''}<form id="groups-form" class="form-grid"><div class="group-picker">${c.groups.length?c.groups.map(g=>`<label class="checkbox-label"><input type="checkbox" name="group" value="${esc(g.group_id)}" ${selected.includes(g.group_id)?'checked':''}><span>${esc(g.group_name)}<small>${esc(g.group_id)} · ${esc(g.member_count)} 人</small></span></label>`).join(''):'<p class="help">尚未读取到群列表。先完成 NapCat 登录并连接，再点击刷新。</p>'}</div><button class="primary" type="submit" ${c.status==='online'?'':'disabled'}>保存所选群并自动收集</button><small>取消全部勾选并保存可暂停收集。只保存新到达的指定群消息及群文件元数据。</small></form></div></div></section>`;
}
async function refreshConnector(force=false){
  try{
    const [c,n]=await Promise.all([api('/api/connector'),api('/api/native')]);
    nativeState=n;updateGroupOptions();
    if(c.status==='online'&&c.collecting&&c.account?.user_id===c.selected_account&&c.selected.length){
      c.message='正在自动收集所选群的新消息';
    }
    const signature=JSON.stringify([c.status,c.account,c.groups,c.selected,c.selected_account,c.enabled,c.url,c.saved,c.last_saved]);
    const sameAccount=connectorState?.account?.user_id===c.account?.user_id;
    const draft=new Map(sameAccount&&!force?Array.from(document.querySelectorAll('#groups-form input[name=group]'),input=>[input.value,input.checked]):[]);
    connectorState=c;
    if(view==='connections'&&$('#native-panel')&&!nativeDraft?.dirty&&(force||(!document.activeElement.closest?.('#native-panel')&&!$('#native-panel details[open]'))))$('#native-panel').outerHTML=nativePanel();
    const active=document.activeElement;
    const busy=active.closest?.('#connector-form') || (active.closest?.('#groups-form') && ['INPUT','TEXTAREA','SELECT'].includes(active.tagName));
    if(view==='connections'&&(!busy||force)&&signature!==connectorSignature){
      const panel=$('#napcat-panel');
      if(panel){
        panel.outerHTML=napcatPanel();
        document.querySelectorAll('#groups-form input[name=group]').forEach(input=>{if(draft.has(input.value))input.checked=draft.get(input.value);});
      }
      connectorSignature=signature;
    }else if(view==='connections'&&$('#connector-message'))$('#connector-message').textContent=c.message;
  }catch(error){if(view==='connections'&&$('#connector-message'))$('#connector-message').textContent='连接状态读取失败：'+error.message;}
}
function settingsView(){
 const tabs=`<div class="tabs page-tabs">${[['model','模型'],['interests','关注词'],['notifications','提醒'],['about','关于']].map(([key,title])=>`<button data-settings-tab="${key}" class="${settingsTab===key?'active':''}" aria-pressed="${settingsTab===key}">${title}</button>`).join('')}</div>`;
 if(settingsTab==='model')return tabs+modelPanel();
 if(settingsTab==='interests')return tabs+`<section class="panel"><h2>关注词</h2><p>优先展示与这些词相关的有效通知、待办和资料；闲聊不会仅因命中关键词进入列表。</p><form id="settings-form" class="form-grid"><label>关键词<textarea name="interests" placeholder="用逗号或换行分隔">${esc(state.settings.interests)}</textarea><small>保存后更新本地规则结果；已有 AI 结果和待办保留。</small></label><button class="primary" type="submit">保存关注词</button></form></section>`;
 if(settingsTab==='notifications')return tabs+`<section class="panel"><h2>到期提醒</h2><p>桌面应用运行时会发送到期提醒，关闭窗口后仍在后台运行。请在系统设置中允许 QQmanager 通知。</p><p class="help">提醒时间按北京时间显示。休眠或关机期间无法即时通知。</p><details><summary>浏览器与兼容提醒</summary><div class="form-grid"><button data-action="enable-browser">开启浏览器通知</button><small>浏览器授权状态：${'Notification' in window?esc(Notification.permission):'不支持'}</small><label class="checkbox-label"><input type="checkbox" id="native-notifications" ${state.settings.native_notifications?'checked':''}>兼容旧服务的 macOS 通知</label><small>桌面版已有内置提醒，无需重复开启。</small>${state.settings.notification_error?`<p class="warning">${esc(state.settings.notification_error)}</p>`:''}</div></details></section>`;
 return tabs+`<section class="panel"><h2>QQmanager</h2><p>原文和资料保存在本机。启用模型后，向你配置的服务发送所选群的新消息、附件提取文字、有限上下文和关注词。</p><p>分类和摘要仅作辅助；提醒时间需要确认，变更消息不会自动覆盖待办。</p><details><summary>数据与功能边界</summary><p>密钥保存在本机数据库，尚未使用系统凭据保险库。可从应用菜单打开数据目录。已支持所选群图片 OCR 与文件正文提取，单附件最多 20 MB；原图和文件保存在本机。语音转写、语义搜索尚未支持。</p></details></section>`;
}

function modelStatus(){
  const m=state.llm||{}, c=m.counts||{};
  const phase=!m.configured?'等待配置模型':m.enabled?'自动分析已开启':!m.tested?'等待连接测试':'自动分析已暂停';
  return `<div class="section-title"><h2>${phase}</h2></div><p>已分析 ${c.done||0} 条 · 排队 ${c.pending||0} 条 · 分析中 ${c.running||0} 条 · 过滤 ${c.filtered||0} 条 · 失败 ${c.failed||0} 条${c.skipped?' · 已取消群跳过 '+c.skipped+' 条':''}</p><small>过去 24 小时已调用 ${m.calls_today||0} / ${m.daily_limit||300} 次${m.model?' · '+esc(m.model):''}。${m.calls_today>=m.daily_limit?'已达上限，剩余消息保留排队。':'规则识别和原文保存持续运行。'}</small>${m.error?`<p class="warning">${esc(m.error)}</p>`:''}`;
}
function modelPanel(){
  const m=state.llm||{};
  return `<section class="panel"><div class="section-title"><h2>模型分析</h2><span class="badge">通知 · 待办 · 资料</span></div><p>启用后自动分析所选群的新消息，保留逐字原文依据。发送内容包括新消息、附件提取文字、最多 7 条同群近期上下文和关注词；同群事项标题用于归并，主动导入的课表/校历文字用于生成预览；原始附件在本机处理，不发送未选群消息。调用费用由所选服务计收。</p><div id="model-status" class="model-status">${modelStatus()}</div><details ${m.configured?'':'open'}><summary>${m.configured?'修改模型配置':'配置模型'}</summary><form id="model-form" class="form-grid"><label>快速填写<select id="model-preset"><option value="">选择服务，或手动填写</option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="local">本机兼容接口</option></select></label><label>接口基础地址<input name="url" type="url" required value="${esc(m.url)}" placeholder="https://api.deepseek.com"><small>填写基础地址，不包含 /chat/completions。服务需支持 Chat Completions 与 JSON 输出。</small></label><label>模型名称<input name="model" required value="${esc(m.model)}" placeholder="填写服务提供的模型 ID"></label><label>API Key<input name="key" type="password" autocomplete="new-password" placeholder="${m.has_key?'已保存，留空则保持原密钥':'仅保存在本机，不会回显'}"><small>请直接在这里填写；使用免密本机服务可留空。更换服务地址时需重新填写密钥。</small></label><label class="checkbox-label"><input name="clear_key" type="checkbox">清除已保存的密钥（例如切换本机模型）</label><label>过去 24 小时调用上限<input name="daily_limit" type="number" min="1" max="5000" value="${m.daily_limit||300}" required><small>新消息分析、事项归并、课表导入和测试均计入调用上限；消息失败最多尝试 3 次。这是次数限制，不是金额预算。</small></label><div class="inline-actions"><button type="submit" class="primary">保存、测试并启用</button><button type="button" data-action="model-pause">暂停 AI 分析</button><button type="button" data-action="model-retry">重试失败消息</button><button type="button" data-action="model-review">按新规则整理已有消息</button></div><p id="model-test-result" role="status">${esc(m.test_error||'测试只发送内置的虚构通知，成功后开始分析排队的新消息。')}</p></form></details><p class="help">队列从本功能安装后接收的新消息开始；暂停期间继续排队，恢复后处理。取消勾选群后，尚未发送的消息会跳过。密钥保存在本机数据库，请勿分享 data 目录。</p></section>`;
}
function evidenceHTML(item){
  let refs=[];try{refs=JSON.parse(item.evidence||'[]');}catch(_){}
  if(!refs.length)return '';
  return `<details class="evidence-details"><summary>AI 引用依据 · ${refs.length} 条</summary>${refs.map(r=>`<div class="evidence"><small>消息 #${esc(r.message_id)} · ${esc(r.sender)} · ${fmt(r.sent_at)}</small><blockquote>${esc(r.quote)}</blockquote><details><summary>查看对应消息片段</summary><div class="source-text">${esc(r.content)}</div></details></div>`).join('')}<p class="help">模型：${esc(item.model)}。原文匹配已核验；分类和摘要仍需结合上下文判断。</p></details>`;
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
function taskDialog(insight=null, task=null){
  openModal(task?'编辑待办':'确认并添加待办',`<form id="task-form" class="form-grid" data-task-id="${task?.id||''}" data-insight-id="${insight?.id||''}"><label>待办内容<textarea name="title" required maxlength="300">${esc(task?.title||insight?.title||'')}</textarea></label><label>提醒时间（北京时间）<input type="datetime-local" name="due" value="${datetimeInput(task?.due||insight?.due)}"><small>${esc(insight?.date_note||'不设置时间也可以保存为待办；设置后将在该时间提醒。')}</small></label><div id="task-error" class="form-error"></div><div class="modal-actions"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">${task?'保存修改':'确认添加'}</button></div></form>`);
}
function attachmentsHTML(items=[]){
 return items.map(a=>`<section class="attachment"><div class="section-title"><strong>${esc(a.name)}</strong><small>${bytes(a.size||0)}</small></div>${a.file_id?`${a.kind==='image'?`<a href="/api/preview/${a.file_id}" target="_blank" rel="noopener"><img class="attachment-image" src="/api/preview/${a.file_id}" alt="${esc(a.name)}" loading="lazy"></a>`:''}<button data-action="file-detail" data-id="${a.file_id}">${a.kind==='image'?'查看识别文字':'查看文件正文'}</button> <a href="/api/download/${a.file_id}" download>下载原文件</a><p class="help">${esc(a.parse_status||'正在解析')}</p>${a.status==='error'?`<button data-action="attachment-retry" data-id="${a.id}">重试文字识别</button>`:''}`:`<p class="help">${a.status==='unavailable'?'QQ 暂未取得附件。请在 QQ 下载后重试。':'尚未取得附件内容，需要插件 0.1.5。旧记录可尝试重新获取。'}</p><button data-action="media-refresh">重新获取附件</button>`}</section>`).join('');
}
function relatedAttachments(i){
 const mid=i.message_id||i.id,t=Date.parse(i.sent_at);
 return state.messages.filter(m=>m.id!==mid&&m.sender===i.sender&&m.live_account===i.live_account&&m.live_group===i.live_group&&m.group_name===i.group_name&&Math.abs(Date.parse(m.sent_at)-t)<=600000&&m.attachments?.length).slice(0,4);
}
function sourceDialog(i){
 const insight=!!i.kind,hasTask=state.tasks.some(t=>(i.merged_ids||[i.id]).includes(t.insight_id));
 openModal(insight?'信息详情':'原始消息',`<div class="detail-meta">${insight?badge(i.kind):''}<span>${esc(i.group_name)}</span><time>${fmt(i.sent_at)}</time></div>${insight?`<h2 class="detail-title">${esc(i.title)}</h2>`:''}<section class="source-section"><h3>原文 <span>${esc(i.sender||'')}</span></h3><div class="source-text">${esc(i.content)}</div></section>${attachmentsHTML(i.attachments)}${relatedAttachments(i).map(m=>`<details class="related-media" open><summary>同发送者相邻附件 · ${fmt(m.sent_at)}（请核对是否属于本通知）</summary>${attachmentsHTML(m.attachments)}</details>`).join('')}${evidenceHTML(i)}${(i.merged_sources||[]).length?`<details><summary>重复通知原文 · ${i.merged_sources.length} 条</summary>${i.merged_sources.map(r=>`<div class="evidence"><small>${esc(r.sender)} · ${fmt(r.sent_at)}</small><div class="source-text">${esc(r.content)}</div></div>`).join('')}</details>`:''}${i.analysis_reason?`<p class="help">${esc(i.analysis_reason)}</p>`:''}${insight?`<details class="detail-explanation"><summary>识别详情</summary><p>${esc(i.reason||'')}</p>${i.date_note?`<p>${esc(i.date_note)}</p>`:''}<small>来源：${esc(i.source||'关联的原始消息')}</small></details><div class="detail-actions"><button class="primary" data-action="task-from" data-id="${i.id}" ${hasTask?'disabled':''}>${hasTask?'已设待办':'添加待办'}</button><button data-action="save" data-id="${i.id}">${i.status==='saved'?'取消收藏':'收藏'}</button><button data-action="read" data-id="${i.id}">已读</button><button data-action="dismiss" data-id="${i.id}">${i.status==='dismissed'?'恢复':'忽略'}</button></div>`:''}${i.analysis_error?`<p class="warning">${esc(i.analysis_error)}</p>`:''}`,true);
}

async function handleAction(action,id){
  const i=state.insights.find(x=>x.id===id),t=state.tasks.find(x=>x.id===id),f=state.files.find(x=>x.id===id);
  switch(action){
    case 'close-modal':modal.close();return;
    case 'import':importDialog();return;
    case 'import-files':importDialog('file');return;
    case 'source':sourceDialog(i);return;
    case 'message-detail':sourceDialog(state.messages.find(x=>x.id===id));return;
    case 'task-source':sourceDialog(t);return;
    case 'new-task':taskDialog();return;
    case 'task-from':taskDialog(i);return;
    case 'task-edit':taskDialog(null,t);return;
    case 'task-toggle':await api('/api/tasks/'+id,{status:t.status==='done'?'open':'done'});toast(t.status==='done'?'待办已重新打开':'待办已完成');break;
    case 'snooze':await api('/api/tasks/'+id,{snooze:true});toast('将在 30 分钟后再次提醒');break;
    case 'save':await api('/api/insight',{id,status:i.status==='saved'?'new':'saved'});await refresh();if(modal.open)sourceDialog(state.insights.find(x=>x.id===id));return;
    case 'read':await api('/api/insight',{id,status:'read'});modal.close();break;
    case 'dismiss':await api('/api/insight',{id,status:i.status==='dismissed'?'new':'dismissed'});modal.close();break;
    case 'file-detail':{const file=await api('/api/file/'+id);openModal(file.name,`${currentMatter?.attachments.some(a=>a.file_id===id)?`<button data-plan="matter" data-id="${currentMatter.id}">‹ 返回事项</button>`:''}<p class="help">${esc(file.parse_status)} · ${bytes(file.size)}</p>${file.kind==='image'?`<a href="/api/preview/${id}" target="_blank" rel="noopener"><img class="attachment-image" src="/api/preview/${id}" alt="${esc(file.name)}"></a>`:''}<div class="source-text">${esc(file.body?.slice(0,100000)||'暂未提取到文字。请查看原图或下载原文件。')}</div>${file.body?.length>100000?'<p class="help">仅预览前 10 万字；搜索覆盖提取全文。</p>':''}<div class="modal-actions">${/\.pdf$/i.test(file.name)?`<a href="/api/preview/${id}" target="_blank" rel="noopener">打开 PDF</a>`:''}<a class="primary" href="/api/download/${id}" download>下载原文件</a></div>`,true);return;}
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
    case 'native-save-groups':{const d=ensureNativeDraft();await api('/api/native/groups',{groups:[...d.selected],names:Object.fromEntries([...d.names].filter(([g])=>d.selected.has(g)))});nativeDraft=null;await refresh(false);await refreshConnector(true);render();toast(state.settings.bridge_groups.length?'群聊选择已保存，约 10 秒内开始按新范围收集':'已停止收集所有群；原始记录和分析结果仍保留');return;}
    case 'native-toggle':await api('/api/native/settings',{enabled:!nativeState?.enabled});await refreshConnector();return;
    case 'model-pause':await api('/api/llm/pause',{});toast('AI 分析已暂停；新消息继续入库并排队');break;
    case 'model-review':{const result=await api('/api/llm/review',{});toast(`已整理本地规则，${result.queued} 条已选群消息进入重新分析队列；按现有模型调用上限处理`);break;}
    case 'model-retry':await api('/api/llm/retry',{});toast('失败消息已重新排队；需启用模型才会处理');break;
    case 'demo':await importDemo();break;
  }
  await refresh();
}
function showNotifications(){openModal('到期提醒',state.notifications.length?state.notifications.map(n=>`<div class="notification-item"><p>${esc(n.title)}<small>${fmt(n.created)}</small></p><button data-action="notification-read" data-id="${n.id}">知道了</button></div>`).join(''):'<p>暂时没有未读提醒。你可以在待办中设置提醒时间。</p>');}
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
  if(button.dataset.removeGroup){ensureNativeDraft();nativeDraft.selected.delete(button.dataset.removeGroup);nativeDraft.dirty=true;renderNativeGroups();return;}
  if(button.dataset.view){if(button.id==='analysis-status'){settingsTab='model';if(view==='settings')render();}event.preventDefault();setView(button.dataset.view);return;}
  if(button.dataset.themeChoice){applyTheme(button.dataset.themeChoice);return;}
  if(button.dataset.settingsTab){settingsTab=button.dataset.settingsTab;render();return;}
  if(button.dataset.connectionTab){connectionTab=button.dataset.connectionTab;render();return;}
  if(button.dataset.topic){topicFilter=button.dataset.topic;render();return;}
  if(button.dataset.taskFilter){taskFilter=button.dataset.taskFilter;render();return;}
  if(button.dataset.action){try{await handleAction(button.dataset.action,Number(button.dataset.id));}catch(error){toast(error.message);}}
});
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target, data=new FormData(form),submit=form.querySelector('[type=submit]');
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
      const due=data.get('due');const body={title:data.get('title'),due:due?due+':00+08:00':null};
      if(form.dataset.insightId)body.insight_id=Number(form.dataset.insightId);
      await api('/api/tasks'+(form.dataset.taskId?'/'+form.dataset.taskId:''),body);modal.close();await refresh();toast('待办已保存');
    }else if(form.id==='watch-form'){
      await api('/api/watches',Object.fromEntries(data));await refresh();toast('监听已添加，将在 10 秒内扫描');
    }else if(form.id==='bridge-form'){
      const groups=data.get('groups').split(/[,，\s]+/).filter(Boolean);if(data.get('enabled')&&!groups.length)throw Error('请先填写至少一个接入群号');
      await api('/api/settings',{bridge_enabled:!!data.get('enabled'),bridge_groups:groups});await refresh();toast('接入配置已保存');
    }else if(form.id==='connector-form'){
      await api('/api/connector/settings',{url:data.get('url'),token:data.get('token'),enabled:!!data.get('enabled')});
      toast('连接设置已保存');await refreshConnector(true);
    }else if(form.id==='groups-form'){
      await api('/api/connector/groups',{groups:data.getAll('group')});
      toast('群选择已保存；之后只收集所选群的新消息');await refresh();await refreshConnector(true);
    }else if(form.id==='native-groups-form'){
      await api('/api/native/pair',{account:data.get('account').trim(),groups:[...ensureNativeDraft().selected]});nativeDraft=null;await refresh();await refreshConnector(true);toast('绑定账号已保存');
    }else if(form.id==='model-form'){
      $('#model-test-result').textContent='正在保存配置并用虚构通知测试，最长约 45 秒…';
      await api('/api/llm/config',{url:data.get('url'),model:data.get('model'),key:data.get('key'),clear_key:!!data.get('clear_key'),daily_limit:Number(data.get('daily_limit'))});
      form.querySelector('[name=key]').value='';
      const result=await api('/api/llm/test',{enable:true});
      await refresh();toast(result.message);
    }else if(form.id==='settings-form'){
      await api('/api/settings',{interests:data.get('interests')});await refresh();toast('关注词已保存，历史消息已重新分析');
    }
  }catch(error){if(form.id==='task-form')$('#task-error').textContent=error.message;else if(form.id==='model-form'){if($('#model-test-result'))$('#model-test-result').textContent='未启用：'+error.message;await refresh(false);}else toast(error.message);}
  finally{if(submit)submit.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.id==='native-group-search'){nativeGroupSearch=event.target.value;renderNativeGroups();}});
document.addEventListener('change',async event=>{
if(event.target.dataset.nativeGroup){ensureNativeDraft();const id=event.target.dataset.nativeGroup;if(event.target.checked)nativeDraft.selected.add(id);else nativeDraft.selected.delete(id);nativeDraft.dirty=true;renderNativeGroups();return;}
if(event.target.id==='read-filter'){category=event.target.value;render();return;}
if(event.target.id==='sort-order'){sortOrder=event.target.value;render();return;}
if(event.target.id==='model-preset'){
  const presets={deepseek:['https://api.deepseek.com','deepseek-flash'],openai:['https://api.openai.com/v1',''],local:['http://127.0.0.1:11434/v1','']};
  const p=presets[event.target.value];if(p){const f=$('#model-form');f.elements.url.value=p[0];f.elements.model.value=p[1];}
}
if(event.target.id==='native-notifications'){try{await api('/api/settings',{native_notifications:event.target.checked});toast('系统通知设置已保存');}catch(error){event.target.checked=!event.target.checked;toast(error.message);}}});
$('#import-button').addEventListener('click',()=>importDialog(view==='files'?'file':'chat'));
$('#new-task-button').addEventListener('click',()=>taskDialog());
$('#notifications-button').addEventListener('click',showNotifications);
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
window.addEventListener('hashchange',()=>setView(canonicalView(location.hash.slice(1))));
window.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){if(!$('#searchbar').hidden){e.preventDefault();$('#search').focus();}}});
async function boot(){try{csrf=(await api('/api/session')).csrf;view=canonicalView(location.hash.slice(1));await refresh();await refreshConnector(true);setInterval(()=>{if(groupSelecting)return;refreshConnector();const busy=modal.open||['connections','settings'].includes(view)||$('#content').contains(document.activeElement)||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName);refresh(!busy);},5000);}catch(error){notice(error.message);}}
boot();
