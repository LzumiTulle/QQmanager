'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {normalizeMessage,numeric}=require('./core.cjs');
const LIMIT=20*1024*1024;
const fingerprint=(account,group,id)=>crypto.createHash('sha256').update('{"onebot": ['+[account,group,id].map(x=>JSON.stringify(String(x))).join(', ')+']}').digest('hex');

// This reader uses the already signed-in session. It never opens a chat or marks it read.
async function syncHistory(service,account,scope,active,request,received,report=()=>{}) {
 const group=scope.group_id,peer={chatType:2,peerUid:group,guildId:''};
 const groups=new Set([group]),history={group_id:group,grant_id:scope.grant_id};
 const floor=Math.max(scope.since,scope.cursor-120);
 const guard=()=>{if(!active())throw Error('history scope changed');};
 async function read(call){
  guard();
  let timer;
  try{
   const result=await Promise.race([call(),new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(Error('history timeout')),15000);
   })]);
   guard();
   const code=Number(result?.result??0);
   if(Number.isSafeInteger(code) && code!==0)throw Error('qq:'+code);
   if(!Array.isArray(result?.msgList) || code!==0)throw Error('invalid_page');
   return result.msgList;
  }finally{clearTimeout(timer);}
 }
 const pageSize=100;
 function validRow(row){
  return Number(row?.chatType)===2 && (numeric(row.peerUin)||numeric(row.peerUid))===group &&
      Number.isSafeInteger(Number(row.msgTime)) && /^-?\d+$/.test(String(row.msgId));
 }
 let page;
 for(const method of ['getAioFirstViewLatestMsgs','getLatestDbMsgs','getMsgs']){
  try{
   const rows=await read(()=>method==='getMsgs'?service.getMsgs(peer,'0',pageSize,true):service[method](peer,pageSize));
   if(!rows.every(validRow))throw Error('invalid_row');
   report(method,rows.length?'ok':'empty');
   page=rows;break;
  }catch(error){
   guard();
   const code=String(error.message);
   report(method,/^(?:qq:-?\d{1,12}|invalid_row|invalid_page|history timeout)$/.test(code)?code:'exception');
   if(method==='getMsgs')throw error;
  }
 }
 let newest=0,oldest=Infinity,previous=null;
 while(page.length){
  const rows=page.map(row=>{
   if(!validRow(row))
    throw Error('unexpected history row');
   return row;
  }).sort((a,b)=>Number(a.msgTime)-Number(b.msgTime) || (BigInt(a.msgSeq||a.msgId)<BigInt(b.msgSeq||b.msgId)?-1:1));
  const first=rows[0],boundary=String(first.msgId);
  if(boundary===previous)throw Error('history pagination stalled');
  oldest=Math.min(oldest,Number(first.msgTime));
  let batch=[],size=0;
  async function deliver(){
   if(!batch.length)return;
   guard();
   const reply=await request('/api/native/events',{history,events:batch.map(item=>item.event)});
   if(!reply?.ok || !Array.isArray(reply.new_ids))throw Error('history batch not acknowledged');
   guard();
   const added=new Set(reply.new_ids);
   for(const item of batch)if(added.has(item.event.message_id))received(item.row,item.event);
   batch=[];size=0;
  }
  for(const row of rows){
   if(String(row.msgId)===previous || Number(row.msgTime)<floor)continue;
   const event=normalizeMessage(row,account,groups,0);
   if(!event)throw Error('history message unavailable');
   const bytes=Buffer.byteLength(JSON.stringify(event))+1;
   if(batch.length===20 || size+bytes>900000)await deliver();
   batch.push({row,event});size+=bytes;newest=Math.max(newest,event.time);
  }
  await deliver();
  if(oldest<floor)break;
  previous=boundary;
  page=await read(()=>service.getMsgsIncludeSelf(peer,boundary,pageSize,false));
  // Some versions page forward with the default order. Require an older
  // sequence before accepting a full continuation page.
  const older=r=>BigInt(r.msgSeq||r.msgId)<BigInt(first.msgSeq||first.msgId);
  if(!page.some(older)){
   const atEnd=page.every(row=>String(row.msgId)===boundary);
   page=await read(()=>service.getMsgsIncludeSelf(peer,boundary,pageSize,true));
   if(!page.some(older)){
    if(!atEnd && page.some(row=>String(row.msgId)!==boundary))throw Error('history pagination unavailable');
    break;
   }
  }
 }
 if(scope.cursor && oldest>scope.cursor)throw Error('history gap');
 guard();
 const through=Math.min(newest,Math.floor(Date.now()/1000));
 const ack=await request('/api/native/events',{events:[],history:{...history,through}});
 if(!ack?.ok)throw Error('history checkpoint not acknowledged');
}

function createMedia({directory,authorized,request,enqueue,signal}) {
 let service,identity,working=false,recoverySeen=-1,recovering=false;
 const pending=new Map(),jobs=[],downloads=new Map(),attached=new WeakSet();
 const key=(account,id,element)=>[account,id,element].join(':');
 function ready(getIdentity,value){
  service=value;identity=getIdentity;
  if(attached.has(value))return;attached.add(value);
  value.addKernelMsgListener(new Proxy({onRichMediaDownloadComplete(result){
   const entry=downloads.get(key(identity(),result?.msgId,result?.msgElementId));
   if(entry)entry.resolve(result?.filePath||'');
  }},{get:(target,key)=>target[key]||(()=>{})}));
  signal('media_service_ready');
 }
 function remember(row,event){
  if(pending.size>=500)return;
  pending.set(key(event.self_id,event.message_id,''),{row,event});
 }
 async function safeRead(file){
  const real=await fs.realpath(file);
  const profile=await fs.realpath(directory);
  // Only plugin-owned downloads or QQ's own attachment cache, never arbitrary user paths.
  const inProfile=real.startsWith(profile+path.sep);
  const qqCache=real.startsWith(os.homedir()+path.sep)&&/(?:^|[\\/])(?:nt_qq|nt_data|Tencent Files)(?:[\\/])/.test(real);
  if(!inProfile&&!qqCache)throw Error('outside QQ media cache');
  const stat=await fs.stat(real);if(!stat.isFile()||stat.size<1||stat.size>LIMIT)throw Error('media size');
  const data=await fs.readFile(real);if(data.length>LIMIT)throw Error('media size');return data;
 }
 async function obtain(row,element,event){
  const part=element.picElement||element.fileElement;
  const existing=part?.sourcePath||part?.filePath;
  if(existing)try{return await safeRead(existing);}catch{}
  if(!service||identity()!==String(event.self_id)||!element.elementId)throw Error('media service unavailable');
  const folder=path.join(directory,'downloads');await fs.mkdir(folder,{recursive:true});
  const target=path.join(folder,crypto.createHash('sha256').update(key(event.self_id,event.message_id,element.elementId)).digest('hex')+(element.picElement?'.jpg':'.bin'));
  let timer;
  const id=key(event.self_id,event.message_id,element.elementId);
  try{
   const file=await new Promise((resolve,reject)=>{
    downloads.set(id,{resolve});timer=setTimeout(()=>reject(Error('download timeout')),45000);
    Promise.resolve(service.downloadRichMedia({fileModelId:'0',downSourceType:0,downloadSourceType:0,triggerType:1,msgId:String(row.msgId),chatType:2,peerUid:String(row.peerUid||event.group_id),elementId:String(element.elementId),thumbSize:0,downloadType:1,filePath:target})).catch(reject);
   });
   return await safeRead(file||target);
  }finally{clearTimeout(timer);downloads.delete(id);}
 }
 async function run(){
  if(working)return;working=true;
  try{while(jobs.length){
   const {row,event}=jobs.shift();if(!authorized(event.self_id,event.group_id))continue;
   for(const element of row.elements||[]){
    if((!element.picElement&&!element.fileElement)||!element.elementId)continue;
    if(!authorized(event.self_id,event.group_id))break;
    const payload={account:String(event.self_id),group:String(event.group_id),message_id:String(event.message_id),element_id:String(element.elementId||'')};
    try{
     if(Number((element.picElement||element.fileElement).fileSize)>LIMIT)throw Error('media size');
     const data=await obtain(row,element,event);
     if(!authorized(event.self_id,event.group_id))break;
     await request('/api/native/attachment',{...payload,data:data.toString('base64')});signal('attachment_saved');
    }catch(_){signal('attachment_failed');if(authorized(event.self_id,event.group_id))try{await request('/api/native/attachment',{...payload,unavailable:true});}catch{}}
   }
  }}finally{working=false;}
 }
 function delivered(events){
  for(const event of events){const id=key(event.self_id,event.message_id,'');const data=pending.get(id);pending.delete(id);if(data&&jobs.length<200)jobs.push(data);}
  run().catch(()=>signal('media_worker_failed'));
 }
 async function recover(config){
  if(recovering||!service||!config.enabled||identity()!==config.account||recoverySeen===config.media_refresh)return;
  recovering=true;recoverySeen=config.media_refresh;
  try{
   const targets=new Map();for(const t of config.media_targets||[]){if(!config.groups.includes(t.group))continue;if(!targets.has(t.group))targets.set(t.group,new Set());targets.get(t.group).add(t.fingerprint);}
   for(const [group,wanted] of targets){
    if(!authorized(config.account,group))continue;
    // Bounded repair: only re-deliver IDs whose fingerprints already exist in the local database.
    let timer;
    let result;
    try{result=await Promise.race([service.getAioFirstViewLatestMsgs({chatType:2,peerUid:group},100),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('history timeout')),30000);})]);}finally{clearTimeout(timer);}
    for(const row of result?.msgList||[]){
     if(!wanted.has(fingerprint(config.account,group,row.msgId))||!authorized(config.account,group))continue;
     const event=normalizeMessage(row,config.account,new Set([group]),0);if(!event)continue;
     event.recovery=true;remember(row,event);enqueue(event);signal('attachment_recovery_matched');
    }
   }
  }catch(_){signal('attachment_recovery_failed');}finally{recovering=false;}
 }
 let checking=false,historyState='waiting',grants=new Map();
 const lastCheck=new Map();
 const historyReports=new Map();
 async function catchUp(config){
  grants=new Map((config.history||[]).map(scope=>[scope.group_id,scope.grant_id]));
  for(const id of historyReports.keys())if(![...grants.values()].includes(id))historyReports.delete(id);
  if(checking)return;
  if(!service || !config.enabled || !config.account || identity()!==config.account || !grants.size){
   historyState='waiting';lastCheck.clear();return;
  }
  const session=service,account=config.account;
  const due=config.history.filter(scope=>(lastCheck.get(scope.grant_id)||0)<Date.now()-60000);
  if(!due.length)return;
  checking=true;historyState='syncing';
  let failed=false;
  try{
   for(const scope of due){
    const active=()=>service===session && identity()===account && authorized(account,scope.group_id) &&
      grants.get(scope.group_id)===scope.grant_id;
    if(!active())continue;
    const details={group_id:scope.group_id};
    try{
     await syncHistory(session,account,scope,active,request,(row,event)=>{
      remember(row,event);delivered([event]);
     },(method,outcome)=>{details[method]=outcome;});
     details.scan='checked';
    }catch(error){
     const reason=String(error.message);
     details.scan=['history scope changed','history gap','unexpected history row','history pagination stalled',
      'history message unavailable','history pagination unavailable','history timeout'].includes(reason)?reason:'read_or_save_failed';
     failed=true;signal('history_retry');
    }
    historyReports.set(scope.grant_id,details);
    lastCheck.set(scope.grant_id,Date.now());
   }
  }finally{checking=false;historyState=failed?'retry':'checked';}
 }
 return {ready,remember,delivered,recover,catchUp,currentAccount:()=>identity?.(),historyState:()=>historyState,historyReports:()=>[...historyReports.values()]};
}
module.exports={createMedia,fingerprint,syncHistory};
