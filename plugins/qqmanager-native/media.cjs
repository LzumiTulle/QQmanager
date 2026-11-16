'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {normalizeMessage}=require('./core.cjs');
const LIMIT=20*1024*1024;
const fingerprint=(account,group,id)=>crypto.createHash('sha256').update('{"onebot": ['+[account,group,id].map(x=>JSON.stringify(String(x))).join(', ')+']}').digest('hex');
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
 return {ready,remember,delivered,recover};
}
module.exports={createMedia,fingerprint};
