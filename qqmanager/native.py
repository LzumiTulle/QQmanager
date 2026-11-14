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
            report.update(enabled=bool(self.store.setting('native_enabled')),saved=self.saved,
                          received=self.received,last_event=self.last_event,
                          connected=bool(report.get('last_seen') and time.time()-report['last_seen']<35))
            return report
