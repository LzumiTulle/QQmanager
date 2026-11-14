'use strict';
const http=require('node:http');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const ORIGIN='http://127.0.0.1:8765';
function request(route,headers={}) {
 return new Promise((resolve,reject)=>{
  const req=http.get(ORIGIN+route,{headers,timeout:1500},res=>{
   let text='',size=0;
   res.on('data',chunk=>{size+=chunk.length;if(size>2e6)req.destroy(Error('Response too large'));else text+=chunk;});
   res.on('end',()=>{try{if(res.statusCode!==200)throw Error('HTTP '+res.statusCode);resolve(JSON.parse(text));}catch(e){reject(e);}});
  });req.on('timeout',()=>req.destroy(Error('timeout')));req.on('error',reject);
 });
}
function isHealth(x){return x?.app==='QQmanager'&&x.protocol===1;}
function allowNavigation(url){try{const u=new URL(url);return u.origin===ORIGIN&&u.pathname==='/';}catch{return false;}}
function externalURL(url){try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password;}catch{return false;}}
class Service {
 constructor({resources,packaged,dataDirectory,root}){Object.assign(this,{resources,packaged,dataDirectory,root});this.child=null;this.external=false;this.stopping=false;}
 async start(){
  try{
   const health=await request('/api/health');
   if(!isHealth(health))throw Error('端口 8765 被其他程序占用。');
   const session=await request('/api/session');
   const details=await request('/api/desktop',{'X-CSRF-Token':session.csrf});
   if(typeof details.data_directory!=='string'||!path.isAbsolute(details.data_directory))throw Error('无法确认已有服务的数据目录。');
   this.dataDirectory=details.data_directory;this.external=true;return;
  }catch(e){if(e.code!=='ECONNREFUSED')throw Error('无法连接已有服务。请升级或退出旧版 QQmanager 服务后重试。'+e.message);}
  fs.mkdirSync(this.dataDirectory,{recursive:true,mode:0o700});
  let executable,args;
  if(this.packaged){executable=path.join(this.resources,'backend','qqmanager-server'+(process.platform==='win32'?'.exe':''));args=[];}
  else {executable=process.env.QQMANAGER_PYTHON||path.join(this.root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');args=['-B',path.join(this.root,'run.py')];}
  const log=fs.openSync(path.join(this.dataDirectory,'desktop-server.log'),'a',0o600);
  this.child=spawn(executable,[...args,'--port','8765','--data-dir',this.dataDirectory],{stdio:['pipe',log,log],windowsHide:true,env:{...process.env,QQMANAGER_DESKTOP:'1',PYTHONUNBUFFERED:'1'}});fs.closeSync(log);
  let failure=null;this.child.once('error',e=>{failure=e;});
  for(let i=0;i<100;i++){
   if(failure||this.child.exitCode!==null)throw Error('后台服务启动失败，请查看 desktop-server.log。');
   try{if(isHealth(await request('/api/health')))return;}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  this.stop();throw Error('后台服务启动超时，请检查端口和日志。');
 }
 stop(){this.stopping=true;if(this.child){this.child.stdin?.end();const child=this.child;const timer=setTimeout(()=>{if(child.exitCode===null)child.kill();},3000);timer.unref();}}
}
module.exports={Service,ORIGIN,request,isHealth,allowNavigation,externalURL};
