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
            CREATE INDEX IF NOT EXISTS messages_sent_order ON messages(julianday(sent_at) DESC, id DESC);
            CREATE TABLE IF NOT EXISTS insights (
              id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE REFERENCES messages(id),
              kind TEXT, title TEXT, score INTEGER, reason TEXT, due TEXT, date_note TEXT,
              status TEXT DEFAULT 'new');
            CREATE TABLE IF NOT EXISTS files (
              id INTEGER PRIMARY KEY, digest TEXT, name TEXT, group_name TEXT, size INTEGER,
              body TEXT, parse_status TEXT, source TEXT, created REAL,
              UNIQUE(digest, name, group_name));
            CREATE INDEX IF NOT EXISTS files_created_order ON files(created DESC, id DESC);
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY, insight_id INTEGER UNIQUE REFERENCES insights(id),
              title TEXT, due TEXT, remind_at REAL, status TEXT DEFAULT 'open',
              notified INTEGER DEFAULT 0, created REAL);
            CREATE TABLE IF NOT EXISTS notifications (
              id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), title TEXT,
              created REAL, seen INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS backfill_receipts (
              message_id INTEGER PRIMARY KEY REFERENCES messages(id), seen INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS unread_messages (
              message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS connector_history (
              account TEXT, group_id TEXT, grant_id TEXT, since INTEGER NOT NULL, cursor INTEGER DEFAULT 0, checked REAL,
              PRIMARY KEY(account,group_id));
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
        from .workflow import migrate as migrate_workflow
        migrate_workflow(self)
        from .exams import migrate as migrate_exams
        from .identity import migrate as migrate_identity
        migrate_identity(self)
        migrate_exams(self)
        from .native import migrate_history
        migrate_history(self)
        self.db.executescript("""CREATE VIEW IF NOT EXISTS current_unread AS
            SELECT u.message_id FROM (
                SELECT message_id FROM unread_messages
                UNION SELECT message_id FROM backfill_receipts WHERE seen=0
            ) u LEFT JOIN analysis_jobs j USING(message_id)
            WHERE j.message_id IS NULL OR j.account=(SELECT json_extract(value,'$') FROM settings WHERE key='ws_account')
        """)
        for operation in ('INSERT','UPDATE'):
            self.db.executescript(f"""CREATE TRIGGER IF NOT EXISTS revoke_connector_history_{operation}
          AFTER {operation} ON settings WHEN NEW.key IN ('ws_account','bridge_groups','bridge_enabled','ws_enabled','qq_managed_login','native_enabled')
          BEGIN UPDATE connector_history SET grant_id=NULL WHERE grant_id IS NOT NULL AND (
            account!=(SELECT json_extract(value,'$') FROM settings WHERE key='ws_account')
            OR group_id NOT IN (SELECT CAST(g.value AS TEXT) FROM settings s,json_each(s.value) g WHERE s.key='bridge_groups')
            OR EXISTS(SELECT 1 FROM settings WHERE key IN ('bridge_enabled','ws_enabled','qq_managed_login') AND value='false')
            OR EXISTS(SELECT 1 FROM settings WHERE key='native_enabled' AND value='true'));
          END;""")
        # Older managed profiles may already have an active group selection.
        if (self.setting('qq_managed_login') and self.setting('bridge_enabled') and self.setting('ws_enabled')
                and self.setting('ws_account') and not self.setting('native_enabled')):
            from .connector import seed_history
            with self.db:
                seed_history(self,self.setting('ws_account'),self.setting('bridge_groups'),{})

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
                self.db.execute('INSERT INTO unread_messages VALUES (?)', (message_id,))
                if row.get('_backfill') and row.get('_live_group'):
                    self.db.execute('INSERT INTO backfill_receipts(message_id) VALUES (?)',(message_id,))
                if row.get('_live_group'):
                    self.db.execute('INSERT OR IGNORE INTO analysis_jobs(message_id,group_id,account,state,created) VALUES (?,?,?,?,?)',
                                    (message_id, row['_live_group'], row.get('_account', ''), 'pending', time.time()))
                insight = analyze(row['content'], row['sent_at'], interests)
                if insight:
                    important += 1
                    self.db.execute('INSERT INTO insights(message_id,kind,title,score,reason,due,date_note) VALUES (?,?,?,?,?,?,?)',
                                    (message_id, insight['kind'], insight['title'], insight['score'], insight['reason'], insight['due'], insight['date_note']))
        return {'messages': added, 'duplicates': len(rows) - added, 'insights': important}

    def protected_messages(self, message_id=None):
        """Source messages whose interpretation the user has already handled."""
        sql="""SELECT DISTINCT i.message_id FROM insights i
                    LEFT JOIN matter_members mm ON mm.insight_id=i.id
                    LEFT JOIN matters e ON e.id=mm.matter_id
                    WHERE (i.status!='new' OR mm.manual OR e.manual
                    OR EXISTS(SELECT 1 FROM workflow_decisions w WHERE w.matter_id=e.id)
                    OR EXISTS(SELECT 1 FROM tasks t WHERE t.insight_id=i.id)
                    OR EXISTS(SELECT 1 FROM identity_answers a WHERE a.insight_id=i.id))"""
        args=()
        if message_id is not None:
            sql+=' AND i.message_id=?'
            args=(message_id,)
        return {r['message_id'] for r in self.query(sql,args)}

    def reanalyze(self, *, full=False, review_run=None, progress=None):
        from .media import analysis_source
        with self.lock, self.db:
            interests=self.setting('interests')
            reviewed=queued=0
            protected=set()
            if full:
                protected=self.protected_messages()
            groups=set(self.setting('bridge_groups') or []) if self.setting('bridge_enabled') else set()
            scope="WHERE j.message_id IS NULL OR j.account=?" if full else ""
            args=(self.setting('ws_account'),) if full else ()
            if progress:
                total=self.db.execute(f'SELECT count(*) FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id {scope}',args).fetchone()[0]
                progress(0,total)
            tracked=0
            rows=self.db.execute(f"""SELECT m.id,m.content,m.sent_at,j.group_id,j.state job_state,
                EXISTS(SELECT 1 FROM insights i WHERE i.message_id=m.id AND i.engine!='rule' AND i.quality_reason='') has_analysis
                FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id {scope} ORDER BY m.id""",
                args)
            for row in rows:
                reviewed+=1
                if progress:
                    progress(reviewed-1,total)
                if row['id'] in protected:
                    continue
                if full:
                    if row['group_id'] in groups and row['job_state'] in ('done','filtered','failed','skipped'):
                        self.db.execute("""UPDATE analysis_jobs SET state='pending',attempts=0,next_at=0,
                            error='',filter_reason='' WHERE message_id=?""",(row['id'],))
                        queued+=1
                    if review_run and row['group_id'] in groups:
                        tracked+=self.db.execute("""UPDATE analysis_jobs SET review_run=? WHERE message_id=?
                            AND state IN ('pending','running')""",(review_run,row['id'])).rowcount
                    row=analysis_source(self,row)
                filtered=noise_reason(row['content'])
                if filtered:
                    self.db.execute('UPDATE insights SET quality_reason=? WHERE message_id=?',(filtered,row['id']))
                    continue
                if (not full and row['job_state']=='done') or (full and row['has_analysis']):
                    continue
                item = analyze(row['content'], row['sent_at'], interests)
                if not item:
                    self.db.execute("UPDATE insights SET quality_reason='不符合重要性门槛' WHERE message_id=? AND engine='rule' AND status='new' AND id NOT IN (SELECT insight_id FROM tasks WHERE insight_id IS NOT NULL)",(row['id'],))
                if item:
                    self.db.execute('''INSERT INTO insights(message_id,kind,title,score,reason,due,date_note) VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(message_id,item_key) DO UPDATE SET kind=excluded.kind, title=excluded.title,
                        score=excluded.score, reason=excluded.reason, due=excluded.due, date_note=excluded.date_note, quality_reason='' ''',
                        (row['id'], item['kind'], item['title'], item['score'], item['reason'], item['due'], item['date_note']))
            if progress:
                progress(reviewed,total)
            return {'reviewed':reviewed,'queued':tracked if review_run else queued}

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

    @staticmethod
    def _search_filters(fields, query, group):
        sql, args = "", []
        if group:
            sql += ' AND group_name=?'
            args.append(group)
        for term in query.split()[:10]:
            sql += ' AND (' + ' OR '.join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ')'
            escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + escaped + '%'] * len(fields))
        return sql, args

    @staticmethod
    def _library_date(table):
        if table == 'messages':
            return "date(m.sent_at, '+8 hours')"
        if table == 'files':
            return "date(created, 'unixepoch', '+8 hours')"
        raise ValueError('未知资料类型')

    def library(self, table, query='', group='', day='', offset=0):
        date_expr = self._library_date(table)
        if table == 'messages':
            source = """messages m LEFT JOIN insights i ON i.id=(
                        SELECT s.id FROM insights s WHERE s.message_id=m.id
                        AND s.quality_reason='' AND s.status!='dismissed'
                        ORDER BY s.score DESC,s.id DESC LIMIT 1)"""
            fields, record_id = ['m.content', 'm.sender'], 'm.id'
            hint = "COALESCE(NULLIF(i.title,''),m.content)"
            priority = "CASE WHEN i.title != '' THEN m.id END"
        else:
            source, fields, record_id = 'files', ['name', 'body'], 'id'
            hint, priority = 'name', 'NULL'
        filters, args = self._search_filters(fields, query, group)
        unread = "m.id IN (SELECT message_id FROM current_unread)" if table == "messages" else "0"
        # Select representative records within the same filter as the chart and detail list.
        sql = f"""WITH matching AS (
            SELECT {record_id} id, {date_expr} day, substr({hint},1,72) hint, {priority} preferred, {unread} unread
            FROM {source} WHERE 1=1 {filters}
        ), days AS (
            SELECT day, count(*) count, sum(unread) unread, coalesce(max(preferred),max(id)) sample,
                   coalesce(min(preferred),min(id)) other, min(id) first, max(id) last
            FROM matching GROUP BY day
        )
        SELECT coalesce(d.day,'unknown') day, d.count, d.unread, a.hint preview, b.hint secondary
        FROM days d JOIN matching a ON a.id=d.sample
        JOIN matching b ON b.id=CASE WHEN d.other!=d.sample THEN d.other
                                     WHEN d.last!=d.sample THEN d.last ELSE d.first END
        ORDER BY d.day DESC"""
        with self.lock:
            days = self.query(sql, args)
            count = next((d['count'] for d in days if d['day'] == day), 0)
            size = 50
            offset = min(max(0, offset // size) * size, max(0, count-1) // size * size)
            rows = self.search(table, query, group, day=day, limit=size, offset=offset) if count else []
        return dict(type=table, days=days, day=day, rows=rows, offset=offset, page_size=size)

    def search(self, table, query='', group='', *, day=None, limit=500, offset=0):
        if table == 'insights':
            sql = "SELECT i.*, m.group_name,m.sender,m.sent_at,m.content,m.source,j.group_id live_group,j.account live_account FROM insights i JOIN messages m ON m.id=i.message_id LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE i.quality_reason=''"
            fields, order = ['i.title', 'm.content', 'm.sender'], 'i.score DESC, i.id DESC'
        elif table == 'files':
            sql = 'SELECT id,name,group_name,size,parse_status,source,created,substr(body,1,350) excerpt FROM files WHERE 1=1'
            fields, order = ['name', 'body'], 'created DESC, id DESC'
        elif table == 'messages':
            sql = 'SELECT m.*,m.id IN (SELECT message_id FROM current_unread) unread,j.group_id live_group,j.account live_account,j.state analysis_state,j.error analysis_error,j.filter_reason analysis_reason FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE 1=1'
            fields, order = ['m.content', 'm.sender'], 'julianday(m.sent_at) DESC, m.id DESC'
        else:
            raise ValueError('未知搜索类型')
        filters, args = self._search_filters(fields, query, group)
        sql += filters
        if day is not None:
            sql += ' AND ' + self._library_date(table) + ' IS ?'
            args.append(None if day == 'unknown' else day)
        rows = self.query(sql + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', [*args, limit, offset])
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
        from . import workflow, matters
        with self.lock:
            matters.sync(self)
            iid=body.get('insight_id')
            mids=self.query('SELECT matter_id FROM matter_members WHERE insight_id=?',(iid,)) if iid else []
            mid=mids[0]['matter_id'] if mids else None
            if body.get('matter_id'):
                detail=matters.detail(self,body['matter_id']);mid=detail['id']
                if body.get('revision')!=workflow.revision(self,mid):raise ValueError('事项有新消息，请重新打开核对')
                iid=detail['personal_insight_id'] or detail['items'][0]['id']
            existing=workflow.linked_tasks(self,mid) if mid else []
            from .schedule import deadline, editor_plan, plan_values
            if existing:
                if 'plan' in body and plan_values(body['plan']) != tuple(existing[0][k] for k in ('planned_date','planned_start','planned_end')):
                    raise ValueError('事项已有待办，请重新打开调整安排')
                return existing[0]['id']
            slot = editor_plan(self,body) if 'plan' in body else (None,None,None)
            limit=deadline(body.get('deadline',detail.get('suggested_deadline') if body.get('matter_id') else None))
            with self.db:
                cur = self.db.execute('INSERT INTO tasks(insight_id,title,due,remind_at,created,deadline,deadline_reviewed,planned_date,planned_start,planned_end) VALUES (?,?,?,?,?,?,?,?,?,?)',
                                  (iid or None, title, dt.isoformat() if dt else None, dt.timestamp() if dt else None, time.time(), limit, int('deadline' in body), *slot))
                if mid:workflow.remember(self,mid,'scheduled')
                return cur.lastrowid

    def update_task(self, task_id, body):
        with self.lock, self.db:
            from . import workflow, matters
            if body.get('matter_id'):
                mid=matters.resolve_id(self,body['matter_id'])
                if body.get('revision')!=workflow.revision(self,mid):raise ValueError('事项有新消息，请重新打开核对')
                if not any(t['id']==task_id for t in workflow.linked_tasks(self,mid)):raise ValueError('待办与事项不匹配')
            alias=self.query('SELECT target_id FROM task_merges WHERE duplicate_id=?',(task_id,))
            if alias:task_id=alias[0]['target_id']
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ValueError('待办不存在')
            from .schedule import editor_plan
            slot = editor_plan(self,body,row) if 'plan' in body else tuple(row[k] for k in ('planned_date','planned_start','planned_end'))
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
            from .schedule import deadline
            limit=deadline(body.get('deadline',row['deadline']))
            if body.get('matter_id'):
                workflow.remember(self,mid,'scheduled')
            self.db.execute('UPDATE tasks SET title=?,due=?,remind_at=?,notified=?,status=?,deadline=?,deadline_reviewed=?,planned_date=?,planned_start=?,planned_end=? WHERE id=?',
                            (title, due, remind_at, notified, status, limit, int('deadline' in body or row['deadline_reviewed']), *slot, task_id))
            if status == 'done' or 'due' in body or body.get('snooze'):
                self.db.execute('UPDATE notifications SET seen=1 WHERE task_id=?', (task_id,))

    def due_notifications(self, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            rows = self.db.execute("SELECT * FROM active_tasks WHERE status='open' AND notified=0 AND remind_at<=?", (now,)).fetchall()
            if rows and self.db.execute('SELECT 1 FROM exam_confirmations LIMIT 1').fetchone():
                from .exams import confirmed_events
                from .identity import profile
                events=confirmed_events(self,profile(self))
                rows=[r for r in rows if not events.get(r['id'],{}).get('outside_class')]
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

    def onebot(self, event, source='OneBot 实时消息', *, backfill=False):
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
                row['_backfill'] = backfill
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

    def backfill_receipts(self, before=0):
        return self.query('''SELECT m.id,m.group_name,m.sent_at,substr(m.content,1,100) preview,m.created
            FROM backfill_receipts r JOIN messages m ON m.id=r.message_id JOIN analysis_jobs j ON j.message_id=m.id
            WHERE j.account=? AND r.seen=0 AND (?=0 OR m.id<?) ORDER BY m.id DESC LIMIT 51''',
            (self.setting('ws_account'),before,before))

    def read_backfill(self, message_ids):
        if not isinstance(message_ids,list) or len(message_ids)>50 or not all(type(i) is int for i in message_ids):
            raise ValueError('补收消息列表无效')
        self.read_messages(message_ids, backfill_only=True)

    def read_messages(self, ids, *, backfill_only=False):
        if not isinstance(ids, list) or len(ids) > 500 or not all(type(i) is int and i > 0 for i in ids):
            raise ValueError('消息列表无效')
        if not ids:
            return
        with self.lock, self.db:
            placeholders = ','.join('?' for _ in ids)
            receipts = 'JOIN backfill_receipts USING(message_id)' if backfill_only else ''
            visible = self.db.execute(
                f'SELECT message_id FROM current_unread {receipts} WHERE message_id IN ({placeholders})', ids).fetchall()
            self.db.executemany('DELETE FROM unread_messages WHERE message_id=?', visible)
            self.db.executemany('UPDATE backfill_receipts SET seen=1 WHERE message_id=?', visible)

    def snapshot(self, query='', group=''):
        from .llm import status
        from . import identity
        from .matters import list_matters
        with self.lock:
            counts = {t: self.db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('messages', 'files', 'insights')}
            counts['insights'] = len(self.search('insights'))
            counts['tasks'] = self.db.execute("SELECT count(*) FROM active_tasks WHERE status='open'").fetchone()[0]
            all_matters = list_matters(self)
            matters = list_matters(self, query, group) if query or group else all_matters
            unread = {r['message_id'] for r in self.query('SELECT message_id FROM current_unread')}
            unread_matters = {m['id'] for m in all_matters if m['status']!='dismissed' and not unread.isdisjoint(m['source_message_ids'])}
            counts.update(matters=len(all_matters), unread_messages=len(unread), unread_matters=len(unread_matters))
            for matter in matters:
                matter['unread'] = matter['id'] in unread_matters
            history_groups = [x['group_name'] for x in self.query("SELECT group_name FROM messages UNION SELECT group_name FROM files WHERE group_name!='个人课表' ORDER BY group_name")]
            from .native import NativeBridge
            active_groups = [g['group_name'] for g in NativeBridge(self).group_catalog() if g['selected']]
            # A configured collector owns the normal filter; historical records remain accessible.
            groups = active_groups if self.setting('ws_account') else history_groups
            tasks=self.query('SELECT t.*, ia.task_id IS NOT NULL identity_task, coalesce(t.insight_id,ia.insight_id) source_insight, m.id message_id, m.group_name, m.content, m.sender, m.sent_at, i.evidence, i.model FROM active_tasks t LEFT JOIN identity_answers ia ON ia.task_id=t.id LEFT JOIN insights i ON i.id=coalesce(t.insight_id,ia.insight_id) LEFT JOIN messages m ON m.id=i.message_id ORDER BY t.status DESC,t.remind_at IS NULL,t.remind_at,t.id DESC')
            for task in tasks:
                sources=self.query('''SELECT DISTINCT m.group_name FROM matter_members mm
                    JOIN insights i ON i.id=mm.insight_id JOIN messages m ON m.id=i.message_id
                    WHERE mm.matter_id=(SELECT matter_id FROM matter_members WHERE insight_id=?)
                    ORDER BY m.group_name''',(task['source_insight'],))
                task['groups']=[r['group_name'] for r in sources] or ([task['group_name']] if task['group_name'] else [])
                task['group_name']='、'.join(task['groups'])
            return {'identity':dict(identity.profile(self),account=identity.account(self)),'matters':matters,'matter_error':self.setting('matter_error') or '', 'llm': status(self), 'counts': counts, 'insights': self.search('insights', query, group), 'files': self.search('files', query, group),
                    'messages': self.search('messages', query, group),
                    'task_merges': self.query('SELECT m.*,t.title,t.due FROM task_merges m JOIN tasks t ON t.id=m.duplicate_id'),
                    'tasks': tasks,
                    'groups': groups, 'history_groups': history_groups,
                    'notifications': self.query('SELECT * FROM notifications WHERE seen=0 ORDER BY id DESC'),
                    'backfill_count':self.db.execute('SELECT count(*) FROM backfill_receipts r JOIN analysis_jobs j ON j.message_id=r.message_id WHERE r.seen=0 AND j.account=?',(self.setting('ws_account'),)).fetchone()[0],
                    'watches': self.query('SELECT * FROM watches'),
                    'settings': {k: self.setting(k) for k in ('interests', 'bridge_enabled', 'bridge_groups', 'last_event', 'native_notifications', 'notification_error')}}
