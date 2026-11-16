"""Local attachment storage. Attachment bytes never become executable paths."""
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from .importers import MAX_FILE, extract_file


def fingerprint(account, group, event_id):
    return hashlib.sha256(json.dumps({'onebot':[str(account),str(group),str(event_id)]},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def migrate(store):
    store.db.executescript('''CREATE TABLE IF NOT EXISTS attachments (
      id INTEGER PRIMARY KEY, message_id INTEGER REFERENCES messages(id), slot INTEGER,
      element_id TEXT DEFAULT '', kind TEXT, name TEXT, size INTEGER DEFAULT 0,
      file_id INTEGER REFERENCES files(id), status TEXT DEFAULT 'missing',
      error TEXT DEFAULT '', UNIQUE(message_id,slot));''')
    # Old clients discarded attachment identifiers. Keep explicit missing entries
    # until the selected QQ group supplies the original event again.
    for row in store.query("SELECT id,content FROM messages WHERE source='QQ 客户端插件'"):
        slot=0
        for match in re.finditer(r'\[(?:文件：([^\]]+)|图片：未解析)\]',row['content']):
            name=match[1] or '图片'
            store.db.execute('INSERT OR IGNORE INTO attachments(message_id,slot,kind,name) VALUES (?,?,?,?)',
                             (row['id'],slot,'file' if match[1] else 'image',name))
            slot+=1
    store.db.commit()


def register(store,event):
    rows=store.query('SELECT id FROM messages WHERE fingerprint=?',(fingerprint(event['self_id'],event['group_id'],event['message_id']),))
    if not rows:return
    mid=rows[0]['id'];slot=0
    with store.lock,store.db:
        for segment in event['message']:
            if segment['type'] not in ('file','image'):continue
            d=segment['data']
            name=Path(str(d.get('name') or '图片').replace('\\','/')).name[:250]
            store.db.execute('''INSERT INTO attachments(message_id,slot,element_id,kind,name,size) VALUES (?,?,?,?,?,?)
                ON CONFLICT(message_id,slot) DO UPDATE SET element_id=excluded.element_id,name=excluded.name,size=excluded.size''',
                (mid,slot,str(d.get('element_id','')),segment['type'],name,max(0,min(int(d.get('size') or 0),2**40))))
            slot+=1


def receive(store,body):
    account,group=str(body.get('account','')),str(body.get('group',''))
    if (account!=store.setting('ws_account') or group not in store.setting('bridge_groups')
            or not store.setting('native_enabled') or not store.setting('bridge_enabled')):
        raise ValueError('附件不在当前授权的账号和群范围内')
    rows=store.query('''SELECT a.*,m.group_name FROM attachments a JOIN messages m ON m.id=a.message_id
        WHERE m.fingerprint=? AND a.element_id=?''',(fingerprint(account,group,body.get('message_id','')),str(body.get('element_id',''))))
    if not rows:raise ValueError('附件没有匹配的原始消息')
    a=rows[0]
    if body.get('unavailable'):
        if a['file_id'] is None:
            with store.lock,store.db:store.db.execute("UPDATE attachments SET status='unavailable',error='QQ 未能取得附件，可在 QQ 下载后重新尝试' WHERE id=?",(a['id'],))
        return {'ok':True}
    raw=base64.b64decode(body.get('data',''),validate=True)
    if not raw or len(raw)>MAX_FILE:raise ValueError('附件为空或超过 20 MB')
    digest=hashlib.sha256(raw).hexdigest()
    path=store.blobs/digest
    if not path.exists():path.write_bytes(raw);path.chmod(0o600)
    with store.lock,store.db:
        store.db.execute('''INSERT OR IGNORE INTO files(digest,name,group_name,size,body,parse_status,source,created)
          VALUES (?,?,?,?,?,?,?,?)''',(digest,a['name'],a['group_name'],len(raw),'','正在解析','QQ 客户端附件',time.time()))
        fid=store.db.execute('SELECT id FROM files WHERE digest=? AND name=? AND group_name=?',(digest,a['name'],a['group_name'])).fetchone()[0]
        changed=a['file_id']!=fid or a['status'] in ('missing','unavailable','error')
        if changed:store.db.execute("UPDATE attachments SET file_id=?,size=?,status='pending',error='' WHERE id=?",(fid,len(raw),a['id']))
    return {'ok':True,'file_id':fid}


def requeue_related(store,mid):
    """Re-read an announcement when its nearby, same-sender attachment becomes available."""
    source=store.query('''SELECT m.*,j.account,j.group_id FROM messages m JOIN analysis_jobs j ON j.message_id=m.id WHERE m.id=?''',(mid,))
    if not source:return
    s=source[0]
    for row in store.query('''SELECT m.id,m.sender,m.sent_at FROM messages m JOIN analysis_jobs j ON j.message_id=m.id
        WHERE j.account=? AND j.group_id=? AND m.sender=? AND abs(julianday(m.sent_at)-julianday(?))<0.007''',
        (s['account'],s['group_id'],s['sender'],s['sent_at'])):
        store.db.execute("UPDATE analysis_jobs SET state='pending',attempts=0,next_at=0,error='',filter_reason='' WHERE message_id=?",(row['id'],))


def step(store):
    rows=store.query("SELECT a.*,f.digest FROM attachments a JOIN files f ON f.id=a.file_id WHERE a.status='pending' ORDER BY a.id LIMIT 1")
    if not rows:return False
    a=rows[0];text=''
    try:
        raw=(store.blobs/a['digest']).read_bytes()
        if a['kind']=='image':
            from .ocr import recognize
            text=recognize(store.blobs/a['digest'])
            status='已识别图片文字（请核对原图）' if text.strip() else '图片无可识别文字'
        else:text,status=extract_file(a['name'],raw)
        state='ready' if text.strip() else 'empty'
    except Exception:
        state,status='error','正文识别失败；原附件可查看或下载，可重试'
    with store.lock,store.db:
        store.db.execute('UPDATE files SET body=?,parse_status=? WHERE id=?',(text[:1000000],status,a['file_id']))
        store.db.execute('UPDATE attachments SET status=?,error=? WHERE id=?',(state,status if state=='error' else '',a['id']))
        requeue_related(store,a['message_id'])
    return True


def for_message(store,mid):
    return store.query('''SELECT a.*,substr(f.body,1,12000) text,f.parse_status FROM attachments a
        LEFT JOIN files f ON f.id=a.file_id WHERE message_id=? ORDER BY slot''',(mid,))


def analysis_source(store,row):
    row=dict(row)
    for a in for_message(store,row['id']):
        if a.get('text'):
            label='图片文字识别，可能有误' if a['kind']=='image' else '附件提取正文'
            row['content']+='\n【'+label+'：'+a['name']+'】\n'+a['text'][:10000]
        elif a['kind']=='image':row['content']+='\n【图片未提取到可读文字，请勿推测具体日期、地点或科目】'
    return row


def recovery_targets(store):
    account=store.setting('ws_account');groups=set(store.setting('bridge_groups'))
    rows=store.query('''SELECT DISTINCT m.fingerprint,j.group_id,m.sent_at FROM attachments a JOIN messages m ON m.id=a.message_id
        JOIN analysis_jobs j ON j.message_id=m.id WHERE j.account=? AND a.status IN ('missing','unavailable') ORDER BY m.id DESC LIMIT 100''',(account,))
    return [{'fingerprint':r['fingerprint'],'group':r['group_id']} for r in rows if r['group_id'] in groups]
