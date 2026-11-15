'use strict';
const {app,BrowserWindow,Tray,Menu,nativeImage,dialog,shell,Notification}=require('electron');
const fs=require('node:fs');const path=require('node:path');
const {Service,ORIGIN,request,allowNavigation,externalURL}=require('./service.cjs');
let window,tray,service,quitting=false,ready=false,prefs={},notificationTimer;
app.setName('QQmanager');
const locked=app.requestSingleInstanceLock();
if(!locked){app.quit();}else{
 app.on('second-instance',()=>{if(ready)show();});
 app.on('activate',()=>{if(ready)show();});
 app.on('window-all-closed',()=>{});
 app.on('before-quit',()=>{quitting=true;clearInterval(notificationTimer);service?.stop();});
 app.whenReady().then(start).catch(async error=>{await dialog.showMessageBox({type:'error',title:'QQmanager 启动失败',message:error.message,detail:'你的数据不会被删除。关闭占用端口的旧服务后重新打开；或检查数据目录中的 desktop-server.log。'});app.quit();});
}
function savePrefs(){fs.writeFileSync(path.join(app.getPath('userData'),'desktop.json'),JSON.stringify(prefs,null,2),{mode:0o600});}
function login(on){
 if(!app.isPackaged)return;
 app.setLoginItemSettings({openAtLogin:on,path:process.execPath,args:['--background']});prefs.autoStart=on;savePrefs();
}
async function show(){
 if(!window||window.isDestroyed()){
  window=new BrowserWindow({width:1280,height:850,minWidth:900,minHeight:650,title:'QQmanager',show:false,backgroundColor:'#f6f8fc',webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,spellcheck:false}});
  const ses=window.webContents.session;
  ses.setPermissionRequestHandler((_wc,permission,callback)=>callback(permission==='notifications'&&allowNavigation(_wc.getURL())));
  ses.setPermissionCheckHandler((_wc,permission,origin)=>permission==='notifications'&&origin===ORIGIN);
  window.webContents.setWindowOpenHandler(({url})=>{if(externalURL(url))shell.openExternal(url);return {action:'deny'};});
  window.webContents.on('will-navigate',(event,url)=>{if(!allowNavigation(url)){event.preventDefault();if(externalURL(url))shell.openExternal(url);}});
  window.webContents.on('will-attach-webview',event=>event.preventDefault());
  window.on('close',event=>{if(!quitting){event.preventDefault();window.hide();}});
  window.on('page-title-updated',event=>event.preventDefault());
  await window.loadURL(ORIGIN);
 }
 window.show();window.focus();
}
function menu(){
 const template=[{label:'打开 QQmanager',click:()=>show()},
 {label:'登录系统后后台启动',type:'checkbox',checked:!!prefs.autoStart,enabled:app.isPackaged,click:item=>{try{login(item.checked);}catch(e){dialog.showErrorBox('开机启动设置失败',e.message);}rebuildMenu();}},
 {label:'打开数据文件夹',click:()=>shell.openPath(service.dataDirectory)},
 {label:process.platform==='darwin'?'安装 / 更新 QQ 消息插件':'打开 QQ 插件与安装说明',click:()=>{
  if(process.platform==='darwin'&&app.isPackaged)shell.openPath(path.join(process.resourcesPath,'QQmanager Installer.app')).then(error=>{if(error)dialog.showErrorBox('无法打开插件助手',error);});
  else shell.openExternal('https://github.com/LzumiTulle/QQmanager/blob/main/docs/NATIVE-PLUGIN.md');
 }},
 {label:'使用说明',click:()=>shell.openExternal('https://github.com/LzumiTulle/QQmanager#readme')},
 {type:'separator'},{label:'退出 QQmanager',accelerator:'CommandOrControl+Q',click:()=>app.quit()}];
 return template;
}
function rebuildMenu(){tray?.setContextMenu(Menu.buildFromTemplate(menu()));Menu.setApplicationMenu(Menu.buildFromTemplate([{label:'QQmanager',submenu:menu()},{label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},{label:'视图',submenu:[{role:'reload'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'}]}]));}
async function start(){
 if(process.platform==='win32')app.setAppUserModelId('io.github.lzumitulle.qqmanager');
 fs.mkdirSync(app.getPath('userData'),{recursive:true,mode:0o700});
 try{prefs=JSON.parse(fs.readFileSync(path.join(app.getPath('userData'),'desktop.json'),'utf8'));}catch{}
 service=new Service({resources:process.resourcesPath,packaged:app.isPackaged,dataDirectory:prefs.dataDirectory||path.join(app.getPath('userData'),'data'),root:path.join(__dirname,'..')});
 await service.start();prefs.dataDirectory=service.dataDirectory;savePrefs();
 if(service.child)service.child.once('exit',()=>{if(!quitting)dialog.showErrorBox('后台服务已停止','请退出并重新打开 QQmanager；原文与设置仍保留。');});
 const trayIcon=nativeImage.createFromPath(path.join(__dirname,'tray.png'));if(process.platform==='darwin')trayIcon.setTemplateImage(true);
 tray=new Tray(trayIcon);tray.setToolTip('QQmanager · 后台运行');tray.on('click',()=>show());
 if(prefs.autoStart===undefined){try{login(true);}catch{prefs.autoStart=false;savePrefs();}}
 rebuildMenu();ready=true;
 if(!process.argv.includes('--background')&&!app.getLoginItemSettings().wasOpenedAtLogin)await show();
 // Native reminders work even while the window is hidden. Persist IDs to avoid
 // repeated popups after a restart; already-existing reminders are not replayed.
 let initialized=Array.isArray(prefs.notified),seen=new Set(prefs.notified||[]),polling=false;
 async function poll(){if(polling)return;polling=true;try{
  const state=await request('/api/state');const rows=state.notifications||[];
  if(initialized)for(const row of rows){if(!seen.has(row.id)&&!row.seen&&Notification.isSupported()){const n=new Notification({title:'QQmanager · 待办提醒',body:String(row.title).slice(0,180)});n.on('click',()=>show());n.show();}}
  for(const row of rows)seen.add(row.id);initialized=true;prefs.notified=[...seen].slice(-1000);savePrefs();
 }catch{}finally{polling=false;}}
 await poll();notificationTimer=setInterval(poll,10000);
}
