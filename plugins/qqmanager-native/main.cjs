'use strict';
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {normalizeMessage,observerExports,mergeGroups}=require('./core.cjs');
const {createMedia}=require('./media.cjs');
let active=false;
function start(configPath) {
  if(active)return;active=true;
  const config=JSON.parse(fs.readFileSync(configPath,'utf8'));
  const url=new URL(config.url);
  if(url.protocol!=='http:' || url.hostname!=='127.0.0.1' || url.pathname!=='/' || url.username || url.password || url.search || url.hash)throw Error('Local endpoint required');
  if(typeof config.token!=='string'||config.token.length<32)throw Error('Local pairing required');
  const directory=path.dirname(configPath),outboxPath=path.join(directory,'outbox.json');
  const startedAt=Date.now()/1000,diagnostics={started_at:startedAt,platform:process.platform,architecture:process.arch,version:'0.1.7',events:0,selected:0,dropped:0,signals:{}};
  const allowed=new Set(config.groups||[]);let account=String(config.account||''),enabled=false,queue=[],busy=false,lastError='',lastConfig=0,identity='';
  const seen=new Set();
  const media=createMedia({directory,authorized:(who,group)=>enabled&&String(who)===account&&allowed.has(String(group)),request,signal,enqueue:event=>{if(queue.length<2000)queue.push(event);}});
  let groupReader=null,groupIdentity=null,groupCatalog=null,groupRevision=0,lastGroupRequest=0,groupRefreshSeen=-1;
  const groupCallbacks={
    ready(getAccount,read){groupIdentity=getAccount;groupReader=read;},
    update(current,groups){
      if(current!==account)return;
      identity=current;
      const previous=groupCatalog?.account===current?groupCatalog.groups:[];
      groupCatalog={account:current,groups:mergeGroups(previous,groups),revision:++groupRevision};signal('group_list_received');
    }
  };
  try {const old=JSON.parse(fs.readFileSync(outboxPath,'utf8'));if(Array.isArray(old))queue=old.slice(-2000);}catch(_){}
  function signal(key){if(Object.keys(diagnostics.signals).length<40)diagnostics.signals[key]=(diagnostics.signals[key]||0)+1;}
  function observe(current,rows,event){
    diagnostics.events++;
    identity=current;
    if(!enabled || current!==account || !Array.isArray(rows))return;
    for(const row of rows.slice(0,500)){
      // Update callbacks also include history and unfinished sends. Admit only completed self-sends.
      if(event==='onMsgInfoListUpdate' && (String(row?.senderUin)!==current || Number(row?.sendStatus)!==2))continue;
      const message=normalizeMessage(row,current,allowed,startedAt);
      if(!message)continue;
      const key=[current,message.group_id,message.message_id].join(':');
      if(seen.has(key))continue;
      if(queue.length>=2000){diagnostics.dropped++;continue;}
      seen.add(key);if(seen.size>5000)seen.delete(seen.values().next().value);
      media.remember(row,message);queue.push(message);diagnostics.selected++;
    }
  }
  const wrapped=new WeakMap();
  function inspect(exports){
    if(!exports || (typeof exports!=='object'&&typeof exports!=='function'))return exports;
    if(wrapped.has(exports))return wrapped.get(exports);
    const result=observerExports(exports,observe,signal,groupCallbacks,media);wrapped.set(exports,result);wrapped.set(result,result);return result;
  }
  const originalDlopen=process.dlopen;
  process.dlopen=function(module,filename,...rest){
    const result=Reflect.apply(originalDlopen,this,[module,filename,...rest]);
    try{if(/(?:wrapper|major)\.node$/i.test(String(filename))){signal('native_module_loaded');module.exports=inspect(module.exports);}}catch(_){signal('native_inspect_error');}
    return result;
  };
  for(const item of Object.values(require.cache)){if(/wrapper\.node$/i.test(item.filename||''))item.exports=inspect(item.exports);}
  function request(route,body){
    return new Promise((resolve,reject)=>{
      const raw=Buffer.from(JSON.stringify(body));
      const req=http.request(new URL(route,url),{method:'POST',headers:{'Authorization':'Bearer '+config.token,'Content-Type':'application/json','Content-Length':raw.length},timeout:4000},res=>{
        let data='',size=0;res.on('data',chunk=>{size+=chunk.length;if(size>1024*1024)req.destroy(Error('response too large'));else data+=chunk;});
        res.on('end',()=>{try{if(res.statusCode!==200)throw Error('HTTP '+res.statusCode);resolve(JSON.parse(data));}catch(e){reject(e);}});
      });req.on('timeout',()=>req.destroy(Error('timeout')));req.on('error',reject);req.end(raw);
    });
  }
  async function flush(){
    if(busy)return;busy=true;
    try{
      await fs.promises.writeFile(outboxPath+'.tmp',JSON.stringify(queue),{mode:0o600});
      await fs.promises.rename(outboxPath+'.tmp',outboxPath);
      if(Date.now()-lastConfig>10000){
        const result=await request('/api/native/heartbeat',{...diagnostics,account:media.currentAccount()||groupIdentity?.()||identity||'',history_state:media.historyState(),history_reports:media.historyReports(),queued:queue.length,error:lastError,
          ...(groupCatalog?.account===account?{group_catalog:groupCatalog}:{})});
        account=String(result.account||'');enabled=!!result.enabled;allowed.clear();for(const g of result.groups||[])allowed.add(String(g));lastConfig=Date.now();
        media.catchUp(result).catch(()=>signal('history_retry'));
        media.recover(result).catch(()=>signal('attachment_recovery_failed'));
        if(groupCatalog&&groupCatalog.account!==account)groupCatalog=null;
        if(groupReader&&groupIdentity?.()===account&&Date.now()-lastGroupRequest>10000&&
           (!groupCatalog||groupRefreshSeen!==result.group_refresh)){
          lastGroupRequest=Date.now();groupRefreshSeen=result.group_refresh;
          groupCatalog=null; // A requested full refresh starts a new batch collection.
          // Only the read-only list API. Never send messages, join or leave groups.
          try{Promise.resolve(groupReader()).catch(()=>signal('group_list_request_failed'));}catch(_){signal('group_list_request_failed');}
        }
      }
      // Remove cancelled groups/accounts before persisting or transmitting.
      queue=queue.filter(x=>x.self_id===account&&allowed.has(x.group_id));
      await fs.promises.writeFile(outboxPath+'.tmp',JSON.stringify(queue),{mode:0o600});
      await fs.promises.rename(outboxPath+'.tmp',outboxPath);
      if(enabled&&queue.length){
        const batch=[];let bytes=20;
        for(const event of queue.slice(0,20)){const size=Buffer.byteLength(JSON.stringify(event))+1;if(bytes+size>900000)break;batch.push(event);bytes+=size;}
        await request('/api/native/events',{events:batch});queue.splice(0,batch.length);media.delivered(batch);
      }
      lastError='';
    }catch(_){lastError='本地服务暂不可达，已排队重试';}
    finally{
      // Diagnostics deliberately contain no message text, group names, tokens or QQ IDs.
      try{await fs.promises.writeFile(path.join(directory,'status.json'),JSON.stringify({...diagnostics,queued:queue.length,enabled,identity_matches:!!identity&&identity===account,error:lastError}),{mode:0o600});}catch(_){}
      busy=false;
    }
  }
  const timer=setInterval(()=>{flush().catch(()=>{});},2000);timer.unref();flush().catch(()=>{});
  signal('observer_started');
}
module.exports.start=start;
// LiteLoader plugin entry, including Windows. Config stays outside the distributed ZIP.
if(typeof globalThis.LiteLoader!=='undefined'){
  const folder=globalThis.LiteLoader.plugins?.qqmanager_native?.path?.data;
  if(folder)try{start(path.join(folder,'config.json'));}catch(_){}
}
