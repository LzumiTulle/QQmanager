'use strict';
// No Electron dependency: the same normalizer and wrapper observer run on Mac/Windows.
const numeric = v => typeof v === 'string' && /^\d{5,20}$/.test(v) ? v : typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? String(v) : '';
function cardTitle(ark){
  if(typeof ark.bytesData!=='string'||ark.bytesData.length>131072)return '';
  try{
    const card=JSON.parse(ark.bytesData);
    const detail=card.meta?.detail_1||card.meta?.news;
    return [detail?.title,detail?.desc,card.prompt].find(value=>typeof value==='string'&&value.trim())?.trim().slice(0,250)||'';
  }catch{return '';}
}
function normalizeMessage(row, account, groups, startedAt) {
  if (!row || Number(row.chatType)!==2 || !numeric(account)) return null;
  const group=numeric(row.peerUin)||numeric(row.peerUid);
  if (!groups.has(group)) return null;
  const id=String(row.msgId||'');
  const timestamp=Number(row.msgTime);
  if (!/^-?\d{1,30}$/.test(id) || !Number.isFinite(timestamp) || timestamp < startedAt-5 || timestamp > Date.now()/1000+300) return null;
  if (!Array.isArray(row.elements)) return null;
  const segments=[];
  for (const e of row.elements.slice(0,200)) {
    if (e.textElement && typeof e.textElement.content==='string') segments.push({type:'text',data:{text:e.textElement.content.slice(0,20000)}});
    else if (e.fileElement || e.picElement) {
      const f=e.fileElement||e.picElement;
      segments.push({type:e.picElement?'image':'file',data:{name:String(f.fileName||(e.picElement?'图片.jpg':'未命名文件')).slice(0,250),element_id:String(e.elementId||'').slice(0,40),size:Math.max(0,Math.min(Number(f.fileSize)||0,2**40))}});
    }
    else if(e.arkElement) segments.push({type:'text',data:{text:('[分享卡片] '+cardTitle(e.arkElement)).trim()}});
    else if(e.faceElement || e.marketFaceElement) segments.push({type:'text',data:{text:'[表情]'}});
    else if(e.replyElement) segments.push({type:'text',data:{text:'[引用消息]'}});
    else if(e.pttElement) segments.push({type:'text',data:{text:'[语音：未解析]'}});
    else if(e.videoElement) segments.push({type:'text',data:{text:'[视频：未解析]'}});
    else segments.push({type:'text',data:{text:'[其他消息类型：未解析]'}});
  }
  if(!segments.length)return null;
  if(JSON.stringify(segments).length>50000)return null;
  return {post_type:'message',message_type:'group',self_id:account,group_id:group,message_id:id,time:timestamp,
    group_name:String(row.peerName||group).slice(0,100),sender:{nickname:String(row.sendMemberName||row.sendNickName||row.sendRemarkName||row.senderUin||'群成员').slice(0,150)},message:segments};
}
function installObserver(native, notify, diagnose=()=>{}) {
  if(!native || !native.NodeIQQNTWrapperSession)return false;
  const sessions=new WeakSet(),services=new WeakSet(),callbacks=new WeakSet();
  function wrap(obj,name,handler) {
    try {
      const original=obj[name];
      if(typeof original!=='function')return false;
      const fn=function(...args){return handler(original,this,args);};
      const descriptor=Object.getOwnPropertyDescriptor(obj,name);
      Object.defineProperty(obj,name,descriptor&&'value' in descriptor?{...descriptor,value:fn}:{value:fn,writable:true,configurable:true});
      return obj[name]===fn;
    } catch (_) {diagnose('unwritable:'+name);return false;}
  }
  function session(value) {
    if(!value || (typeof value!=='object' && typeof value!=='function')||sessions.has(value))return;
    sessions.add(value);
    let account='';
    if(wrap(value,'init',(original,self,args)=>{
      const result=Reflect.apply(original,self,args);
      try {account=numeric(args[0]?.selfUin);diagnose(account?'identity_observed':'identity_missing');}catch(_){}
      return result;
    }))diagnose('session_init_hooked');
    wrap(value,'getMsgService',(original,self,args)=>{
      const result=Reflect.apply(original,self,args);
      try {
        if(result && !services.has(result)){
          services.add(result);
          const hooked=wrap(result,'addKernelMsgListener',(fn,owner,params)=>{
            const callback=params[0];
            if(callback && !callbacks.has(callback)){
              callbacks.add(callback);
              for(const event of ['onRecvMsg','onMsgInfoListUpdate']) {
                if(wrap(callback,event,(listener,receiver,payload)=>{
                  // Preserve QQ callback this/arguments/return/exception; side effects only follow success.
                  const out=Reflect.apply(listener,receiver,payload);
                  try{notify(account,payload[0],event);}catch(_){diagnose('observer_error');}
                  return out;
                }))diagnose('callback_hooked:'+event);
              }
            }
            return Reflect.apply(fn,owner,params);
          });
          if(hooked)diagnose('message_service_hooked');
        }
      } catch(_){diagnose('service_hook_error');}
      return result;
    });
  }
  let hooked=false;
  for(const method of ['create','getNTWrapperSession']) {
    hooked=wrap(native.NodeIQQNTWrapperSession,method,(original,self,args)=>{
      const result=Reflect.apply(original,self,args);
      try{session(result);}catch(_){diagnose('session_hook_error');}
      return result;
    })||hooked;
  }
  return hooked;
}
// QQ 7.0.3 exposes immutable native methods. Keep those objects untouched and
// interpose on the CommonJS export before QQ obtains it. Each facade has stable
// identity; native methods always receive their real receiver (N-API requires it).
function normalizeGroups(rows) {
  if(!Array.isArray(rows))return null;
  const groups=new Map();
  for(const row of rows.slice(0,1000)){
    const id=numeric(row?.groupCode);
    if(!id||id.length<5||id.length>20)continue;
    groups.set(id,{group_id:id,group_name:String(row.groupName||id).slice(0,100)});
  }
  return rows.length&&!groups.size?null:[...groups.values()];
}
function mergeGroups(previous, incoming) {
  const groups=new Map(previous.map(g=>[g.group_id,g]));
  for(const group of incoming)groups.set(group.group_id,group);
  return [...groups.values()].slice(0,1000);
}
function observerExports(native, notify, diagnose=()=>{}, groupCallbacks={}, mediaCallbacks={}) {
  if(!native?.NodeIQQNTWrapperSession)return native;
  const originals=new WeakMap(),sessions=new WeakMap(),services=new WeakMap();
  const unwrap=x=>x && (typeof x==='object'||typeof x==='function') ? originals.get(x)||x : x;
  function facade(source,intercept,onConstruct) {
    const methods=new Map();
    const shell=typeof source==='function' ? (function(){}).bind(null) : Object.create(null);
    const proxy=new Proxy(shell,{
      get(_t,key){
        const value=Reflect.get(source,key,source);
        if(typeof value!=='function'||key==='constructor')return value;
        if(!methods.has(key)||methods.get(key).original!==value){
          const fn=(...args)=>intercept(key,()=>Reflect.apply(value,source,args.map(unwrap)),args,value);
          methods.set(key,{original:value,fn});
        }
        return methods.get(key).fn;
      },
      has:(_t,key)=>Reflect.has(source,key),
      ownKeys:()=>Reflect.ownKeys(source),
      getOwnPropertyDescriptor(_t,key){const d=Reflect.getOwnPropertyDescriptor(source,key);return d?{...d,configurable:true}:undefined;},
      getPrototypeOf:()=>Reflect.getPrototypeOf(source),
      set:(_t,key,value)=>Reflect.set(source,key,unwrap(value),source),
      apply:(_t,self,args)=>Reflect.apply(source,unwrap(self),args.map(unwrap)),
      construct(_t,args,newTarget){const value=Reflect.construct(source,args.map(unwrap),newTarget===proxy?source:newTarget);return onConstruct?onConstruct(value):value;},
      // Never freeze the independent shell; it would invalidate reflection traps.
      preventExtensions:()=>false
    });
    originals.set(proxy,source);return proxy;
  }
  function captureSession(real){
    if(!real||(typeof real!=='object'&&typeof real!=='function'))return real;
    if(sessions.has(real))return sessions.get(real);
    let account='';
    const proxy=facade(real,(name,call,args)=>{
      const result=call(); // QQ's original errors are not swallowed.
      if(name==='init'){
        account=numeric(args[0]?.selfUin);diagnose(account?'identity_observed':'identity_missing');
      }
      if(name==='getMsgService'&&result){
        if(!services.has(result)){
          const listeners=new WeakMap();
          const service=facade(result,(method,invoke,params,fn)=>{
            if((method==='addKernelMsgListener'||method==='removeKernelMsgListener')&&params[0]&&typeof params[0]==='object'){
              const cb=params[0];
              if(method==='addKernelMsgListener'&&!listeners.has(cb)){
                listeners.set(cb,facade(cb,(event,original,payload)=>{
                  const out=original();
                  if(event==='onRecvMsg'||event==='onMsgInfoListUpdate'){
                    try{notify(account,payload[0],event);}catch(_){diagnose('observer_error');}
                  }
                  return out;
                }));
                for(const event of ['onRecvMsg','onMsgInfoListUpdate'])if(typeof cb[event]==='function')diagnose('callback_hooked:'+event);
              }
              return Reflect.apply(fn,result,[listeners.get(cb)||cb,...params.slice(1).map(unwrap)]);
            }
            return invoke();
          });
          services.set(result,service);diagnose('message_service_hooked');
          try{mediaCallbacks.ready?.(()=>account,result);}catch(_){diagnose('media_service_failed');}
        }
        return services.get(result);
      }
      if(name==='getGroupService'&&result){
        if(!services.has(result)){
          const listeners=new WeakMap();
          const service=facade(result,(method,invoke,params,fn)=>{
            if((method==='addKernelGroupListener'||method==='removeKernelGroupListener')&&params[0]&&typeof params[0]==='object'){
              const cb=params[0];
              if(method==='addKernelGroupListener'&&!listeners.has(cb)){
                listeners.set(cb,facade(cb,(event,original,payload)=>{
                  const out=original();
                  if(event==='onGroupListUpdate'){
                    try{const rows=normalizeGroups(payload[1]);if(rows)groupCallbacks.update?.(account,rows);}catch(_){diagnose('group_observer_error');}
                  }
                  return out;
                }));
                if(typeof cb.onGroupListUpdate==='function')diagnose('callback_hooked:onGroupListUpdate');
              }
              const out=Reflect.apply(fn,result,[listeners.get(cb)||cb,...params.slice(1).map(unwrap)]);
              if(method==='addKernelGroupListener'&&typeof cb.onGroupListUpdate==='function'&&typeof result.getGroupList==='function'){
                try{groupCallbacks.ready?.(()=>account,()=>Reflect.apply(result.getGroupList,result,[true]));}catch(_){diagnose('group_ready_error');}
              }
              return out;
            }
            return invoke();
          });
          services.set(result,service);diagnose('group_service_hooked');
        }
        return services.get(result);
      }
      return result;
    });
    sessions.set(real,proxy);diagnose('session_facade_created');return proxy;
  }
  const cls=facade(native.NodeIQQNTWrapperSession,(method,call)=>{
    const value=call();return method==='create'||method==='getNTWrapperSession'?captureSession(value):value;
  },captureSession);
  const descriptors=Object.getOwnPropertyDescriptors(native);
  descriptors.NodeIQQNTWrapperSession={value:cls,writable:false,enumerable:descriptors.NodeIQQNTWrapperSession?.enumerable??true,configurable:false};
  diagnose('native_exports_facade');
  return Object.create(Object.getPrototypeOf(native),descriptors);
}
module.exports={normalizeMessage,normalizeGroups,mergeGroups,installObserver,observerExports,numeric};
