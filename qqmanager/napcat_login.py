"""A narrow, loopback-only client for NapCat's WebUI login and QQ QR status."""
import hashlib
import http.client
import json
import secrets
import threading
import time
from urllib.parse import urlsplit



def _post(path, body, credential=''):
    connection = http.client.HTTPConnection('127.0.0.1', 6099, timeout=4)
    headers = {'Content-Type': 'application/json'}
    if credential:
        headers['Authorization'] = 'Bearer ' + credential
    try:
        connection.request('POST', '/api/' + path, json.dumps(body), headers)
        response = connection.getresponse()
        if response.status in (401, 403):
            return {'code': -1, 'message': 'Unauthorized'}
        if response.status != 200:
            raise ValueError('本机 NapCat 登录接口不可用，请核对 WebUI 版本和端口 6099。')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('NapCat 登录接口响应过大')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            result = None
        if not isinstance(result, dict) or 'code' not in result:
            raise ValueError('本机服务未返回可识别的 NapCat 登录状态。')
        return result
    finally:
        connection.close()


class NapCatLogin:
    def __init__(self):
        self.credential = ''
        self.expires = 0
        self.lock = threading.Lock()

    def update(self, body):
        with self.lock:
            try:
                return self._update(body)
            except (OSError, http.client.HTTPException):
                return {'state': 'unavailable'}

    def _update(self, body):
        token = body.get('token', '')
        otp = body.get('otp', '')
        if not isinstance(token, str) or len(token) > 1024 or not isinstance(otp, str) or len(otp) > 8:
            raise ValueError('凭据格式无效')
        if self.expires <= time.monotonic():
            self.credential = ''
        if token:
            digest = hashlib.sha256((token + '.napcat').encode()).hexdigest()
            result = _post('auth/login', {'hash': digest, 'totpCode': otp})
            data = result.get('data') or {}
            if result['code'] != 0:
                return {'state': 'authorize', 'message': '授权失败，请检查 WebUI Token 和验证码，稍后再试。'}
            if data.get('require2FA'):
                return {'state': 'two_factor'}
            if not isinstance(data.get('Credential'), str) or not data['Credential']:
                raise ValueError('NapCat 未返回登录授权。')
            self.credential = data['Credential']
            self.expires = time.monotonic() + 3500

        status = _post('QQLogin/CheckLoginStatus', {}, self.credential)
        if status['code'] != 0:
            if not self.credential or 'unauthor' in str(status.get('message', '')).lower():
                self.credential = ''
                return {'state': 'authorize'}
            raise ValueError('NapCat 无法查询登录状态，请重试。')
        data = status.get('data') or {}
        phase = data.get('loginPhase')
        if data.get('isLogin'):
            return {'state': 'online'}
        if data.get('qrLoginAccepted') or phase in ('qrcode_scanned', 'initializing'):
            return {'state': 'scanned'}
        if phase == 'reconnecting':
            return {'state': 'reconnecting'}
        link = data.get('qrcodeurl', '')
        if body.get('refresh') is True:
            refreshed = _post('QQLogin/RefreshQRcode', {}, self.credential)
            if refreshed['code'] != 0:
                return {'state': 'refresh_needed'}
            link = (refreshed.get('data') or {}).get('qrcodeurl')
        elif data.get('loginError'):
            return {'state': 'refresh_needed'}
        if not link or (phase == 'generating_qrcode' and not body.get('refresh')):
            return {'state': 'waiting'}
        if not isinstance(link, str):
            raise ValueError('NapCat 二维码格式无效')
        url = urlsplit(link)
        if len(link) > 2048 or url.scheme != 'https' or not (url.hostname or '').endswith('.qq.com') or url.username is not None:
            raise ValueError('NapCat 返回的扫码地址无法识别，请刷新二维码。')
        try:
            import segno
        except ImportError as exc:
            raise ValueError('请更新 QQmanager 的运行依赖以显示二维码。') from exc
        return {'state': 'ready', 'image': segno.make_qr(link).png_data_uri(scale=6, border=4)}


class QQBinding:
    def __init__(self, store, connector, *, managed_supported=True):
        self.store = store
        self.connector = connector
        self.runtime = None
        if managed_supported:
            from .login_runtime import LoginRuntime
            self.runtime = LoginRuntime(store.directory)
        self.login = NapCatLogin()
        self.lock = threading.Lock()

    def update(self, body):
        if self.runtime is None:
            raise ValueError('Windows 版请连接本机 QQ，或先使用本地功能。')
        with self.lock:
            if body.get('revision', '') != (self.store.setting('qq_login_revision') or ''):
                return {'state': 'expired', 'message': '账号状态已更新，请重新打开登录窗口。'}
            phase, error = self.runtime.prepare(body.get('retry') is True)
            if phase == 'failed':
                return {'state': 'failed', 'message': error}
            if phase != 'ready':
                return {'state': 'preparing'}
            try:
                return self._poll(body)
            except (OSError, http.client.HTTPException):
                if time.monotonic() - self.runtime.started < 100:
                    return {'state': 'preparing'}
                return {'state': 'failed', 'message': '登录组件未能连接，请重试。'}

    def logout(self, account, revision):
        with self.lock, self.store.lock, self.store.db:
            current = self.store.setting('ws_account')
            if not current:
                return
            if account != current or revision != (self.store.setting('qq_login_revision') or ''):
                raise ValueError('账号状态已更新，请重新打开设置。')
            if self.runtime is not None and self.store.setting('qq_managed_login'):
                self.runtime.remember_account('')
            values = {
                'ws_account': '', 'bridge_groups': [], 'bridge_enabled': False,
                'ws_enabled': False, 'ws_token': '', 'native_enabled': False,
                'qq_managed_login': False, 'qq_login_revision': secrets.token_hex(16),
                'bridge_token': secrets.token_urlsafe(32), 'native_token': secrets.token_urlsafe(32)
            }
            self.store.db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                      [(key, json.dumps(value)) for key, value in values.items()])
            self.login = NapCatLogin()
            self.connector.update(status='disabled', account=None, groups=[], message='已退出 QQmanager')
            self.connector.refresh_requested.set()

    def _poll(self, body):
        # The component is pinned and its local port bindings and data mounts have been verified.
        args = {'refresh': body.get('refresh') is True}
        if not self.login.credential or self.login.expires <= time.monotonic():
            args.update(token=self.runtime.keys['webui_token'], otp=body.get('otp', ''))
        result = self.login._update(args)
        if result['state'] == 'authorize':
            if 'token' not in args:
                return {'state': 'waiting'}
            return {'state': 'failed', 'message': '登录组件授权失败，请检查本机登录配置后重试。'}
        if result['state'] != 'online':
            return result
        info = _post('QQLogin/GetQQLoginInfo', {}, self.login.credential)
        account = str((info.get('data') or {}).get('uin') or '')
        if info['code'] != 0 or not account.isascii() or not account.isdigit() or not 5 <= len(account) <= 20:
            return {'state': 'waiting_account'}
        store = self.store
        with store.lock, store.db:
            previous = store.setting('ws_account')
            if previous and previous != account:
                return {'state': 'failed', 'message': '扫码账号与当前资料库已绑定的 QQ 不同，请使用原账号。'}
            if not previous and store.setting('qq_login_revision') and body.get('confirm_account') != account:
                return {'state': 'confirm_account', 'account': account}
            self.runtime.remember_account(account)
            settings = {'ws_url': 'ws://127.0.0.1:3001', 'ws_token': self.runtime.keys['onebot_token'],
                        'ws_enabled': True, 'ws_account': account, 'qq_managed_login': True}
            if not previous:
                settings.update(bridge_groups=[], bridge_enabled=False)
            for key, value in settings.items():
                if store.setting(key) != value:
                    store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        self.connector.refresh_requested.set()
        return {'state': 'bound', 'account': account, 'select_groups': not bool(store.setting('bridge_groups'))}
