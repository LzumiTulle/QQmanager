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

    def handle_packet(self, packet):
        if not isinstance(packet, dict):
            return
        self.update(last_packet=time.time())
        if 'echo' in packet:
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

    def select_groups(self, groups):
        current = self.snapshot()
        if current['status'] != 'online' or not current['account']:
            raise ValueError('请先连接并登录 QQ，再选择群')
        available = {x['group_id'] for x in current['groups']}
        if not isinstance(groups, list) or any(str(x) not in available for x in groups):
            raise ValueError('包含不在当前账号列表中的群，请刷新列表')
        with self.store.lock, self.store.db:
            for key, value in {'bridge_groups': list(dict.fromkeys(str(x) for x in groups)),
                               'ws_account': current['account']['user_id'], 'bridge_enabled': bool(groups)}.items():
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
