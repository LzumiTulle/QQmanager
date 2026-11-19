"""Authenticated, receive-only bridge from the user's existing QQ process."""
import time
import json
import threading
import re
from datetime import date, datetime, timedelta, timezone


def history_midnight(value):
    try:
        day = date.fromisoformat(value)
        since = int(datetime.combine(day, datetime.min.time(), timezone(timedelta(hours=8))).timestamp())
        if not 0 < since <= time.time():
            raise ValueError()
        return since
    except (TypeError, ValueError, OverflowError, OSError):
        raise ValueError('请选择有效的今日或更早日期')


def migrate_history(store):
    """Keep a fresh authorization boundary even when a group is reselected while QQ is offline."""
    db = store.db
    db.execute('''CREATE TABLE IF NOT EXISTS native_history (
        account TEXT, group_id TEXT, grant_id TEXT NOT NULL, since INTEGER NOT NULL,
        cursor INTEGER NOT NULL DEFAULT 0, checked REAL, saved INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(account,group_id))''')
    active = """SELECT json_extract(a.value,'$') account, CAST(g.value AS TEXT) group_id
        FROM settings a, settings groups, json_each(groups.value) g
        WHERE a.key='ws_account' AND a.value!='""' AND groups.key='bridge_groups'
        AND (SELECT value FROM settings WHERE key='bridge_enabled')='true'
        AND (SELECT value FROM settings WHERE key='native_enabled')='true'"""
    reconcile = f"""DELETE FROM native_history WHERE (account,group_id) NOT IN ({active});
        INSERT OR IGNORE INTO native_history(account,group_id,grant_id,since)
        SELECT account,group_id,lower(hex(randomblob(12))),CAST(strftime('%s','now') AS INTEGER)
        FROM ({active}) a WHERE NOT EXISTS (SELECT 1 FROM native_history h
        WHERE h.account=a.account AND h.group_id=a.group_id);"""
    # Only the first grant may include the interval following the last locally
    # archived message. Remember a revoked grant even after its live row is gone.
    db.execute('''CREATE TABLE IF NOT EXISTS native_history_seeded (
        account TEXT, group_id TEXT, PRIMARY KEY(account,group_id))''')
    def seed_time(account, group, cutoff):
        return f"""coalesce((SELECT max(CAST(strftime('%s',m.sent_at) AS INTEGER))
            FROM analysis_jobs j JOIN messages m ON m.id=j.message_id
            WHERE j.account={account} AND j.group_id={group} AND m.created<={cutoff}+1
              AND CAST(strftime('%s',m.sent_at) AS INTEGER)<={cutoff}),{cutoff})"""
    db.executescript(f"""CREATE TRIGGER IF NOT EXISTS native_history_first_grant
        AFTER INSERT ON native_history WHEN NOT EXISTS (SELECT 1 FROM native_history_seeded
            WHERE account=NEW.account AND group_id=NEW.group_id)
        BEGIN
            UPDATE native_history SET since={seed_time('NEW.account','NEW.group_id','NEW.since')}
                WHERE account=NEW.account AND group_id=NEW.group_id;
            INSERT INTO native_history_seeded VALUES(NEW.account,NEW.group_id);
        END;""")
    # Upgrade 0.1.6 databases once, including those already checked after launch.
    # Invalidate old in-flight acknowledgements when rewinding an existing grant.
    with db:
        for row in db.execute('''SELECT h.* FROM native_history h WHERE NOT EXISTS
                (SELECT 1 FROM native_history_seeded s WHERE s.account=h.account AND s.group_id=h.group_id)''').fetchall():
            cutoff = row['since']
            start = db.execute('SELECT '+seed_time(':account',':group_id',':since'),dict(row)).fetchone()[0]
            if start < cutoff:
                db.execute('''UPDATE native_history SET since=?,cursor=0,checked=NULL,grant_id=lower(hex(randomblob(12)))
                    WHERE account=? AND group_id=?''',(start,row['account'],row['group_id']))
            db.execute('INSERT INTO native_history_seeded VALUES(?,?)',(row['account'],row['group_id']))
    for operation in ('INSERT','UPDATE'):
        db.executescript(f"""CREATE TRIGGER IF NOT EXISTS native_history_{operation}
            AFTER {operation} ON settings WHEN NEW.key IN ('ws_account','bridge_groups','bridge_enabled','native_enabled')
            BEGIN {reconcile} END;""")
    db.executescript(reconcile)


class NativeBridge:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.report = {}
        self.received = self.saved = 0
        self.last_event = None
        self.group_refresh = 0
        self.media_refresh = 0

    def refresh_media(self):
        from .media import recovery_targets
        with self.lock:self.media_refresh += 1
        return {'requested':True,'messages':len(recovery_targets(self.store))}

    def group_catalog(self):
        s = self.store
        account = s.setting('ws_account')
        names = (s.setting('native_group_names') or {}).get(account, {})
        catalog = {}
        for row in s.query('''SELECT j.group_id,m.group_name FROM analysis_jobs j
                JOIN messages m ON m.id=j.message_id WHERE j.account=? ORDER BY m.id''', (account,)):
            if row['group_name'] != row['group_id']:
                catalog[row['group_id']] = row['group_name']
        catalog.update({g:n for g,n in names.items() if n})
        synced = s.setting('native_group_catalog') or {}
        if synced.get('account') == account:
            catalog.update({g['group_id']:g['group_name'] for g in synced.get('groups',[])})
        selected = s.setting('bridge_groups')
        for group in selected:
            catalog.setdefault(group, '')
        return [{'group_id': group, 'group_name': name or '群 ' + group, 'selected': group in selected}
                for group,name in sorted(catalog.items(), key=lambda x: (x[0] not in selected, x[1], x[0]))]

    def select_groups(self, groups, names=None, start_dates=None, start_grants=None):
        s = self.store
        account = s.setting('ws_account')
        if not account:
            raise ValueError('请先绑定日常使用的 QQ 账号')
        if (not isinstance(groups, list) or len(groups)>1000 or
                any(not isinstance(g,str) or not g.isascii() or not g.isdigit() or not 5<=len(g)<=20 for g in groups)):
            raise ValueError('群号应为 5–20 位数字')
        names = names or {}
        if not isinstance(names,dict) or any(g not in groups or not isinstance(n,str) or len(n)>100 for g,n in names.items()):
            raise ValueError('群名称无效')
        if start_dates is None: start_dates = {}
        if not isinstance(start_dates, dict) or any(g not in groups for g in start_dates):
            raise ValueError('日期只能用于本次选择的群')
        starts = {g: history_midnight(d) for g,d in start_dates.items()}
        grants = {} if start_grants is None else start_grants
        if not isinstance(grants,dict) or any(g not in starts or not isinstance(token,str) for g,token in grants.items()):
            raise ValueError('补收授权无效')
        with s.lock,s.db:
            if s.setting('ws_account') != account:
                raise ValueError('账号已变更，请重新打开群聊设置。')
            catalog = s.setting('native_group_names') or {}
            selected = set(s.setting('bridge_groups'))
            for group,since in starts.items():
                if group in grants:
                    self._rewind_empty_group(account,group,grants[group],since)
                elif group in selected:
                    raise ValueError('群收集状态已更新，请刷新后重新选择')
            known = catalog.setdefault(account,{})
            for group in s.setting('bridge_groups'):
                known.setdefault(group,'')
            for group in groups:
                known[group] = names.get(group,known.get(group,''))
            settings = dict(native_group_names=catalog, bridge_groups=list(dict.fromkeys(groups)),
                            bridge_enabled=bool(groups), native_enabled=True)
            s.db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                             [(k,json.dumps(v)) for k,v in settings.items()])
            for group,since in starts.items():
                if group in grants:
                    continue
                s.db.execute('UPDATE native_history SET since=? WHERE group_id=? AND account=?',
                             (since,group,account))
        return {'groups':s.setting('bridge_groups'),'collecting':bool(groups)}

    def refresh_groups(self):
        with self.lock:
            self.group_refresh += 1
        return {'requested':True}

    def start_history(self, body):
        """Explicit per-group first scan from midnight in the application's calendar."""
        since=history_midnight(body.get('date'))
        s=self.store
        with s.lock,s.db:
            self._rewind_empty_group(s.setting('ws_account'),body.get('group_id'),body.get('grant_id'),since)
        return {'requested':True}

    def _rewind_empty_group(self, account, group, grant, since):
        updated=self.store.db.execute('''UPDATE native_history SET since=?,cursor=0,checked=NULL,
            grant_id=lower(hex(randomblob(12))) WHERE group_id=? AND account=?
            AND grant_id=? AND since>? AND NOT EXISTS (SELECT 1 FROM analysis_jobs j
              WHERE j.group_id=native_history.group_id AND j.account=native_history.account)''',
            (since,group,account,grant,since))
        if not updated.rowcount:
            raise ValueError('请选择早于当前起点的日期。群授权或已有记录发生变化时，请刷新后再试。')

    def heartbeat(self, body):
        s = self.store
        catalog = body.get('group_catalog')
        if catalog is not None:
            if not isinstance(catalog,dict) or str(catalog.get('account'))!=s.setting('ws_account') or str(body.get('account'))!=s.setting('ws_account'):
                raise ValueError('群列表账号与绑定账号不符')
            groups=catalog.get('groups')
            if type(catalog.get('revision')) is not int or not 0<=catalog['revision']<=100000000:
                raise ValueError('群列表版本无效')
            if not isinstance(groups,list) or len(groups)>1000:
                raise ValueError('群列表格式无效')
            normalized={}
            for g in groups:
                if not isinstance(g,dict):raise ValueError('群列表格式无效')
                gid=g.get('group_id');name=g.get('group_name')
                if not isinstance(gid,str) or not gid.isascii() or not gid.isdigit() or not 5<=len(gid)<=20 or not isinstance(name,str) or len(name)>100:
                    raise ValueError('群列表内容无效')
                normalized[gid]={'group_id':gid,'group_name':name}
            prior=s.setting('native_group_catalog') or {}
            if prior.get('account')!=catalog['account'] or prior.get('revision')!=catalog.get('revision') or prior.get('groups')!=list(normalized.values()):
                s.set_setting('native_group_catalog',{'account':catalog['account'],'groups':list(normalized.values()),'revision':catalog.get('revision'),'updated':time.time()})
        # Allowlisted diagnostics; never persist arbitrary plugin data or credentials.
        report = {k: str(body.get(k, ''))[:100] for k in ('platform','architecture','version')}
        for k in ('events','selected','dropped','queued'):
            value = body.get(k, 0)
            report[k] = max(0, min(int(value), 100000000))
        signals = body.get('signals', {})
        if not isinstance(signals, dict):
            raise ValueError('无效插件状态')
        report['signals'] = {str(k)[:100]: min(max(int(v),0),100000000) for k,v in list(signals.items())[:40]}
        report['account_matches'] = bool(body.get('account')) and str(body['account']) == s.setting('ws_account')
        report['error'] = '插件暂未连通本地服务，正在重试' if body.get('error') else ''
        report['last_seen'] = time.time()
        report['history_state'] = body.get('history_state') if body.get('history_state') in ('waiting','syncing','checked','retry') else None
        if report['account_matches'] and isinstance(body.get('history_reports'),list):
            groups=set(s.setting('bridge_groups'))
            report['history_reports']=[]
            for item in body['history_reports'][:1000]:
                if not isinstance(item,dict) or not isinstance(item.get('group_id'),str) or item['group_id'] not in groups:
                    continue
                entry={'group_id':item['group_id']}
                for method in ('getAioFirstViewLatestMsgs','getLatestDbMsgs','getMsgs'):
                    value=item.get(method)
                    if isinstance(value,str) and re.fullmatch(r'ok|empty|qq:-?\d{1,12}|invalid_row|invalid_page|history timeout|exception',value):
                        entry[method]=value
                if item.get('scan') in ('checked','read_or_save_failed','history scope changed','history gap',
                    'unexpected history row','history pagination stalled','history message unavailable','history pagination unavailable','history timeout'):
                    entry['scan']=item['scan']
                report['history_reports'].append(entry)
        with self.lock:
            self.report = report
        from .media import recovery_targets
        return {'enabled': bool(s.setting('native_enabled') and s.setting('bridge_enabled')),
                'account': s.setting('ws_account'), 'groups': s.setting('bridge_groups'), 'group_refresh':self.group_refresh,
                'media_refresh':self.media_refresh,'media_targets':recovery_targets(s),
                'history':s.query('SELECT group_id,grant_id,since,cursor FROM native_history WHERE account=?',
                                  (str(body.get('account','')),)) if report['account_matches'] and body.get('history_state') else []}

    def events(self, body):
        with self.store.lock:
            result=self._save_events(body)
        with self.lock:
            self.received+=len(body['events']);self.saved+=result['messages']
            if result['messages']:self.last_event=time.time()
        return result

    def _save_events(self, body):
        s = self.store
        events = body.get('events')
        if not isinstance(events, list) or len(events) > 20:
            raise ValueError('每次最多20条消息')
        # Prevalidate the whole batch before writing any item.
        for event in events:
            if not isinstance(event, dict) or event.get('post_type') != 'message' or event.get('message_type') != 'group':
                raise ValueError('只接收群消息事件')
            if str(event.get('self_id', '')) != s.setting('ws_account') or not s.setting('ws_account'):
                raise ValueError('插件账号与已授权账号不符')
            if not isinstance(event.get('message'),list) or len(event['message'])>200:
                raise ValueError('无效消息段')
            for seg in event['message']:
                if not isinstance(seg,dict) or seg.get('type') not in ('text','file','image') or not isinstance(seg.get('data'),dict):
                    raise ValueError('无效消息或附件')
                value=seg['data'].get('text' if seg['type']=='text' else 'name')
                if not isinstance(value,str) or len(value)>20000:
                    raise ValueError('消息段过长或无效')
                if seg['type']!='text':
                    if not isinstance(seg['data'].get('element_id',''),str) or len(seg['data'].get('element_id',''))>40:
                        raise ValueError('附件标识无效')
                    if type(seg['data'].get('size',0)) not in (int,float) or not 0<=seg['data'].get('size',0)<=2**40:
                        raise ValueError('附件大小无效')
            if not isinstance(event.get('time'),(int,float)) or not str(event.get('message_id','')).lstrip('-').isdigit():
                raise ValueError('缺少消息时间或标识')
        if not s.setting('native_enabled') or not s.setting('bridge_enabled'):
            raise ValueError('插件收集已暂停，消息应继续在客户端排队')
        history=body.get('history')
        if history is not None:
            if not isinstance(history,dict):raise ValueError('无效补收范围')
            rows=s.query('SELECT * FROM native_history WHERE account=? AND group_id=? AND grant_id=?',
                         (s.setting('ws_account'),history.get('group_id'),history.get('grant_id')))
            if not rows:raise ValueError('补收授权已变更')
            scope=rows[0]
            if any(str(e.get('group_id'))!=scope['group_id'] or e['time']<scope['since'] for e in events):
                raise ValueError('消息不在补收时段或群范围内')
            through=history.get('through')
            if through is not None and (events or type(through) is not int or not 0<=through<=time.time()+1):
                raise ValueError('无效补收进度')
        saved=0
        new_ids=[]
        for event in events:
            from .media import fingerprint,register
            if history is not None or event.get('recovery'):
                exists=s.query('SELECT id,content FROM messages WHERE fingerprint=?',
                               (fingerprint(event['self_id'],event['group_id'],event['message_id']),))
                if event.get('recovery') and not exists:continue
                if history is not None and exists:
                    parts=event['message']
                    if exists[0]['content']=='[其他消息类型：未解析]' and len(parts)==1 and parts[0]['type']=='text':
                        content=parts[0]['data']['text']
                        if content.startswith('[分享卡片] '):
                            with s.db:
                                s.db.execute('UPDATE messages SET content=? WHERE id=?',(content,exists[0]['id']))
                    register(s,event)
                    continue
            result=s.onebot(event,source='QQ 客户端插件',backfill=history is not None)
            if str(event.get('group_id')) in s.setting('bridge_groups'):register(s,event)
            saved+=result.get('messages',0)
            if result.get('messages'):new_ids.append(event['message_id'])
        if history is not None:
            with s.db:
                s.db.execute('UPDATE native_history SET saved=saved+? WHERE grant_id=?',(saved,scope['grant_id']))
                if through is not None:
                    s.db.execute('UPDATE native_history SET cursor=max(cursor,?),checked=? WHERE grant_id=?',
                                 (through,time.time(),scope['grant_id']))
            return {'ok':True,'messages':saved,'new_ids':new_ids}
        return {'ok':True,'messages':saved}

    def snapshot(self):
        with self.lock:
            report=dict(self.report)
            catalog=self.store.setting('native_group_catalog') or {}
            current=catalog.get('account')==self.store.setting('ws_account')
            report.update(enabled=bool(self.store.setting('native_enabled') and self.store.setting('bridge_enabled')),saved=self.saved,
                          archived_groups=[r['group_id'] for r in self.store.query(
                              'SELECT DISTINCT group_id FROM analysis_jobs WHERE account=?',(self.store.setting('ws_account'),))],
                          empty_history=self.store.query('''SELECT group_id,since,grant_id FROM native_history h
                              WHERE NOT EXISTS (SELECT 1 FROM analysis_jobs j WHERE j.account=h.account AND j.group_id=h.group_id)'''),
                          history=self.store.query('SELECT max(checked) checked,coalesce(sum(saved),0) saved FROM native_history')[0],
                          received=self.received,last_event=self.last_event,
                          groups=self.group_catalog(),
                          groups_updated=catalog.get('updated') if current else None,
                          groups_count=len(catalog.get('groups',[])) if current else 0,
                          connected=bool(report.get('last_seen') and time.time()-report['last_seen']<35))
            return report
