'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {normalizeMessage,installObserver,observerExports}=require('../plugins/qqmanager-native/core.cjs');
const now=Math.floor(Date.now()/1000);
function message(){return {chatType:2,peerUin:'123456',msgId:'9007199254740993999',msgTime:now,senderUin:'777777',sendNickName:'群成员',peerName:'群',elements:[{textElement:{content:'明天18:00提交报告'}},{fileElement:{fileName:'材料.pdf',filePath:'/private/should-not-copy'}}]};}
test('only chosen groups, numeric account, new group messages; never copies file paths',()=>{
 const row=message(),out=normalizeMessage(row,'777777',new Set(['123456']),now);
 assert.equal(out.message_id,row.msgId);assert.equal(out.message[1].data.name,'材料.pdf');assert(!JSON.stringify(out).includes('/private/'));
 assert.equal(normalizeMessage({...row,chatType:1},'777777',new Set(['123456']),now),null);
 assert.equal(normalizeMessage(row,'',new Set(['123456']),now),null);
 assert.equal(normalizeMessage(row,'777777',new Set(['999999']),now),null);
 assert.equal(normalizeMessage({...row,msgTime:now-60},'777777',new Set(['123456']),now),null);
});
test('wrapper observer preserves original calls, receiver, arguments and results',()=>{
 let callback;const service={addKernelMsgListener(cb){callback=cb;return 42;}};
 const session={init(config){assert.equal(this,session);return config;},getMsgService(){return service;}};
 const native={NodeIQQNTWrapperSession:{create(){return session;}}};
 const events=[],signals=[];
 assert(installObserver(native,(...args)=>events.push(args),s=>signals.push(s)));
 assert.equal(native.NodeIQQNTWrapperSession.create(),session);
 const config={selfUin:'777777'};assert.equal(session.init(config),config);
 const original={onRecvMsg(rows){assert.equal(this,original);return rows;}};
 assert.equal(session.getMsgService().addKernelMsgListener(original),42);
 const rows=[message()];assert.equal(callback.onRecvMsg(rows),rows);
 assert.equal(events[0][0],'777777');assert.equal(events[0][1],rows);assert(signals.includes('callback_hooked:onRecvMsg'));
});
test('observer faults do not break QQ; original exceptions remain unchanged',()=>{
 let callback;const service={addKernelMsgListener(cb){callback=cb;}};
 const session={init(){},getMsgService(){return service;}};
 const native={NodeIQQNTWrapperSession:{create(){return session;}}};
 installObserver(native,()=>{throw Error('plugin failed');});native.NodeIQQNTWrapperSession.create();session.init({selfUin:'777777'});
 session.getMsgService().addKernelMsgListener({onRecvMsg(){return 'QQ works';}});assert.equal(callback.onRecvMsg([]),'QQ works');
 const error=Error('QQ original');session.getMsgService().addKernelMsgListener({onRecvMsg(){throw error;}});
 assert.throws(()=>callback.onRecvMsg([]),e=>e===error);
});
test('read-only native methods fail without replacing or calling other APIs',()=>{
 const create=()=>({});const cls=Object.freeze({create});const native={NodeIQQNTWrapperSession:cls};
 assert.equal(installObserver(native,()=>{}),false);assert.equal(cls.create,create);
});
test('account changes observed from same session; frozen diagnostics never throw',()=>{
 let cb;const session={init(){return true;},getMsgService(){return {addKernelMsgListener(x){cb=x;}};}};
 const native={NodeIQQNTWrapperSession:{getNTWrapperSession(){return session;}}},seen=[];
 installObserver(native,a=>seen.push(a));native.NodeIQQNTWrapperSession.getNTWrapperSession('nt_1');session.init({selfUin:'111111'});
 session.getMsgService().addKernelMsgListener({onRecvMsg(){}});cb.onRecvMsg([]);session.init({selfUin:'222222'});cb.onRecvMsg([]);
 assert.deepEqual(seen,['111111','222222']);
});
test('runtime pairs locally, forwards selected messages and persists retry outbox', async()=>{
 const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path'),http=require('node:http');
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'qqm-native-'));
 const received=[];
 const server=http.createServer((req,res)=>{
   let body='';req.on('data',x=>body+=x);req.on('end',()=>{
     assert.equal(req.headers.authorization,'Bearer '+'x'.repeat(40));
     const obj=JSON.parse(body);
     res.setHeader('Content-Type','application/json');
     if(req.url==='/api/native/heartbeat')res.end(JSON.stringify({enabled:true,account:'777777',groups:['123456']}));
     else {received.push(...obj.events);res.end('{"ok":true}');}
   });
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 await fs.writeFile(path.join(folder,'config.json'),JSON.stringify({url:`http://127.0.0.1:${server.address().port}/`,token:'x'.repeat(40),account:'777777',groups:['123456']}));
 const real=process.dlopen;const timers=[];const realInterval=global.setInterval;global.setInterval=(fn,ms)=>{const t=realInterval(fn,ms);timers.push(t);return t;};
 let callback;
 const service={addKernelMsgListener(cb){callback=cb;}},session={init(){},getMsgService(){return service;}};
 process.dlopen=(module)=>{module.exports={NodeIQQNTWrapperSession:{create(){return session;}}};return 'unchanged';};
 try{
   require('../plugins/qqmanager-native/main.cjs').start(path.join(folder,'config.json'));
   const module={exports:{}};assert.equal(process.dlopen(module,'wrapper.node'),'unchanged');
   const observed=module.exports.NodeIQQNTWrapperSession.create();observed.init({selfUin:'777777'});
   observed.getMsgService().addKernelMsgListener({onRecvMsg(){return 'normal QQ result';}});
   await new Promise(resolve=>setTimeout(resolve,100));
   assert.equal(callback.onRecvMsg([message(),{...message(),peerUin:'999999'}]),'normal QQ result');
   await new Promise(resolve=>setTimeout(resolve,2300));
   assert.equal(received.length,1);assert.equal(received[0].group_id,'123456');
   const report=JSON.parse(await fs.readFile(path.join(folder,'status.json'),'utf8'));
   assert.equal(report.identity_matches,true);assert.equal(report.selected,1);
   assert(!JSON.stringify(report).includes('777777'));assert(!JSON.stringify(report).includes('提交报告'));
 }finally{
   timers.forEach(clearInterval);global.setInterval=realInterval;process.dlopen=real;
   await new Promise(resolve=>server.close(resolve));await fs.rm(folder,{recursive:true,force:true});
 }
});
test('immutable native exports keep receivers, reflection, listener removal and errors',()=>{
 let registered,removed;const events=[];const failure=Error('native');
 const service=Object.freeze({addKernelMsgListener(cb){assert.equal(this,service);registered=cb;return 123;},removeKernelMsgListener(cb){removed=cb;}});
 const session=Object.freeze({init(x){assert.equal(this,session);return x;},getMsgService(){assert.equal(this,session);return service;},check(x){assert.equal(x,session);return true;},fail(){throw failure;}});
 const cls=Object.freeze({getNTWrapperSession(arg){assert.equal(arg,'actual-QQ-argument');assert.equal(this,cls);return session;}});
 const native=Object.freeze({NodeIQQNTWrapperSession:cls,untouched:42});
 const exposed=observerExports(native,(...args)=>events.push(args));
 assert.deepEqual(Object.keys(exposed),Object.keys(native));assert.equal(exposed.untouched,42);assert.equal(native.NodeIQQNTWrapperSession,cls);
 const s=exposed.NodeIQQNTWrapperSession.getNTWrapperSession('actual-QQ-argument');
 assert.equal(s,exposed.NodeIQQNTWrapperSession.getNTWrapperSession('actual-QQ-argument'));assert.equal(s.check(s),true);
 assert.deepEqual(Object.keys(s),Object.keys(session));assert.equal(s.init,s.init);assert.throws(()=>s.fail(),e=>e===failure);
 const input={selfUin:'777777'};assert.equal(s.init(input),input);
 const cb=Object.freeze({onRecvMsg(rows){assert.equal(this,cb);return rows;}});
 const msgService=s.getMsgService();assert.equal(msgService,s.getMsgService());assert.equal(msgService.addKernelMsgListener(cb),123);
 const rows=[message()];assert.equal(registered.onRecvMsg(rows),rows);assert.equal(events[0][0],'777777');assert.equal(events[0][1],rows);
 msgService.removeKernelMsgListener(cb);assert.equal(removed,registered);
 const failing=Object.freeze({onRecvMsg(){throw failure;}});msgService.addKernelMsgListener(failing);assert.throws(()=>registered.onRecvMsg([]),e=>e===failure);assert.equal(events.length,1);
});
test('group list observer preserves QQ callbacks and exposes only a read-only refresh',async()=>{
 const {normalizeGroups}=require('../plugins/qqmanager-native/core.cjs');
 let registered,removed,reader,identity;const updates=[],calls=[];
 const service=Object.freeze({addKernelGroupListener(cb){assert.equal(this,service);registered=cb;return 17;},removeKernelGroupListener(cb){removed=cb;},getGroupList(forced){assert.equal(this,service);calls.push(forced);return Promise.resolve({result:0});},sendMessage(){assert.fail('must never send messages');}});
 const session=Object.freeze({init(){},getGroupService(){return service;}});
 const native=Object.freeze({NodeIQQNTWrapperSession:Object.freeze({create(){return session;}})});
 const wrapped=observerExports(native,()=>{},()=>{},{update:(...args)=>updates.push(args),ready:(getAccount,read)=>{identity=getAccount;reader=read;}});
 const observed=wrapped.NodeIQQNTWrapperSession.create();observed.init({selfUin:'777777'});
 const callback=Object.freeze({onGroupListUpdate(...args){assert.equal(this,callback);return args;}});
 const groupService=observed.getGroupService();assert.equal(groupService.addKernelGroupListener(callback),17);assert.deepEqual(calls,[]);
 const rows=[{groupCode:'123456',groupName:'设计群',memberList:['private'],ownerUin:'private'}];
 assert.deepEqual(registered.onGroupListUpdate(0,rows),[0,rows]);
 assert.deepEqual(updates,[['777777',[{group_id:'123456',group_name:'设计群'}]]]);
 assert.equal(identity(),'777777');await reader();assert.deepEqual(calls,[true]);
 groupService.removeKernelGroupListener(callback);assert.equal(removed,registered);
 assert.equal(normalizeGroups([{unexpected:'shape'}]),null);assert.deepEqual(normalizeGroups([]),[]);
 const failure=Error('QQ callback');groupService.addKernelGroupListener({onGroupListUpdate(){throw failure;}});
 assert.throws(()=>registered.onGroupListUpdate(0,rows),e=>e===failure);assert.equal(updates.length,1);
});
test('partial group callbacks retain earlier batches and update names without duplicates',()=>{
 const {mergeGroups}=require('../plugins/qqmanager-native/core.cjs');
 const first=[{group_id:'123456',group_name:'设计群'},{group_id:'654321',group_name:'技术群'}];
 const second=mergeGroups(first,[{group_id:'654321',group_name:'技术资料群'}]);
 assert.deepEqual(second,[first[0],{group_id:'654321',group_name:'技术资料群'}]);
 assert.deepEqual(mergeGroups(second,[]),second);
 assert.deepEqual(mergeGroups([],second.slice(1)),second.slice(1));
});
test('attachment worker downloads only selected message media and repairs known fingerprints',async()=>{
 const {createMedia,fingerprint}=require('../plugins/qqmanager-native/media.cjs');
 const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'qqm-media-'));
 let listener,done;const uploaded=new Promise(resolve=>done=resolve),requests=[],recovered=[];
 const row={...message(),elements:[{elementId:'80001',picElement:{fileName:'考试.png',fileSize:3}}]};
 const media=createMedia({directory:dir,authorized:(a,g)=>a==='777777'&&g==='123456',signal:()=>{},enqueue:x=>recovered.push(x),request:async(url,body)=>{requests.push({url,body});done();}});
 const service={addKernelMsgListener(cb){listener=cb;},async downloadRichMedia(args){await fs.writeFile(args.filePath,Buffer.from('abc'));listener.onRichMediaDownloadComplete({msgId:row.msgId,msgElementId:'80001',filePath:args.filePath});},async getAioFirstViewLatestMsgs(){return {msgList:[row,{...row,msgId:'900000000'}]};}};
 try{
  media.ready(()=> '777777',service);
  const event=normalizeMessage(row,'777777',new Set(['123456']),now);
  media.remember(row,event);media.delivered([event]);await uploaded;
  assert.equal(requests[0].url,'/api/native/attachment');assert.equal(requests[0].body.data,'YWJj');assert(!JSON.stringify(requests).includes(dir));
  await media.recover({enabled:true,account:'777777',groups:['123456'],media_refresh:1,media_targets:[{group:'123456',fingerprint:fingerprint('777777','123456',row.msgId)}]});
  assert.equal(recovered.length,1);assert.equal(recovered[0].recovery,true);
 }finally{await fs.rm(dir,{recursive:true,force:true});}
});

test('reopen replays multiple offline pages, retries a lost acknowledgement, and includes equal-time IDs',async()=>{
 const {syncHistory}=require('../plugins/qqmanager-native/media.cjs');
 const rows=Array.from({length:235},(_,i)=>({...message(),msgId:String(i+1),msgSeq:String(i+1),msgTime:now-500+Math.floor(i/3)}));
 const scope={group_id:'123456',grant_id:'selected',since:rows[7].msgTime,cursor:0};
 const database=new Set([rows.at(-1).msgId]); // Live push arrived first.
 const delivered=new Set(),commits=[];
 const service={
  async getAioFirstViewLatestMsgs(){return {msgList:rows.slice(-35)};},
  async getMsgsIncludeSelf(peer,id,count){assert.equal(peer.peerUid,scope.group_id);return {msgList:rows.slice(Math.max(0,Number(id)-count),Number(id))};}
 };
 let drop=true;
 async function request(route,{events,history}){
  assert.equal(route,'/api/native/events');
  if(!events.length){commits.push(history.through);return {ok:true};}
  assert.ok(events.length<=20);
  const added=events.filter(x=>!database.has(x.message_id)).map(x=>x.message_id);
  added.forEach(id=>database.add(id));
  if(drop){drop=false;throw Error('lost response');}
  return {ok:true,new_ids:added};
 }
 const scan=()=>syncHistory(service,'777777',scope,()=>true,request,(_,e)=>delivered.add(e.message_id));
 await assert.rejects(scan(),/lost response/);
 assert.deepEqual(commits,[]);
 await scan();
 const expected=rows.filter(x=>x.msgTime>=scope.since);
 assert.deepEqual([...database].sort(),expected.map(x=>x.msgId).sort());
 assert.deepEqual(commits,[rows.at(-1).msgTime]);
 scope.cursor=commits[0];
 await scan();
 assert.equal(database.size,expected.length);
 assert.ok(!delivered.has(rows.at(-1).msgId));
});

test('authorization changes and missing offline history cannot advance a checkpoint',async()=>{
 const {syncHistory}=require('../plugins/qqmanager-native/media.cjs');
 const scope={group_id:'123456',grant_id:'selected',since:now-7200,cursor:now-3600};
 let enabled=true,posted=0;
 const service={async getAioFirstViewLatestMsgs(){enabled=false;return {msgList:[message()]};}};
 const request=async()=>{posted++;return {ok:true,new_ids:[]};};
 await assert.rejects(syncHistory(service,'777777',scope,()=>enabled,request,()=>{}),/scope changed/);
 assert.equal(posted,0);
 await assert.rejects(syncHistory({getAioFirstViewLatestMsgs:async()=>({msgList:[]})},
  '777777',scope,()=>true,request,()=>{}),/history gap/);
 assert.equal(posted,0);
});

test('a -2 first view needs a successful local history read to confirm an empty group',async()=>{
 const {syncHistory}=require('../plugins/qqmanager-native/media.cjs');
 const scope={group_id:'123456',grant_id:'allowed',since:now-3600,cursor:0};
 const service={getAioFirstViewLatestMsgs:async()=>({result:-2}),getLatestDbMsgs:async()=>({result:0,msgList:[]})};
 const reports=[],commits=[];
 const scan=()=>syncHistory(service,'777777',scope,()=>true,async(route,body)=>{
  commits.push(body.history.through);return {ok:true};
 },()=>{},(method,result)=>reports.push([method,result]));
 await scan();
 assert.deepEqual(commits,[0]);
 assert.deepEqual(reports,[['getAioFirstViewLatestMsgs','qq:-2'],['getLatestDbMsgs','empty']]);
 service.getLatestDbMsgs=async()=>({result:-2,msgList:[]});
 await assert.rejects(scan());
 assert.deepEqual(commits,[0]);
});

test('a shared activity card stores the visible title and omits its link and payload',()=>{
 const card={meta:{detail_1:{title:'学生活动',url:'https://example.invalid/private'}}};
 const row={...message(),elements:[{arkElement:{bytesData:JSON.stringify(card)}}]};
 assert.deepEqual(normalizeMessage(row,'777777',new Set(['123456']),now).message,
   [{type:'text',data:{text:'[分享卡片] 学生活动'}}]);
});
