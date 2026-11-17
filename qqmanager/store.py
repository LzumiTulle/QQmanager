import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .analysis import analyze, timestamp
from .quality import noise_reason, merge_notices
from .importers import MAX_FILE, WATCH_EXT, extract_file, normalize, parse_chat, segments


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.directory / 'files'
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.directory / 'qqmanager.db'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, group_name TEXT, sender TEXT,
              sent_at TEXT, content TEXT, source TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS insights (
              id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE REFERENCES messages(id),
              kind TEXT, title TEXT, score INTEGER, reason TEXT, due TEXT, date_note TEXT,
              status TEXT DEFAULT 'new');
            CREATE TABLE IF NOT EXISTS files (
              id INTEGER PRIMARY KEY, digest TEXT, name TEXT, group_name TEXT, size INTEGER,
              body TEXT, parse_status TEXT, source TEXT, created REAL,
              UNIQUE(digest, name, group_name));
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY, insight_id INTEGER UNIQUE REFERENCES insights(id),
              title TEXT, due TEXT, remind_at REAL, status TEXT DEFAULT 'open',
              notified INTEGER DEFAULT 0, created REAL);
            CREATE TABLE IF NOT EXISTS notifications (
              id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), title TEXT,
              created REAL, seen INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS watches (
              id INTEGER PRIMARY KEY, path TEXT UNIQUE, group_name TEXT, mode TEXT,
              enabled INTEGER DEFAULT 1, last_scan REAL, error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS watched_files (
              watch_id INTEGER, path TEXT, digest TEXT, error TEXT DEFAULT '',
              PRIMARY KEY(watch_id,path));
        ''')
        defaults = {'interests': '', 'bridge_enabled': False, 'bridge_groups': [],
                    'bridge_token': secrets.token_urlsafe(32), 'last_event': None,
                    'native_notifications': False, 'notification_error': '',
                    'ws_enabled': False, 'ws_url': 'ws://127.0.0.1:3001', 'ws_token': '', 'ws_account': '',
                    'native_enabled': False, 'native_token': secrets.token_urlsafe(32)}
        for key, value in defaults.items():
            self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()
        from .llm import migrate
        migrate(self)
        from .media import migrate as migrate_media
        migrate_media(self)
        from .planner import migrate as migrate_planner
        from .matters import migrate as migrate_matters
        migrate_planner(self)
        migrate_matters(self)
        from .reschedule import migrate as migrate_reschedule
        migrate_reschedule(self)

    def setting(self, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def add_messages(self, rows, source):
        if len(rows) > 50000:
            raise ValueError('单次最多 50,000 条消息，请拆分记录')
        added = important = 0
        with self.lock, self.db:
            interests = self.setting('interests')
            for row in rows:
                identity = {'onebot': row['_identity']} if '_identity' in row else row
                key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                cur = self.db.execute('INSERT OR IGNORE INTO messages(fingerprint,group_name,sender,sent_at,content,source,created) VALUES (?,?,?,?,?,?,?)',
                                      (key, row['group_name'], row['sender'], row['sent_at'], row['content'], source, time.time()))
                if not cur.rowcount:
                    continue
                added += 1
                message_id = cur.lastrowid
                if row.get('_live_group'):
                    self.db.execute('INSERT OR IGNORE INTO analysis_jobs(message_id,group_id,account,state,created) VALUES (?,?,?,?,?)',
                                    (message_id, row['_live_group'], row.get('_account', ''), 'pending', time.time()))
                insight = analyze(row['content'], row['sent_at'], interests)
                if insight:
                    important += 1
                    self.db.execute('INSERT INTO insights(message_id,kind,title,score,reason,due,date_note) VALUES (?,?,?,?,?,?,?)',
                                    (message_id, insight['kind'], insight['title'], insight['score'], insight['reason'], insight['due'], insight['date_note']))
        return {'messages': added, 'duplicates': len(rows) - added, 'insights': important}

    def reanalyze(self):
        with self.lock, self.db:
            for row in self.db.execute('SELECT * FROM messages').fetchall():
                filtered=noise_reason(row['content'])
                if filtered:
                    self.db.execute('UPDATE insights SET quality_reason=? WHERE message_id=?',(filtered,row['id']))
                    continue
                if self.db.execute("SELECT 1 FROM analysis_jobs WHERE message_id=? AND state='done'", (row['id'],)).fetchone():
                    continue
                item = analyze(row['content'], row['sent_at'], self.setting('interests'))
                if not item:
                    self.db.execute("UPDATE insights SET quality_reason='不符合重要性门槛' WHERE message_id=? AND engine='rule' AND status='new' AND id NOT IN (SELECT insight_id FROM tasks WHERE insight_id IS NOT NULL)",(row['id'],))
                if item:
                    self.db.execute('''INSERT INTO insights(message_id,kind,title,score,reason,due,date_note) VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(message_id,item_key) DO UPDATE SET kind=excluded.kind, title=excluded.title,
                        score=excluded.score, reason=excluded.reason, due=excluded.due, date_note=excluded.date_note, quality_reason='' ''',
                        (row['id'], item['kind'], item['title'], item['score'], item['reason'], item['due'], item['date_note']))

    def import_data(self, name, data, group, mode, source=None):
        if not data:
            raise ValueError('文件为空')
        if len(data) > MAX_FILE:
            raise ValueError('单个文件最多 20 MB')
        name = Path(name.replace('\\', '/')).name[:250] or '未命名.txt'
        group = str(group or '未分组').strip()[:100]
        if mode == 'chat':
            if Path(name).suffix.lower() not in {'.txt', '.json', '.csv', '.log', '.md'}:
                raise ValueError('聊天记录支持 TXT、JSON、CSV、LOG、MD')
            return self.add_messages(parse_chat(name, data, group), source or name)
        if mode != 'file':
            raise ValueError('导入类型无效')
        digest = hashlib.sha256(data).hexdigest()
        try:
            body, status = extract_file(name, data)
        except Exception:
            body, status = '', '仅文件名：正文解析失败，请检查文件格式'
        path = self.blobs / digest
        if not path.exists():
            path.write_bytes(data)
            path.chmod(0o600)
        with self.lock, self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO files(digest,name,group_name,size,body,parse_status,source,created) VALUES (?,?,?,?,?,?,?,?)',
                                  (digest, name, group, len(data), body, status, source or name, time.time()))
        return {'files': cur.rowcount, 'duplicates': 0 if cur.rowcount else 1, 'parse_status': status}

    def query(self, sql, args=()):
        with self.lock:
            return [dict(x) for x in self.db.execute(sql, args).fetchall()]

    def search(self, table, query='', group=''):
        if table == 'insights':
            sql = "SELECT i.*, m.group_name,m.sender,m.sent_at,m.content,m.source,j.group_id live_group,j.account live_account FROM insights i JOIN messages m ON m.id=i.message_id LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE i.quality_reason=''"
            fields, order = ['i.title', 'm.content', 'm.sender'], 'i.score DESC, i.id DESC'
        elif table == 'files':
            sql = 'SELECT id,name,group_name,size,parse_status,source,created,substr(body,1,350) excerpt FROM files WHERE 1=1'
            fields, order = ['name', 'body'], 'id DESC'
        elif table == 'messages':
            sql = 'SELECT m.*,j.group_id live_group,j.account live_account,j.state analysis_state,j.error analysis_error,j.filter_reason analysis_reason FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE 1=1'
            fields, order = ['m.content', 'm.sender'], 'm.id DESC'
        else:
            raise ValueError('未知搜索类型')
        args = []
        if group:
            sql += ' AND group_name=?'
            args.append(group)
        for term in query.split()[:10]:
            sql += ' AND (' + ' OR '.join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ')'
            escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + escaped + '%'] * len(fields))
        # Bounded UI response; raw records remain in SQLite.
        rows=self.query(sql + ' ORDER BY ' + order + ' LIMIT 500', args)
        if table in ('messages','insights'):
            from .media import for_message
            for row in rows:
                row['attachments']=for_message(self,row['message_id'] if table=='insights' else row['id'])
        return merge_notices(rows) if table=='insights' else rows

    def update_insight(self, insight_id, status):
        with self.lock,self.db:
            ids=next((r['merged_ids'] for r in self.search('insights') if insight_id in r['merged_ids']),[insight_id])
            self.db.executemany('UPDATE insights SET status=? WHERE id=?',[(status,i) for i in ids])


    def create_task(self, body):
        title = str(body.get('title', '')).strip()[:300]
        if not title:
            raise ValueError('请填写待办标题')
        dt = timestamp(body.get('due'))
        if body.get('due') and not dt:
            raise ValueError('提醒时间无效')
        with self.lock, self.db:
            cur = self.db.execute('INSERT INTO tasks(insight_id,title,due,remind_at,created) VALUES (?,?,?,?,?)',
                                  (body.get('insight_id') or None, title, dt.isoformat() if dt else None, dt.timestamp() if dt else None, time.time()))
            return cur.lastrowid

    def update_task(self, task_id, body):
        with self.lock, self.db:
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ValueError('待办不存在')
            status = body.get('status', row['status'])
            if status not in ('open', 'done'):
                raise ValueError('状态无效')
            due = row['due']
            remind_at = row['remind_at']
            notified = row['notified']
            title = str(body.get('title', row['title'])).strip()[:300]
            if not title:
                raise ValueError('标题不能为空')
            if 'due' in body:
                dt = timestamp(body['due'])
                if body['due'] and not dt:
                    raise ValueError('时间无效')
                due, remind_at, notified = (dt.isoformat(), dt.timestamp(), 0) if dt else (None, None, 0)
            if body.get('snooze'):
                remind_at, notified = time.time() + 1800, 0
            if status == 'open' and row['status'] == 'done':
                notified = 0
            self.db.execute('UPDATE tasks SET title=?,due=?,remind_at=?,notified=?,status=? WHERE id=?', (title, due, remind_at, notified, status, task_id))
            if status == 'done' or 'due' in body or body.get('snooze'):
                self.db.execute('UPDATE notifications SET seen=1 WHERE task_id=?', (task_id,))

    def due_notifications(self, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            rows = self.db.execute("SELECT * FROM tasks WHERE status='open' AND notified=0 AND remind_at<=?", (now,)).fetchall()
            for row in rows:
                self.db.execute('INSERT INTO notifications(task_id,title,created) VALUES (?,?,?)', (row['id'], row['title'], now))
                self.db.execute('UPDATE tasks SET notified=1 WHERE id=?', (row['id'],))
        return [dict(x) for x in rows]

    def add_watch(self, body):
        path = Path(str(body.get('path', ''))).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('文件夹不存在；请输入本机文件夹的完整路径')
        if path == self.directory or self.directory in path.parents or path in self.directory.parents:
            raise ValueError('请选择独立的资料文件夹，不要包含软件数据目录')
        if body.get('mode') not in ('chat', 'file'):
            raise ValueError('监听类型无效')
        with self.lock, self.db:
            self.db.execute('INSERT INTO watches(path,group_name,mode) VALUES (?,?,?)', (str(path), str(body.get('group_name') or '未分组')[:100], body['mode']))

    def scan_watches(self):
        for watch in self.query('SELECT * FROM watches WHERE enabled=1'):
            errors, count = [], 0
            path = Path(watch['path'])
            try:
                if not path.is_dir():
                    raise ValueError('文件夹不可访问')
                # Deliberately nonrecursive: avoids unexpectedly ingesting a user's entire disk.
                for file in sorted(path.iterdir()):
                    if file.is_symlink() or not file.is_file() or file.suffix.lower() not in WATCH_EXT:
                        continue
                    if watch['mode'] == 'chat' and file.suffix.lower() not in {'.txt', '.json', '.csv', '.log', '.md'}:
                        continue
                    count += 1
                    if count > 1000:
                        errors.append('目录超过 1,000 个支持的文件；请拆分目录')
                        break
                    stat = file.stat()
                    if stat.st_size > MAX_FILE:
                        errors.append(file.name + '：超过 20 MB')
                        continue
                    if time.time() - stat.st_mtime < 2:
                        continue
                    data = file.read_bytes()
                    after = file.stat()
                    if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                        continue
                    digest = hashlib.sha256(data).hexdigest()
                    old = self.query('SELECT digest,error FROM watched_files WHERE watch_id=? AND path=?', (watch['id'], str(file)))
                    if old and old[0]['digest'] == digest:
                        if old[0]['error']:
                            errors.append(file.name + '：' + old[0]['error'])
                        continue
                    error = ''
                    try:
                        self.import_data(file.name, data, watch['group_name'], watch['mode'], '文件夹：' + str(file))
                    except Exception as exc:
                        error = str(exc)
                        errors.append(file.name + '：' + error)
                    with self.lock, self.db:
                        self.db.execute('INSERT OR REPLACE INTO watched_files VALUES (?,?,?,?)', (watch['id'], str(file), digest, error))
            except Exception as exc:
                errors.append(str(exc))
            with self.lock, self.db:
                self.db.execute('UPDATE watches SET last_scan=?,error=? WHERE id=?', (time.time(), '\n'.join(errors)[:2000], watch['id']))

    def onebot(self, event, source='OneBot 实时消息'):
        if not self.setting('bridge_enabled'):
            raise ValueError('自动接入尚未启用')
        if event.get('post_type') == 'meta_event':
            return {'ignored': 'meta_event'}
        group = str(event.get('group_id', ''))
        if not group or group not in self.setting('bridge_groups'):
            return {'ignored': '群不在接入列表'}
        if event.get('post_type') in ('message', 'message_sent') and event.get('message_type') == 'group':
            row = normalize(event, group)
            row['_live_group'], row['_account'] = group, str(event.get('self_id', ''))
            if event.get('message_id') is not None:
                # Stable across nickname/group-name changes and reconnection replays.
                row['_identity'] = [str(event.get('self_id', '')), group, str(event['message_id'])]
            result = self.add_messages([row], source)
        elif event.get('notice_type') == 'group_upload':
            file = event.get('file') or {}
            name = str(file.get('name', '未知文件'))
            digest = 'remote:' + hashlib.sha256((group + ':' + str(file.get('id') or name)).encode()).hexdigest()
            with self.lock, self.db:
                self.db.execute('INSERT OR IGNORE INTO files(digest,name,group_name,size,body,parse_status,source,created) VALUES (?,?,?,?,?,?,?,?)',
                                (digest, name, str(event.get('group_name') or group), int(file.get('size') or 0), '', '待获取：仅收到群文件信息，未下载正文', 'OneBot 群文件事件', time.time()))
            self.add_messages([{'group_name': str(event.get('group_name') or group),
                                'sender': str(event.get('user_id') or '群成员'),
                                'sent_at': timestamp(event.get('time')).isoformat() if timestamp(event.get('time')) else None,
                                'content': '[群文件上传事件] ' + name + '\n仅收到文件名称，未获取正文。',
                                '_identity': [str(event.get('self_id', '')), group, digest],
                                '_live_group': group, '_account': str(event.get('self_id', ''))}], 'OneBot 群文件事件（非聊天原文）')
            result = {'file_event': True}
        else:
            return {'ignored': '不支持的事件类型'}
        self.set_setting('last_event', time.time())
        return result

    def snapshot(self, query='', group=''):
        from .llm import status
        from .matters import list_matters
        with self.lock:
            counts = {t: self.db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('messages', 'files', 'insights')}
            counts['insights'] = len(self.search('insights'))
            counts['tasks'] = self.db.execute("SELECT count(*) FROM tasks WHERE status='open'").fetchone()[0]
            matters=list_matters(self,query,group)
            counts['matters']=len(list_matters(self))
            history_groups = [x['group_name'] for x in self.query("SELECT group_name FROM messages UNION SELECT group_name FROM files WHERE group_name!='个人课表' ORDER BY group_name")]
            from .native import NativeBridge
            active_groups = [g['group_name'] for g in NativeBridge(self).group_catalog() if g['selected']]
            # A configured collector owns the normal filter; historical records remain accessible.
            groups = active_groups if self.setting('ws_account') else history_groups
            return {'matters':matters,'matter_error':self.setting('matter_error') or '', 'llm': status(self), 'counts': counts, 'insights': self.search('insights', query, group), 'files': self.search('files', query, group),
                    'messages': self.search('messages', query, group),
                    'tasks': self.query('SELECT t.*, m.group_name, m.content, m.sender, m.sent_at, i.evidence, i.model FROM tasks t LEFT JOIN insights i ON i.id=t.insight_id LEFT JOIN messages m ON m.id=i.message_id ORDER BY t.status DESC,t.remind_at IS NULL,t.remind_at,t.id DESC'),
                    'groups': groups, 'history_groups': history_groups,
                    'notifications': self.query('SELECT * FROM notifications WHERE seen=0 ORDER BY id DESC'),
                    'watches': self.query('SELECT * FROM watches'),
                    'settings': {k: self.setting(k) for k in ('interests', 'bridge_enabled', 'bridge_groups', 'last_event', 'native_notifications', 'notification_error')}}
