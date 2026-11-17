import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .store import Store
from .connector import Connector, validate_url
from .llm import Analyzer, save_config, test_connection
from .native import NativeBridge

ROOT = Path(__file__).resolve().parent.parent


def bridge_authorized(headers, raw, token):
    bearer = headers.get('Authorization', '')
    signature = headers.get('X-Signature', '')
    expected = 'sha1=' + hmac.new(token.encode(), raw, hashlib.sha1).hexdigest()
    return hmac.compare_digest(bearer, 'Bearer ' + token) or hmac.compare_digest(signature, expected)


def native_notify(title):
    if sys.platform != 'darwin':
        return '系统通知目前仅支持 macOS；请使用浏览器通知或站内提醒'
    # Pass untrusted text as argv, never interpolate it into AppleScript source.
    script = 'on run argv\ndisplay notification (item 1 of argv) with title "QQmanager · 待办提醒"\nend run'
    try:
        subprocess.run(['osascript', '-e', script, title[:180]], check=True, capture_output=True, timeout=10)
        return ''
    except (OSError, subprocess.SubprocessError):
        return '系统通知发送失败；提醒已保留在站内，请检查系统通知权限'


def workers(store, stop):
    def organize():
        from .matters import organize_step
        while not stop.is_set():
            try:organize_step(store)
            except Exception:pass
            stop.wait(10)
    threading.Thread(target=organize,daemon=True).start()
    def reschedule():
        from .reschedule import recognize_step
        while not stop.is_set():
            try:recognize_step(store)
            except Exception:pass
            stop.wait(15)
    threading.Thread(target=reschedule,daemon=True).start()
    def exams():
        from .exams import recognize_step
        while not stop.is_set():
            try:recognize_step(store)
            except Exception:pass
            stop.wait(20)
    threading.Thread(target=exams,daemon=True).start()
    def attachments():
        from .media import step
        while not stop.is_set():
            try:step(store)
            except Exception:pass
            stop.wait(2)
    threading.Thread(target=attachments,daemon=True).start()
    def reminders():
        while not stop.is_set():
            try:
                due = store.due_notifications()
                if due and store.setting('native_notifications'):
                    error = native_notify('；'.join(x['title'] for x in due[:3]))
                    store.set_setting('notification_error', error)
            except Exception as exc:
                print('Reminder worker:', type(exc).__name__, file=sys.stderr)
            stop.wait(5)

    def watches():
        while not stop.is_set():
            store.scan_watches()
            stop.wait(10)

    for target in (reminders, watches, lambda: Analyzer(store).run(stop)):
        threading.Thread(target=target, daemon=True).start()


def make_server(store, port=8765, connector=None):
    csrf = secrets.token_urlsafe(32)
    connector = connector or Connector(store)
    native = NativeBridge(store)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log URLs, which may contain private search terms.
            pass

        def send(self, data, status=200, content_type='application/json; charset=utf-8', headers=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.send({'error': '不允许的 Host'}, 403)
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + x for x in allowed}:
                self.send({'error': '不允许跨站访问'}, 403)
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.send({'error': '不允许跨站访问'}, 403)
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            url = urlparse(self.path)
            try:
                if url.path == '/api/session':
                    self.send({'csrf': csrf})
                elif url.path == '/api/health':
                    self.send({'app': 'QQmanager', 'protocol': 1})
                elif url.path == '/api/desktop':
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                        self.send({'error': '会话无效'}, 403)
                        return
                    self.send({'data_directory': str(store.directory)})
                elif url.path == '/api/state':
                    q = parse_qs(url.query)
                    self.send(store.snapshot(q.get('q', [''])[0], q.get('group', [''])[0]))
                elif url.path == '/api/overview':
                    from .overview import snapshot
                    params=parse_qs(url.query)
                    self.send(snapshot(store,params.get('date',[None])[0],params.get('span',['day'])[0],params.get('offset',[0])[0]))
                elif url.path.startswith('/api/message/'):
                    from .media import for_message
                    mid=int(url.path.rsplit('/',1)[1]);rows=store.query('SELECT * FROM messages WHERE id=?',(mid,))
                    if not rows:self.send({'error':'消息不存在'},404);return
                    self.send(dict(rows[0],attachments=for_message(store,mid)))
                elif url.path == '/api/connector':
                    self.send(dict(connector.snapshot(), enabled=store.setting('ws_enabled'), url=store.setting('ws_url'),
                                   configured=bool(store.setting('ws_token')), selected=store.setting('bridge_groups'),
                                   selected_account=store.setting('ws_account'), collecting=store.setting('bridge_enabled')))
                elif url.path == '/api/native':
                    self.send(native.snapshot())
                elif url.path == '/api/planner':
                    from .planner import snapshot
                    params=parse_qs(url.query)
                    self.send(snapshot(store,params.get('date',[None])[0],params.get('span',['week'])[0]))
                elif url.path.startswith('/api/planner/change/'):
                    from .reschedule import review
                    self.send(review(store,int(url.path.rsplit('/',1)[1])))
                elif url.path.startswith('/api/matter/'):
                    from .matters import detail
                    self.send(detail(store,int(url.path.rsplit('/',1)[1])))
                elif url.path == '/api/bridge':
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                        self.send({'error': '会话无效'}, 403)
                        return
                    self.send({'token': store.setting('bridge_token'), 'url': f'http://127.0.0.1:{self.server.server_port}/api/onebot'})
                elif url.path.startswith('/api/file/'):
                    rows=store.query('SELECT id,name,body,parse_status,size,(SELECT kind FROM attachments WHERE file_id=files.id LIMIT 1) kind FROM files WHERE id=?',(int(url.path.rsplit('/',1)[1]),))
                    if not rows:self.send({'error':'文件不存在'},404);return
                    self.send(rows[0])
                elif url.path.startswith('/api/preview/'):
                    rows=store.query('SELECT * FROM files WHERE id=?',(int(url.path.rsplit('/',1)[1]),))
                    if not rows or rows[0]['digest'].startswith('remote:'):self.send({'error':'尚未取得附件'},404);return
                    row=rows[0];data=(store.blobs/row['digest']).read_bytes()
                    # Sniff only inert preview formats; never serve uploaded HTML/SVG as active content.
                    typ='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else 'image/gif' if data.startswith((b'GIF87a',b'GIF89a')) else 'image/webp' if data.startswith(b'RIFF') and data[8:12]==b'WEBP' else 'application/pdf' if data.startswith(b'%PDF-') else None
                    if not typ:self.send({'error':'此格式请查看提取正文或下载原文件'},415);return
                    self.send(data,content_type=typ,headers={'Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})
                elif url.path.startswith('/api/download/'):
                    rows = store.query('SELECT * FROM files WHERE id=?', (int(url.path.split('/')[-1]),))
                    if not rows or rows[0]['digest'].startswith('remote:'):
                        self.send({'error': '尚未取得文件内容'}, 404)
                        return
                    row = rows[0]
                    self.send((store.blobs / row['digest']).read_bytes(), content_type='application/octet-stream', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['name'])})
                elif url.path in ('/', '/app.js', '/filter-model.js', '/planner-model.js', '/planner.js', '/style.css'):
                    file = ROOT / 'web' / ('index.html' if url.path == '/' else url.path[1:])
                    self.send(file.read_bytes(), content_type=mimetypes.guess_type(str(file))[0] + '; charset=utf-8')
                else:
                    self.send({'error': '未找到'}, 404)
            except (ValueError, OSError):
                self.send({'error': '请求无效或文件不可访问'}, 400)

        def do_POST(self):
            if not self.local_request():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 29 * 1024 * 1024:
                    self.send({'error': '请求过大，单文件最多 20 MB'}, 413)
                    return
                native_request = self.path in ('/api/native/heartbeat', '/api/native/events','/api/native/attachment')
                if native_request and self.path!='/api/native/attachment' and size > 1024 * 1024:
                    raise ValueError('插件批次过大')
                if self.path != '/api/onebot' and not native_request and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                    self.send({'error': '会话已失效，请刷新页面'}, 403)
                    return
                raw = self.rfile.read(size)
                if native_request and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + store.setting('native_token')):
                    self.send({'error':'插件配对凭据无效'},401)
                    return
                if self.path == '/api/onebot' and not bridge_authorized(self.headers, raw, store.setting('bridge_token')):
                    self.send({'error': 'OneBot 凭据无效'}, 401)
                    return
                body = json.loads(raw or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('需要 JSON 对象')
                result = {'ok': True}
                if self.path == '/api/native/heartbeat':
                    result = native.heartbeat(body)
                elif self.path == '/api/native/events':
                    result = native.events(body)
                elif self.path == '/api/native/attachment':
                    from .media import receive
                    result=receive(store,body)
                elif self.path == '/api/native/media/refresh':
                    result=native.refresh_media()
                elif self.path == '/api/attachment/retry':
                    with store.lock,store.db:
                        store.db.execute("UPDATE attachments SET status='pending',error='' WHERE id=? AND file_id IS NOT NULL",(int(body['id']),))
                elif self.path == '/api/native/settings':
                    store.set_setting('native_enabled', bool(body.get('enabled')))
                elif self.path == '/api/native/groups':
                    result = native.select_groups(body.get('groups'), body.get('names'))
                elif self.path == '/api/native/groups/refresh':
                    result = native.refresh_groups()
                elif self.path == '/api/native/pair':
                    account = str(body.get('account') or store.setting('ws_account'))
                    groups = body.get('groups', store.setting('bridge_groups'))
                    if not account.isdigit() or not 5 <= len(account) <= 20:
                        raise ValueError('请先指定要绑定的QQ账号')
                    if not isinstance(groups,list) or len(groups)>1000 or any(not str(g).isascii() or not str(g).isdigit() or not 5 <= len(str(g)) <= 20 for g in groups):
                        raise ValueError('群号应为 5–20 位数字')
                    with store.lock, store.db:
                        store.set_setting('ws_account', account)
                        store.set_setting('bridge_groups', list(dict.fromkeys(str(g) for g in groups)))
                        store.set_setting('bridge_enabled', bool(groups))
                        store.set_setting('native_enabled', True)
                    result = {'url':f'http://127.0.0.1:{self.server.server_port}/', 'token':store.setting('native_token'),
                              'account':account, 'groups':store.setting('bridge_groups')}
                elif self.path == '/api/import':
                    if 'text' in body:
                        data = str(body['text']).encode()
                    else:
                        data = base64.b64decode(body.get('data', ''), validate=True)
                    result = store.import_data(str(body.get('name', '聊天记录.txt')), data, body.get('group_name'), body.get('mode', 'chat'))
                elif self.path == '/api/tasks':
                    result = {'id': store.create_task(body)}
                elif self.path == '/api/matter':
                    from .matters import update
                    update(store,body)
                elif self.path == '/api/planner/calendar':
                    from .planner import save_config as save_calendar
                    save_calendar(store,body)
                elif self.path == '/api/planner/change/confirm':
                    from .reschedule import apply
                    result=apply(store,body)
                elif self.path == '/api/planner/change/action':
                    from .reschedule import action
                    action(store,body)
                elif self.path == '/api/planner/course':
                    from .planner import save_course
                    result={'id':save_course(store,body)}
                elif self.path == '/api/planner/course/delete':
                    with store.lock,store.db:
                        store.db.execute('UPDATE courses SET deleted=1 WHERE id=?',(int(body['id']),))
                elif self.path == '/api/planner/course/restore':
                    with store.lock,store.db:
                        store.db.execute('UPDATE courses SET deleted=0 WHERE id=?',(int(body['id']),))
                elif self.path == '/api/planner/import':
                    from .planner import import_draft
                    if body.get('allow_model') is not True:raise ValueError('请先确认允许将识别文字发送给已配置的模型')
                    result=import_draft(store,str(body.get('name','课表.txt')),base64.b64decode(body.get('data',''),validate=True),body.get('kind','courses'))
                elif self.path == '/api/planner/apply':
                    from .planner import apply_import
                    result=apply_import(store,body)
                elif self.path.startswith('/api/tasks/'):
                    store.update_task(int(self.path.rsplit('/', 1)[1]), body)
                elif self.path == '/api/insight':
                    if body.get('status') not in ('new', 'read', 'saved', 'dismissed'):
                        raise ValueError('无效状态')
                    with store.lock, store.db:
                        store.update_insight(int(body['id']),body['status'])
                elif self.path == '/api/settings':
                    if 'bridge_groups' in body:
                        groups = body['bridge_groups']
                        if not isinstance(groups, list) or any(not str(x).isdigit() for x in groups):
                            raise ValueError('请输入以逗号分隔的数字群号')
                        store.set_setting('bridge_groups', [str(x) for x in groups])
                    for key in ('bridge_enabled', 'native_notifications'):
                        if key in body:
                            store.set_setting(key, bool(body[key]))
                    if 'interests' in body:
                        store.set_setting('interests', str(body['interests'])[:1000])
                        store.reanalyze()
                elif self.path == '/api/llm/review':
                    from .llm import queue_review
                    result=queue_review(store)
                elif self.path == '/api/llm/config':
                    save_config(store, body)
                elif self.path == '/api/llm/test':
                    result = test_connection(store, bool(body.get('enable', True)))
                elif self.path == '/api/llm/pause':
                    with store.lock, store.db:
                        config = store.setting('llm_config')
                        config['enabled'] = False
                        store.set_setting('llm_config', config)
                elif self.path == '/api/llm/retry':
                    with store.lock, store.db:
                        store.db.execute("UPDATE analysis_jobs SET state='pending',attempts=0,next_at=0,error='' WHERE state='failed'")
                elif self.path == '/api/connector/settings':
                    url = validate_url(str(body.get('url', store.setting('ws_url'))))
                    token = str(body.get('token') or store.setting('ws_token'))
                    if body.get('enabled') and not token:
                        raise ValueError('请填写 NapCat WebSocket Token')
                    if '\r' in token or '\n' in token:
                        raise ValueError('凭据不能包含换行')
                    store.set_setting('ws_url', url)
                    store.set_setting('ws_token', token)
                    store.set_setting('ws_enabled', bool(body.get('enabled')))
                    connector.refresh_requested.set()
                elif self.path == '/api/connector/refresh':
                    connector.refresh_requested.set()
                elif self.path == '/api/connector/groups':
                    connector.select_groups(body.get('groups'))
                elif self.path == '/api/watches':
                    store.add_watch(body)
                elif self.path == '/api/watch-toggle':
                    with store.lock, store.db:
                        store.db.execute('UPDATE watches SET enabled=? WHERE id=?', (int(bool(body['enabled'])), int(body['id'])))
                elif self.path == '/api/notifications/read':
                    with store.lock, store.db:
                        store.db.execute('UPDATE notifications SET seen=1 WHERE id=?', (int(body['id']),))
                elif self.path == '/api/onebot':
                    result = store.onebot(body)
                else:
                    self.send({'error': '未找到'}, 404)
                    return
                self.send(result)
            except sqlite3.IntegrityError:
                self.send({'error': '该待办或文件夹已存在，或来源已失效'}, 409)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception:
                self.send({'error': '处理失败，请检查数据格式或本地磁盘空间'}, 500)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='QQmanager 本地信息助手')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=str(ROOT / 'data'))
    args = parser.parse_args()
    store = Store(args.data_dir)
    stop = threading.Event()
    connector = Connector(store)
    server = make_server(store, args.port, connector)
    workers(store, stop)
    threading.Thread(target=connector.run, args=(stop,), daemon=True).start()
    print(f'QQmanager 已启动：http://127.0.0.1:{server.server_port}', flush=True)
    print('本地保存原文。启用模型后向所配置接口发送选定群的消息片段。Ctrl+C 退出。', flush=True)
    if os.environ.get('QQMANAGER_DESKTOP') == '1':
        # EOF reliably stops a child when the desktop quits or crashes, on both OSes.
        def parent_watch():
            sys.stdin.buffer.read()
            stop.set()
            server.shutdown()
        threading.Thread(target=parent_watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
