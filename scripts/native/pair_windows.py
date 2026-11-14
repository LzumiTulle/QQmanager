"""Pair the already installed LiteLoader plugin with the local QQmanager service."""
import argparse
import json
import os
from pathlib import Path
import urllib.request

p=argparse.ArgumentParser(description='把 QQmanager 插件与本机服务配对；不修改 QQ 或安装 DLL')
p.add_argument('--plugin-data',required=True,help='LiteLoaderQQNT 插件数据目录：data/qqmanager_native')
p.add_argument('--account',required=True,help='你正常登录的QQ号')
p.add_argument('--groups',required=True,help='收集群号，英文逗号分隔')
p.add_argument('--port',type=int,default=8765)
a=p.parse_args()
if not a.account.isdigit():raise SystemExit('QQ号必须为数字')
base='http://127.0.0.1:'+str(a.port)
csrf=json.load(urllib.request.urlopen(base+'/api/session',timeout=5))['csrf']
body={'account':a.account,'groups':[g.strip() for g in a.groups.split(',') if g.strip()]}
r=urllib.request.Request(base+'/api/native/pair',json.dumps(body).encode(),{'Content-Type':'application/json','X-CSRF-Token':csrf})
config=json.load(urllib.request.urlopen(r,timeout=5))
folder=Path(a.plugin_data).expanduser();folder.mkdir(parents=True,exist_ok=True)
target=folder/'config.json';tmp=folder/'config.json.tmp';tmp.write_text(json.dumps(config),encoding='utf-8');os.replace(tmp,target)
print('已配对。密钥未显示；请勿共享插件数据目录。正常重启已装载插件的 QQ 后，在自动接入页检查状态。')
