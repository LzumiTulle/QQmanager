"""Personal decisions over source revisions; task state is the single completion state."""
import json
import time
from .reschedule import digest


def migrate(s):
    s.db.executescript('''CREATE TABLE IF NOT EXISTS workflow_decisions (
      matter_id INTEGER PRIMARY KEY REFERENCES matters(id), revision TEXT NOT NULL,
      decision TEXT NOT NULL, updated REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS task_merges (
      duplicate_id INTEGER PRIMARY KEY REFERENCES tasks(id),
      target_id INTEGER NOT NULL REFERENCES tasks(id), created REAL NOT NULL);
      CREATE VIEW IF NOT EXISTS active_tasks AS SELECT * FROM tasks
      WHERE id NOT IN (SELECT duplicate_id FROM task_merges);''')
    if not s.setting('workflow_content_revision_v2'):
        with s.lock,s.db:
            for d in s.query('SELECT * FROM workflow_decisions'):
                if d['revision']==legacy_revision(s,d['matter_id']):
                    s.db.execute('UPDATE workflow_decisions SET revision=? WHERE matter_id=?',
                                 (revision(s,d['matter_id']),d['matter_id']))
            s.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('workflow_content_revision_v2','true'))



def legacy_revision(s,mid):
    rows=s.query('''SELECT i.id,i.title,i.due,m.id message_id,m.content FROM matter_members mm
      JOIN insights i ON i.id=mm.insight_id JOIN messages m ON m.id=i.message_id
      WHERE mm.matter_id=? ORDER BY i.id''',(mid,))
    files=s.query('''SELECT a.id,a.file_id,f.digest,f.body FROM attachments a
      LEFT JOIN files f ON f.id=a.file_id WHERE a.message_id IN
      (SELECT i.message_id FROM matter_members mm JOIN insights i ON i.id=mm.insight_id
       WHERE mm.matter_id=?) ORDER BY a.id''',(mid,))
    return digest(dict(items=rows,files=files))


def revision(s,mid):
    import re
    # A repost is the same evidence, irrespective of group/message/insight IDs.
    rows=s.query('''SELECT m.content,m.sent_at FROM matter_members mm
      JOIN insights i ON i.id=mm.insight_id JOIN messages m ON m.id=i.message_id
      WHERE mm.matter_id=?''',(mid,))
    contents=set()
    for r in rows:
        text=re.sub(r'\s+','',r['content'])
        relative=bool(re.search('今天|明天|今晚|明晚|本周|下周|后天',text))
        contents.add((text,r['sent_at'][:10] if relative and r['sent_at'] else ''))
    files=s.query('''SELECT f.digest,f.body FROM attachments a JOIN files f ON f.id=a.file_id
      WHERE a.message_id IN (SELECT i.message_id FROM matter_members mm
      JOIN insights i ON i.id=mm.insight_id WHERE mm.matter_id=?)''',(mid,))
    return digest(dict(contents=sorted(contents),files=sorted({(f['digest'],f['body'] or '') for f in files})))


def remember(s,mid,decision):
    s.db.execute('INSERT OR REPLACE INTO workflow_decisions VALUES (?,?,?,?)',
                 (mid,revision(s,mid),decision,time.time()))


def linked_tasks(s,mid):
    return s.query('''SELECT t.*, ia.task_id IS NOT NULL identity_task FROM active_tasks t
                      LEFT JOIN identity_answers ia ON ia.task_id=t.id
                      JOIN matter_members mm ON mm.insight_id=coalesce(t.insight_id,ia.insight_id)
                      WHERE mm.matter_id=? ORDER BY t.id''',(mid,))


def decide(s,body):
    from . import matters
    mid=int(body['id'])
    with s.lock:
        detail=matters.detail(s,mid);mid=detail['id']
        if detail['status']=='dismissed':raise ValueError('事项已忽略，请先恢复')
        if body.get('revision')!=revision(s,mid):raise ValueError('事项有新消息，请重新打开核对')
        action=body.get('decision')
        if action not in ('acknowledged','reviewed'):raise ValueError('无效处理方式')
        with s.db:remember(s,mid,action)


def snapshot(s,exam_data,changes,course_notices=()):
    from . import matters, exams
    result={};confirm=[];updates=[]
    exam_by={};change_by={}
    for e in exam_data['events']:exam_by.setdefault(e['matter_id'],[]).append(e)
    for c in changes:change_by.setdefault(c['matter_id'],[]).append(c)
    pending={e['matter_id'] for e in exam_data['pending']}
    from .identity import profile
    academic=profile(s)
    fixed_exams=exams.confirmed_events(s,academic)
    historical={}
    for tid,e in fixed_exams.items():
        if not e['outside_class']:
            historical.setdefault(e['matter_id'],{})[tid]=s.query('SELECT * FROM active_tasks WHERE id=?',(tid,))[0]
    calendar_exams=[e for e in fixed_exams.values() if not e['outside_class']]
    for m in matters.list_matters(s):
        mid=m['id'];rev=revision(s,mid);tasks=linked_tasks(s,mid)
        decisions=s.query('SELECT * FROM workflow_decisions WHERE matter_id=?',(mid,))
        accepted=decisions[0] if decisions else None
        es=exam_by.get(mid,[]);cs=change_by.get(mid,[])
        relevant=[e for e in es if e['personal']]
        if m['status']!='dismissed':
            calendar_exams.extend(e for e in relevant if e['class_slot'] and not e['task'] and not e.get('previous_task'))
        filtered=bool(m['topic']=='考试' and academic['class_name'] and es and not relevant
                      and all(e['audience'] and not e['class_matches'] for e in es))
        old_tasks=list(historical.get(mid,{}).values())
        scheduled=bool(tasks or old_tasks or any(e.get('task') or e.get('previous_task') or e['class_slot'] for e in relevant) or any(c['state']=='applied' for c in cs))
        changed=bool(accepted and accepted['revision']!=rev)
        if any(e.get('previous_task') and not e.get('task') for e in relevant):changed=True
        if any(c['state']!='applied' for c in cs) and any(c['state']=='applied' for c in cs):changed=True
        needs=any(not e.get('task') and not e['class_slot'] for e in relevant) or any(c['state']!='applied' for c in cs)
        all_tasks=list({t['id']:t for t in tasks+old_tasks+[e['task'] for e in relevant if e.get('task')]}.values())
        state='changed' if changed else 'confirm' if needs else 'done' if all_tasks and all(t['status']=='done' for t in all_tasks) else 'scheduled' if scheduled else 'acknowledged' if accepted and accepted['revision']==rev else 'confirm'
        if mid in pending and scheduled:state='changed'
        if m['status']=='dismissed':state='dismissed'
        label={'changed':'有新变化','confirm':'需要确认','done':'已完成','scheduled':'已安排','acknowledged':'已知悉','dismissed':'已忽略'}[state]
        if state=='confirm' and not (all_tasks or needs or es or cs) and set(m['source_message_ids']).issubset(course_notices):
            state,label='course','已加入课程'
        candidate=bool(m['actionable'] and state=='confirm' and not all_tasks and not cs and m['topic']!='考试')
        if candidate or (state=='scheduled' and tasks and not cs and not old_tasks and not relevant and not any(t['planned_date'] for t in tasks if t['status']=='open')):
            label='待安排'
        if filtered and state!='dismissed':
            label='未匹配本班'
            if state=='confirm':state='acknowledged'
        entry=dict(source_message_ids=m['source_message_ids'],class_filtered=filtered,exam_class=academic['class_name'] if m['topic']=='考试' else '',exam_sessions=exam_data.get('sessions',{}).get(str(mid),[]),date_facts=m['date_facts'],week_start=m['week_start'],candidate=candidate,personal_title=m['personal_title'],personal_insight_id=m['personal_insight_id'],suggested_deadline=m['suggested_deadline'],id=mid,title=m['title'],topic=m['topic'],group_name=m['group_name'],groups=m['groups'],state=state,label=label,revision=rev,
                   tasks=all_tasks,exams=relevant,changes=cs,recognizing=mid in pending,dates=m['dates'])
        entry['read']=m['read']
        result[str(mid)]=entry
        if state=='changed':updates.append(entry)
        elif state=='confirm':confirm.append(entry)
    return dict(matters=result,confirm=confirm,updates=updates,fixed_exams=list(fixed_exams.values()),calendar_exams=calendar_exams)


def merge_exam_duplicates(s):
    """Archive only exact duplicates of confirmed exam reminders, never delete tasks."""
    merged=[]
    with s.lock,s.db:
        for ref in s.query('SELECT DISTINCT task_id FROM exam_confirmations'):
            rows=s.query('SELECT * FROM active_tasks WHERE id=?',(ref['task_id'],))
            if not rows:continue
            target=rows[0]
            duplicates=s.query('''SELECT t.* FROM active_tasks t WHERE id!=? AND insight_id IS NULL
              AND deadline IS NULL AND planned_date IS NULL AND title=? AND due IS ? AND remind_at IS ? AND status=? AND notified=?
              AND NOT EXISTS (SELECT 1 FROM exam_confirmations c WHERE c.task_id=t.id)
              AND NOT EXISTS (SELECT 1 FROM task_merges m WHERE m.target_id=t.id)''',
              (target['id'],target['title'],target['due'],target['remind_at'],target['status'],target['notified']))
            for t in duplicates:
                s.db.execute('INSERT INTO task_merges VALUES (?,?,?)',(t['id'],target['id'],time.time()))
                s.db.execute('UPDATE notifications SET seen=1 WHERE task_id=?',(t['id'],))
                merged.append(t['id'])
    return merged


def restore_merge(s,task_id):
    with s.lock,s.db:
        s.db.execute('DELETE FROM task_merges WHERE duplicate_id=?',(int(task_id),))


def move_matter_links(s,source_id,target_id):
    """Keep personal state attached when automatic/manual grouping changes its container."""
    from . import exams
    for batch in s.query('SELECT payload FROM exam_extractions WHERE matter_id=?',(source_id,)):
        for e in json.loads(batch['payload']).get('exams',[]):
            before=exams.event_key(dict(e,matter_id=source_id));after=exams.event_key(dict(e,matter_id=target_id))
            s.db.execute('''INSERT OR IGNORE INTO exam_confirmations SELECT ?,task_id,confirmed_at
                            FROM exam_confirmations WHERE event_key=?''',(after,before))
            s.db.execute('DELETE FROM exam_confirmations WHERE event_key=?',(before,))
    s.db.execute('UPDATE exam_extractions SET matter_id=? WHERE matter_id=?',(target_id,source_id))
    s.db.execute('UPDATE schedule_changes SET matter_id=? WHERE matter_id=?',(target_id,source_id))
    s.db.execute('''INSERT OR IGNORE INTO workflow_decisions SELECT ?,revision,decision,updated
                    FROM workflow_decisions WHERE matter_id=?''',(target_id,source_id))
    s.db.execute('DELETE FROM workflow_decisions WHERE matter_id=?',(source_id,))
