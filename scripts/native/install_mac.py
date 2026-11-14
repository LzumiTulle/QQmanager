"""Reversible, source-only extension loader for this Mac's existing QQ app."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
ORIGINAL_MAIN='./application.asar/app_launcher/index.js'
NEW_MAIN='./app_launcher/qqmanager-bootstrap.cjs'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def request_config(account=None, groups=None, port=8765):
    base='http://127.0.0.1:'+str(port)
    csrf=json.load(urllib.request.urlopen(base+'/api/session',timeout=5))['csrf']
    data={}
    if account:data['account']=account
    if groups:data['groups']=groups
    req=urllib.request.Request(base+'/api/native/pair',json.dumps(data).encode(),{'Content-Type':'application/json','X-CSRF-Token':csrf})
    return json.load(urllib.request.urlopen(req,timeout=5))

def atomic(path, data, mode=0o600):
    tmp=path.with_name(path.name+'.qqmanager-tmp')
    tmp.write_bytes(data);tmp.chmod(mode);os.replace(tmp,path)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['prepare','install','restore','status'])
    p.add_argument('--app',default='/Applications/QQ.app')
    p.add_argument('--stage',default=str(ROOT/'data/native-install'))
    p.add_argument('--profile')
    a=p.parse_args();app=Path(a.app).resolve();stage=Path(a.stage).resolve()
    profile=Path(a.profile).expanduser().resolve() if a.profile else Path.home()/'Library/Containers/com.tencent.qq/Data/Documents/QQmanager-native'
    package=app/'Contents/Resources/app/package.json';bootstrap=app/'Contents/Resources/app/app_launcher/qqmanager-bootstrap.cjs'
    current=package.read_bytes();parsed=json.loads(current)
    if a.action=='status':
        print(json.dumps({'version':parsed.get('version'),'entry':parsed.get('main'),'installed':parsed.get('main')==NEW_MAIN,'backup_exists':(stage/'backup.json').exists()}));return
    if a.action=='prepare':
        if parsed.get('main')!=ORIGINAL_MAIN:raise SystemExit('当前 QQ 入口不是原版，停止准备，避免覆盖已有插件。')
        stage.mkdir(parents=True,exist_ok=True,mode=0o700)
        config=request_config()
        atomic(stage/'config.json',json.dumps(config).encode())
        atomic(stage/'plan.json',json.dumps({'app':str(app),'profile':str(profile),'package_sha256':sha(current),'version':parsed.get('version')}).encode())
        # Loader errors never suppress the normal QQ entry. No second QQ session is created.
        text="""'use strict';
const path=require('node:path'), fs=require('node:fs');
function report(phase,error) { try {fs.writeFileSync(REPORT,JSON.stringify({phase,error_name:error?.name||'',error_code:error?.code||'',at:Date.now()}),{mode:0o600});}catch(_){} }
try { require(PLUGIN).start(CONFIG); report('plugin_started'); } catch (error) { report('plugin_error',error); }
require(path.join(process.resourcesPath,'app',ORIGINAL));
report('qq_entry_loaded');
setImmediate(()=>{try {if(global.launcher?.installPathPkgJson)global.launcher.installPathPkgJson.main=ORIGINAL;}catch(_){}});
""".replace('PLUGIN',json.dumps(str(profile/'main.cjs'))).replace('CONFIG',json.dumps(str(profile/'config.json'))).replace('ORIGINAL',json.dumps(ORIGINAL_MAIN)).replace('REPORT',json.dumps(str(profile/'bootstrap-status.json')))
        atomic(stage/'bootstrap.cjs',text.encode())
        print('准备完成：QQ '+str(parsed.get('version'))+'；原版入口未修改；已生成可恢复的安装计划。');return
    if a.action=='restore':
        backup=json.loads((stage/'backup.json').read_text())
        if str(package)!=backup['package']:raise SystemExit('备份目标不一致')
        if sha(current)!=backup['installed_sha256']:
            if sha(current)==backup['original_sha256']:print('QQ 已是备份时的原版入口。');return
            raise SystemExit('QQ 文件已被其他程序更新，停止覆盖；请按当前版本重新检查。')
        atomic(package,base64.b64decode(backup['original']),backup['mode'])
        if bootstrap.exists() and sha(bootstrap.read_bytes())==backup['bootstrap_sha256']:bootstrap.unlink()
        print('已恢复原版 QQ 入口；插件配置与原始记录保留。下次启动 QQ 生效。');return
    plan=json.loads((stage/'plan.json').read_text())
    if plan['app']!=str(app) or sha(current)!=plan['package_sha256'] or parsed.get('main')!=ORIGINAL_MAIN:
        raise SystemExit('QQ 版本或入口在准备后有变化，请重新核查。')
    if bootstrap.exists():raise SystemExit('入口扩展文件已存在，停止覆盖。')
    profile=Path(plan['profile']);profile.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ('main.cjs','core.cjs','manifest.json'):
        atomic(profile/name,(ROOT/'plugins/qqmanager-native'/name).read_bytes())
    atomic(profile/'config.json',(stage/'config.json').read_bytes())
    patched=dict(parsed,main=NEW_MAIN);raw=(json.dumps(patched,ensure_ascii=False,indent=2)+'\n').encode()
    boot=(stage/'bootstrap.cjs').read_bytes();mode=package.stat().st_mode&0o777
    backup={'package':str(package),'original':base64.b64encode(current).decode(),'original_sha256':sha(current),
            'installed_sha256':sha(raw),'bootstrap_sha256':sha(boot),'mode':mode}
    atomic(stage/'backup.json',json.dumps(backup).encode())
    atomic(bootstrap,boot,0o644)
    try:atomic(package,raw,mode)
    except BaseException:
        bootstrap.unlink(missing_ok=True);raise
    print('已安装 QQmanager 源码扩展；下次正常启动 QQ 生效。恢复命令：python3 scripts/native/install_mac.py restore')

if __name__=='__main__':main()
