"""Local OneBot v11 forward WebSocket receiver and read-only discovery RPCs."""
import json
import threading
import time
from urllib.parse import urlparse


def validate_url(url):
    parts = urlparse(url)
    if (parts.scheme != 'ws' or parts.hostname not in ('127.0.0.1', 'localhost')
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('只允许本机 ws://127.0.0.1:端口 或 ws://localhost:端口；凭据单独保存')
    if not parts.port:
        raise ValueError('请指定 WebSocket 端口')
    return url


def seed_history(store, account, selected, starts):
    for group in selected:
        prior = store.db.execute('SELECT grant_id FROM connector_history WHERE account=? AND group_id=?',
                                 (account, group)).fetchone()
        if prior and prior['grant_id']:
            if group in starts:
                raise ValueError('该群已启用补收，请刷新群列表')
            continue
        now = int(time.time())
        local = store.db.execute("""SELECT max(CAST(strftime('%s',m.sent_at) AS INTEGER))
            FROM analysis_jobs j JOIN messages m ON m.id=j.message_id WHERE j.account=? AND j.group_id=?
            AND CAST(strftime('%s',m.sent_at) AS INTEGER)<=?""",(account,group,now)).fetchone()[0]
        since = starts.get(group, (local if not prior else None) or now)
        store.db.execute("""INSERT OR REPLACE INTO connector_history(account,group_id,grant_id,since)
            VALUES (?,?,lower(hex(randomblob(12))),?)""",(account,group,since))


class Connector:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.refresh_requested = threading.Event()
        self.state = {'status': 'disabled', 'message': '尚未启用 NapCat 连接', 'account': None,
                      'groups': [], 'connected_at': None, 'last_packet': None, 'last_saved': None,
                      'received': 0, 'saved': 0, 'retry_count': 0}
        self.pending = {}
        self.sequence = 0
        self.history_pending = {}
        self.history_scans = {}
        self.history_tick = 0

    def snapshot(self):
        with self.lock:
            return dict(self.state, groups=list(self.state['groups']))

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def rpc(self, socket, action):
        # Intentionally no send_message / moderation / file-reading actions.
        if action not in ('get_login_info', 'get_group_list', 'get_status'):
            raise ValueError('不允许的接入操作')
        self.sequence += 1
        echo = str(self.sequence)
        self.pending[echo] = (action, time.monotonic())
        socket.send(json.dumps({'action': action, 'params': {}, 'echo': echo}))

    def poll_history(self, socket):
        now = time.monotonic()
        if now < self.history_tick:
            return
        self.history_tick = now + 1
        s = self.store
        current = self.snapshot()
        account = (current['account'] or {}).get('user_id')
        with s.lock:
            if (current['status'] != 'online' or not account or account != s.setting('ws_account')
                    or not s.setting('qq_managed_login') or s.setting('native_enabled')
                    or not s.setting('ws_enabled') or not s.setting('bridge_enabled')):
                return
            grants = s.query('SELECT * FROM connector_history WHERE account=? AND grant_id IS NOT NULL', (account,))
        active = {g['grant_id'] for g in grants}
        self.history_scans = {k:v for k,v in self.history_scans.items() if k in active}
        self.history_pending = {e:v for e,v in self.history_pending.items() if v[0] in active and now-v[1]<45}
        waiting = {v[0] for v in self.history_pending.values()}
        for g in grants:
            grant = g['grant_id']
            scan = self.history_scans.setdefault(grant, {'anchor':'', 'latest':0, 'after':0})
            if grant in waiting or now < scan['after']:
                continue
            self.sequence += 1
            echo = 'history-' + str(self.sequence)
            self.history_pending[echo] = (grant,now)
            params = {'group_id':g['group_id'], 'count':50, 'reverseOrder':True,
                      'disable_get_url':True, 'parse_mult_msg':False}
            if scan['anchor']:
                params['message_seq'] = scan['anchor']
            socket.send(json.dumps({'action':'get_group_msg_history', 'params':params, 'echo':echo}))

    def history_reply(self, grant, packet):
        scan = self.history_scans.get(grant)
        if not scan:
            return
        s = self.store
        with s.lock:
            row = s.db.execute('SELECT * FROM connector_history WHERE grant_id=?', (grant,)).fetchone()
            current = self.snapshot()
            if not row or current['status'] != 'online' or (current['account'] or {}).get('user_id') != row['account']:
                return
            group, account = row['group_id'], row['account']
            names = {g['group_id']:g['group_name'] for g in current['groups']}
            name = names.get(group) or (s.setting('native_group_names') or {}).get(account, {}).get(group) or group
            rows = (packet.get('data') or {}).get('messages') if isinstance(packet.get('data'),dict) else None
            cutoff = max(row['since'], row['cursor'])
            try:
                if packet.get('retcode') != 0 or not isinstance(rows,list):
                    # The bundled adapter reports an empty history as an error on its first page.
                    if not scan['anchor'] and '消息undefined不存在' in str(packet.get('message','') or packet.get('wording','')):
                        rows = []
                    else:
                        raise ValueError()
                if any(not isinstance(m,dict) or str(m.get('group_id')) != group
                       or str(m.get('self_id',account)) != account or m.get('message_id') is None
                       or int(m.get('time',0))<=0 for m in rows):
                    raise ValueError()
                ordered = sorted(rows, key=lambda m:(int(m['time']),int(m.get('real_seq') or 0)))
                for m in ordered:
                    sent = int(m['time'])
                    if sent >= cutoff:
                        event = dict(m, group_name=name, self_id=account, post_type='message', message_type='group')
                        if not event.get('message'):
                            event['message'] = event.get('raw_message') or '[其他消息类型：未解析]'
                        s.onebot(event, 'QQ 历史补收', backfill=True)
                        scan['latest'] = max(scan['latest'], sent)
                if ordered and int(ordered[0]['time']) >= cutoff:
                    anchor = str(ordered[0]['message_id'])
                    if anchor != scan['anchor']:
                        scan['anchor'] = anchor
                        return
                    if len(rows)>=50:
                        raise ValueError()
                with s.db:
                    s.db.execute('UPDATE connector_history SET cursor=max(cursor,?),checked=? WHERE grant_id=?',
                                 (scan['latest'],time.time(),grant))
                self.update(history_status=f'{name} · 已核对')
            except (TypeError,ValueError,OverflowError):
                self.update(history_status=f'{name} · 暂未完成核对，将自动重试')
            scan.update(anchor='',latest=0,after=time.monotonic()+60)

    def handle_packet(self, packet):
        if not isinstance(packet, dict):
            return
        self.update(last_packet=time.time())
        if 'echo' in packet:
            history = self.history_pending.pop(str(packet['echo']), None)
            if history:
                self.history_reply(history[0], packet)
                return
            request = self.pending.pop(str(packet['echo']), None)
            if not request:
                return
            action, _ = request
            if packet.get('retcode') != 0 or packet.get('status') not in ('ok', None):
                self.update(status='waiting_login', message='NapCat 尚未提供账号或群列表，请完成扫码登录')
                return
            data = packet.get('data')
            if action == 'get_login_info' and isinstance(data, dict) and data.get('user_id'):
                account = {'user_id': str(data['user_id']), 'nickname': str(data.get('nickname', 'QQ 用户'))}
                previous = self.snapshot()['account']
                if previous and previous['user_id'] != account['user_id']:
                    self.update(groups=[])
                self.update(account=account, status='online', message='QQ 已登录，请选择要收集的群')
            elif action == 'get_group_list' and isinstance(data, list):
                groups = [{'group_id': str(x['group_id']), 'group_name': str(x.get('group_name', x['group_id'])),
                           'member_count': x.get('member_count', 0)} for x in data if isinstance(x, dict) and x.get('group_id')]
                self.update(groups=groups)
            elif action == 'get_status' and isinstance(data, dict) and data.get('online') is False:
                self.update(status='waiting_login', message='QQ 已离线，请在 NapCat 中重新登录')
            return
        if packet.get('post_type') == 'meta_event':
            if packet.get('meta_event_type') == 'heartbeat' and packet.get('status', {}).get('online') is False:
                self.update(status='waiting_login', message='QQ 心跳显示离线，请重新登录')
            return
        current = self.snapshot()
        self.update(received=current['received'] + 1)
        account = current.get('account') or {}
        if current['status'] != 'online' or not account.get('user_id'):
            return
        # Reusing a NapCat instance for another account must not silently reuse consent.
        if self.store.setting('ws_account') != account['user_id']:
            return
        if str(packet.get('self_id', '')) != account['user_id']:
            return
        if not self.store.setting('bridge_enabled'):
            return
        if self.store.setting('native_enabled') and self.store.setting('qq_managed_login'):
            return
        group_id = str(packet.get('group_id', ''))
        if group_id not in self.store.setting('bridge_groups'):
            return
        names = {x['group_id']: x['group_name'] for x in current['groups']}
        packet = dict(packet, group_name=names.get(group_id, group_id))
        result = self.store.onebot(packet)
        saved = result.get('messages', 0) + int(bool(result.get('file_event')))
        if saved:
            self.update(saved=current['saved'] + saved, last_saved=time.time())

    def run(self, stop):
        import websocket
        while not stop.is_set():
            if not self.store.setting('ws_enabled'):
                self.update(status='disabled', message='NapCat 连接已停用', account=None, groups=[])
                stop.wait(1)
                continue
            socket = None
            try:
                url = validate_url(self.store.setting('ws_url'))
                token = self.store.setting('ws_token')
                if not token:
                    raise ValueError('尚未设置 NapCat 连接凭据')
                self.update(status='connecting', message='正在连接本机 NapCat…', account=None, groups=[])
                socket = websocket.create_connection(url, header={'Authorization': 'Bearer ' + token}, timeout=8,
                                                       http_no_proxy=['127.0.0.1', 'localhost'], suppress_origin=True)
                socket.settimeout(2)
                self.pending.clear()
                self.history_scans.clear()
                self.history_pending.clear()
                self.history_tick = 0
                self.update(status='waiting_login', message='通道已连接，正在读取 QQ 登录状态', connected_at=time.time())
                last_refresh = float('-inf')
                last_data = time.monotonic()
                while not stop.is_set() and self.store.setting('ws_enabled'):
                    if (self.store.setting('ws_url'), self.store.setting('ws_token')) != (url, token):
                        break
                    now = time.monotonic()
                    if now - last_refresh >= 30 or self.refresh_requested.is_set():
                        self.refresh_requested.clear()
                        self.pending.clear()
                        for action in ('get_login_info', 'get_group_list', 'get_status'):
                            self.rpc(socket, action)
                        last_refresh = now
                    self.poll_history(socket)
                    try:
                        raw = socket.recv()
                    except websocket.WebSocketTimeoutException:
                        if now - last_data > 65:
                            raise ConnectionError('heartbeat timeout')
                        continue
                    if not raw:
                        raise ConnectionError('closed')
                    last_data = time.monotonic()
                    if len(raw) > 4 * 1024 * 1024:
                        continue
                    try:
                        self.handle_packet(json.loads(raw))
                    except (ValueError, TypeError, KeyError):
                        self.update(message='收到无法解析的事件；已跳过并继续接收')
            except Exception as exc:
                # Never reflect exception strings that might include headers or tokens.
                code = getattr(exc, 'status_code', None)
                text = 'NapCat 拒绝凭据，请检查连接 Token' if code in (401, 403) else 'NapCat 未启动、未登录或连接中断；5 秒后自动重连'
                self.update(status='disconnected', message=text, account=None, groups=[], retry_count=self.snapshot()['retry_count'] + 1)
            finally:
                if socket:
                    socket.close()
            stop.wait(5)

    def select_groups(self, groups, start_dates=None):
        current = self.snapshot()
        if current['status'] != 'online' or not current['account']:
            raise ValueError('请先连接并登录 QQ，再选择群')
        available = {x['group_id'] for x in current['groups']}
        if not isinstance(groups, list) or any(str(x) not in available for x in groups):
            raise ValueError('包含不在当前账号列表中的群，请刷新列表')
        with self.store.lock, self.store.db:
            if not self.store.setting('ws_enabled') and self.store.setting('qq_login_revision'):
                raise ValueError('请先登录 QQ，再选择群')
            if self.snapshot()['account'] != current['account']:
                raise ValueError('账号已变更，请刷新群列表')
            account = current['account']['user_id']
            selected = list(dict.fromkeys(str(x) for x in groups))
            managed = self.store.setting('qq_managed_login') and not self.store.setting('native_enabled')
            starts = {} if start_dates is None else start_dates
            if not isinstance(starts,dict) or any(g not in selected or not managed for g in starts):
                raise ValueError('日期只能用于本次选择的群')
            from .native import history_midnight
            starts = {g:history_midnight(day) for g,day in starts.items()}
            names = self.store.setting('native_group_names') or {}
            names.setdefault(account,{}).update({g['group_id']:g['group_name'] for g in current['groups'] if g['group_id'] in selected})
            for key, value in {'bridge_groups': selected, 'ws_account':account,
                               'native_group_names':names, 'bridge_enabled':bool(selected)}.items():
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
            if managed:
                seed_history(self.store,account,selected,starts)
