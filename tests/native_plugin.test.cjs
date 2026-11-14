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
