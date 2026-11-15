"""Authenticated, receive-only bridge from the user's existing QQ process."""
import time
import threading


class NativeBridge:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.report = {}
        self.received = self.saved = 0
        self.last_event = None

    def group_catalog(self):
        s = self.store
        account = s.setting('ws_account')
        names = (s.setting('native_group_names') or {}).get(account, {})
        catalog = dict(names)
        for row in s.query('''SELECT j.group_id,m.group_name FROM analysis_jobs j
                JOIN messages m ON m.id=j.message_id WHERE j.account=? ORDER BY m.id''', (account,)):
            if row['group_name'] != row['group_id']:
                catalog[row['group_id']] = row['group_name']
        selected = s.setting('bridge_groups')
        for group in selected:
            catalog.setdefault(group, '')
        return [{'group_id': group, 'group_name': name or '群 ' + group, 'selected': group in selected}
                for group,name in sorted(catalog.items(), key=lambda x: (x[0] not in selected, x[1], x[0]))]

    def select_groups(self, groups, names=None):
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
        with s.lock,s.db:
            catalog = s.setting('native_group_names') or {}
            known = catalog.setdefault(account,{})
            for group in s.setting('bridge_groups'):
                known.setdefault(group,'')
            for group in groups:
                known[group] = names.get(group,known.get(group,''))
            s.set_setting('native_group_names',catalog)
            s.set_setting('bridge_groups',list(dict.fromkeys(groups)))
            s.set_setting('bridge_enabled',bool(groups))
            s.set_setting('native_enabled',True)
        return {'groups':s.setting('bridge_groups'),'collecting':bool(groups)}

    def heartbeat(self, body):
        s = self.store
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
        with self.lock:
            self.report = report
        return {'enabled': bool(s.setting('native_enabled') and s.setting('bridge_enabled')),
                'account': s.setting('ws_account'), 'groups': s.setting('bridge_groups')}

    def events(self, body):
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
                if not isinstance(seg,dict) or seg.get('type') not in ('text','file') or not isinstance(seg.get('data'),dict):
                    raise ValueError('只接收文本和文件名')
                value=seg['data'].get('text' if seg['type']=='text' else 'name')
                if not isinstance(value,str) or len(value)>20000:
                    raise ValueError('消息段过长或无效')
            if not isinstance(event.get('time'),(int,float)) or not str(event.get('message_id','')).lstrip('-').isdigit():
                raise ValueError('缺少消息时间或标识')
        if not s.setting('native_enabled') or not s.setting('bridge_enabled'):
            raise ValueError('插件收集已暂停，消息应继续在客户端排队')
        saved=0
        for event in events:
            result=s.onebot(event,source='QQ 客户端插件')
            saved+=result.get('messages',0)
        with self.lock:
            self.received+=len(events);self.saved+=saved
            if saved:self.last_event=time.time()
        return {'ok':True,'messages':saved}

    def snapshot(self):
        with self.lock:
            report=dict(self.report)
            report.update(enabled=bool(self.store.setting('native_enabled') and self.store.setting('bridge_enabled')),saved=self.saved,
                          received=self.received,last_event=self.last_event,
                          groups=self.group_catalog(),
                          connected=bool(report.get('last_seen') and time.time()-report['last_seen']<35))
            return report
