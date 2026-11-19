"""Personal time blocks belong to the task that owns completion and reminders."""
import re
import time
from datetime import date, datetime, timedelta

from . import planner, workflow
from .analysis import due_date, negative_instruction

CLOCK = r'(?:[01]\d|2[0-3]):[0-5]\d'
DAY = r'(?:20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]?|今天|今日|今晚|明天|明晚|后天|(?:下周|本周|这周|下星期|本星期|这星期|周|星期)[一二三四五六日天])'
PERIOD = r'(?:上午|早上|下午|中午|晚上)?'
HOUR = r'(?:[01]?\d|2[0-3])(?:[:：][0-5]\d|[点时](?:[0-5]?\d分?|半)?)'
WHEN = re.compile(r'(?P<day>'+DAY+r')\s*(?P<start>'+PERIOD+r'\s*'+HOUR+r')?(?:\s*[-—–至到]\s*(?P<end>'+PERIOD+r'\s*'+HOUR+r'))?')
DELIVERY = re.compile(r'^(?:(?:全部|统一|集中|务必|请|需|要|记得|之前|前|完成|将|把|提交给)\s*)*(?:提交|交给|交到|交齐|上交|交作业|交材料|报送|上传|填报|缴费)')
EVENT = re.compile(r'举行|召开|开会|开考|集合|签到|开展|讲座|参加|开幕|审核|考试|面试|答辩|上课')


def _clock(text, period=''):
    if not text:
        return ''
    hour, minute = (re.findall(r'\d+',text)+['0'])[:2]
    hour = int(hour)
    explicit=re.search('下午|晚上|中午|上午|早上',text)
    modifier=explicit[0] if explicit else period
    if hour < 12 and (re.search('下午|晚上',modifier) or ('中午' in modifier and hour<=3)):
        hour += 12
    return f'{hour:02d}:{30 if "半" in text else int(minute):02d}'


def notice_dates(items, *, calendar=None, exam_week=None):
    facts=set()
    for item in items:
        # Older notices retain the original “明天有早八” instead of the local extractor's “明天08:00上课”.
        content=re.sub(r'^(?:@全体成员\s*)?('+DAY+r')有早八(?=[，,](?:大家)?不要忘记去上)',r'\g<1>08:00上课',item['content'])
        for clause in re.split(r'[，,。！!？?；;\n]',content):
            if negative_instruction(clause) or re.search(r'取消|作废|原定|待定|尚未确定',clause):
                continue
            if exam_week and re.match(r'^\s*(?:由于|因)',clause):
                # A reason for moving an exam can mention another exam's date.
                continue
            matches=list(WHEN.finditer(clause))
            for i,m in enumerate(matches):
                before=clause[matches[i-1].end() if i else 0:m.start()].strip()
                after=clause[m.end():matches[i+1].start() if i+1<len(matches) else len(clause)].strip()
                if re.search(r'(?:截止(?:日期|时间)?|截至|最迟)(?:到|为|于|是)?[：:\s]*$',before) or re.match(r'^(?:之前|前(?!往|来|去)|截止|截至)',after) or DELIVERY.match(after):
                    kind,label='deadline','提交截止'
                elif re.match(r'^(?:将|把).{1,30}(?:送至|送到|送交|交至|交到)|^(?:送至|送到|送交)',after):
                    kind,label='handoff','送交材料'
                elif event := (EVENT.search(after[:24]) or EVENT.search(before[-18:])):
                    kind,label='event',event[0]
                else:
                    continue
                token=m['day'].replace('号','日').replace('星期','周')
                if '月' in token and not token.endswith('日'):token+='日'
                qualified=re.search(r'(?:第\s*)?(\d{1,2})\s*周(?:的)?\s*$',before)
                if qualified and re.fullmatch('周[一二三四五六日天]',token):
                    week=int(qualified[1])
                    if not calendar or not calendar['configured'] or not 1<=week<=calendar['total_weeks']:continue
                    event_day=planner.week_one(calendar)+timedelta(weeks=week-1,days='一二三四五六日'.index(token[-1].replace('天','日')))
                else:
                    _,note=due_date(token,item.get('sent_at'))
                    found=re.search(r'\d{4}-\d{2}-\d{2}',note)
                    if not found:continue
                    event_day=date.fromisoformat(found[0])
                if exam_week and (not calendar['configured'] or planner.school_week(calendar,event_day)!=exam_week):
                    continue
                clock=_clock(m['start'])
                end=_clock(m['end'],m['start'] or '')
                value=event_day.isoformat()+('T'+clock if clock else '')
                part=re.match(r'^(上午|早上|下午|中午|晚上)',after) if not clock else None
                period=part[1] if part else ('晚上' if token in ('今晚','明晚') and not clock else '')
                facts.add((kind,value,end if end>clock else '',label,period))
    return [dict(kind=k,value=v,end=e,label=l,period=p) for k,v,e,l,p in sorted(facts,key=lambda f:(f[1],f[0],f[3]))]


def single_deadline(facts):
    limits={f['value'] for f in facts if f['kind']=='deadline'}
    # A date-only repetition and a more precise notice on that date agree.
    precise={v for v in limits if len(v)>10}
    limits=precise | {v for v in limits if len(v)==10 and not any(t.startswith(v) for t in precise)}
    return limits.pop() if len(limits)==1 else None


def suggested_deadline(items):
    return single_deadline(notice_dates(items))


def recommend(facts, limit, week_start, busy, now):
    """Suggest one editable work session; notification times keep their stated precision."""
    today=now.date().isoformat()
    clock=now.strftime('%H:%M')
    if not limit and len({f['value'] for f in facts if f['kind']=='deadline'})>1:
        return dict(reason='通知中有多个截止时间，请先核对原文。')
    events={(f['value'],f['end'],f.get('period','')) for f in facts if f['kind']=='event'}
    if len(events)>1 and not limit:
        return dict(reason='通知中有多个活动时间，请选择要安排的活动。')
    event,finish,period=next(iter(events)) if len(events)==1 and not limit else ('','','')
    target=(limit or event)[:10]
    if target and (target<today or (target==today and (limit or event)[11:] and (limit or event)[11:]<clock)):
        return dict(reason='通知日期已过，可自行选择新的计划时间。')

    def available(day,start,end):
        return not any(start<b and a<end if b else start<=a<end for a,b in busy.get(day,()))

    if event and len(event)>10:
        start=event[11:]
        end=finish or (datetime.fromisoformat(event)+timedelta(minutes=30)).strftime('%H:%M')
        if end>start and available(target,start,end):
            return dict(date=target,start=start,end=end,reason='按通知时段预填。' if finish else '按通知开始时间预填，预留 30 分钟。')
        return dict(date=target,reason='活动时间与已有日程冲突或跨日，请核对后安排。')

    first=date.fromisoformat(today)
    if limit:
        last=date.fromisoformat(target)
        preferred=max(first,last-timedelta(days=1))
        # Prefer the preceding day, then earlier free days, then the due date.
        days=[preferred-timedelta(days=n) for n in range(min(6,(preferred-first).days)+1)]
        if last!=preferred:days.append(last)
    elif event:
        days=[date.fromisoformat(target)]
    else:
        if week_start:first=max(first,date.fromisoformat(week_start))
        days=[first+timedelta(days=n) for n in range(7)]
    for d in days:
        day=d.isoformat()
        hours={'下午':(13,18),'晚上':(18,21),'早上':(8,12),'上午':(8,12),'中午':(12,14)}
        window=hours.get(period,(8,21))
        for minute in range(window[0]*60,window[1]*60-29,15):
            start=f'{minute//60:02d}:{minute%60:02d}'
            end=f'{(minute+30)//60:02d}:{(minute+30)%60:02d}'
            if day==today and start<=clock:continue
            if limit and day==target and limit[11:] and end>limit[11:]:break
            if available(day,start,end):
                return dict(date=day,start=start,end=end,reason='截止前的空闲时段 · 30 分钟' if limit else '建议空闲时段 · 30 分钟')
    return dict(reason='可安排的日期内没有连续 30 分钟空闲，请手动选择时段。')


def recommendations(s,flow,courses,blocks):
    from collections import defaultdict
    busy=defaultdict(list)
    for c in courses:
        busy[c['date']].append((blocks[(c['start_period']-1)//2][0],blocks[(c['end_period']-1)//2][1]))
    for e in flow['calendar_exams']:
        if e.get('start_time'):busy[e['date']].append((e['start_time'],e.get('end_time')))
    tasks=s.query("SELECT * FROM active_tasks")
    for t in tasks:
        if t['planned_start']:busy[t['planned_date']].append((t['planned_start'],t['planned_end']))
    now=datetime.now(planner.TZ)
    result={}
    sources={}
    for m in flow['matters'].values():
        sources.update({t['id']:m for t in m['tasks']})
        if m['candidate']:
            result['m'+str(m['id'])]=recommend(m['date_facts'],m['suggested_deadline'],m['week_start'],busy,now)
    fixed={e['task_id'] for e in flow['fixed_exams']}
    for t in tasks:
        if t['status']!='open' or t['planned_date'] or t['id'] in fixed:continue
        m=sources.get(t['id'],{})
        personal_clause=next((q for q in m.get('tasks',[]) if q['id']==t['id'] and q.get('identity_task')),None)
        if personal_clause:
            result['t'+str(t['id'])]=recommend([],t['deadline'],None,busy,now)
            continue
        if m.get('topic')=='考试':continue
        limit=t['deadline'] if t['deadline_reviewed'] else t['deadline'] or m.get('suggested_deadline')
        facts=m.get('date_facts',[])
        if t['deadline_reviewed'] and not limit:facts=[f for f in facts if f['kind']!='deadline']
        result['t'+str(t['id'])]=recommend(facts,limit,m.get('week_start'),busy,now)
    return result


def deadline(value):
    if not value:
        return None
    value = str(value)
    try:
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return value
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T' + CLOCK, value):
            datetime.fromisoformat(value)
            return value
    except ValueError:
        pass
    raise ValueError('请填写有效的截止日期和时间')


def plan_values(body):
    day, start, end = (body.get(k) or None for k in ('planned_date','planned_start','planned_end'))
    if day:
        try:
            valid_day = date.fromisoformat(day).isoformat() == day and 1900 <= int(day[:4]) <= 2200
        except (TypeError, ValueError):
            valid_day = False
        if not valid_day:
            raise ValueError('计划日期无效')
    if start or end:
        if not day or not all(isinstance(t,str) and re.fullmatch(CLOCK,t) for t in (start,end)) or start >= end:
            raise ValueError('请选择同一天内的有效起止时间')
    return day, start, end


def check_slot(s, board, day, start, end, task_id):
    if start:
        course_day = next(d for d in board['days'] if d['date']==day)
        busy = [(c['start_time'],c['end_time'],c['title']) for c in course_day['courses']]
        for exam in board['workflow']['calendar_exams']:
            if exam['date']==day and exam.get('start_time'):
                busy.append((exam['start_time'],exam.get('end_time'),exam['course']))
        busy.extend((t['planned_start'],t['planned_end'],t['title']) for t in s.query(
            "SELECT * FROM active_tasks WHERE planned_date=? AND planned_start IS NOT NULL AND id!=?",
            (day,task_id or 0)))
        for a,b,title in busy:
            overlaps = start < b and a < end if b else start <= a < end
            if overlaps:
                raise ValueError('时段冲突：'+title+'，请选择其他时间')


def editor_plan(s, body, task=None):
    slot = plan_values(body['plan'])
    if task:
        current = [task[k] for k in ('planned_date','planned_start','planned_end','deadline')]
        if body.get('expected') != current:
            raise ValueError('安排已更改，请重新打开')
        if tuple(current[:3]) == slot:
            return slot
        if s.query('SELECT 1 FROM exam_confirmations WHERE task_id=?', (task['id'],)):
            raise ValueError('请在考试安排中核对考试时间')
    if slot[1]:
        check_slot(s, planner.snapshot(s,slot[0]), *slot, task['id'] if task else None)
    return slot


def save(s, body):
    from . import matters
    day, start, end = plan_values(body)
    limit = deadline(body.get('deadline'))
    with s.lock:
        board = planner.snapshot(s,day)
        mid = body.get('matter_id')
        task_id = body.get('task_id')
        if mid:
            mid = matters.resolve_id(s,int(mid))
            item = board['workflow']['matters'].get(str(mid))
            if not item or item['revision'] != body.get('revision') or item['state'] == 'dismissed':
                raise ValueError('事项已更新，请重新打开')
            linked = workflow.linked_tasks(s,mid)
            if body.get('clause'):
                raise ValueError('请刷新日程，重新核对这条通知')
            if linked:
                task_id = linked[0]['id']
            elif not day or not item['candidate']:
                raise ValueError('该事项已处理，请刷新后查看')
        if not task_id and not mid:
            raise ValueError('请选择待办')
        rows = s.query('SELECT * FROM active_tasks WHERE id=?',(task_id,))
        task = rows[0] if rows else None
        if task_id and (not task or task['status'] != 'open'):
            raise ValueError('待办已完成或已合并，请刷新后查看')
        fields = ('planned_date','planned_start','planned_end','deadline')
        target = [day,start,end,limit]
        if task:
            if s.query('SELECT 1 FROM exam_confirmations WHERE task_id=?',(task_id,)):
                raise ValueError('请在考试安排中核对考试时间')
            current = [task[k] for k in fields]
            if current == target:
                return task_id
            if body.get('expected') != current:
                raise ValueError('安排已更改，请重新打开')
        check_slot(s, board, day, start, end, task_id)
        with s.db:
            if task:
                s.db.execute('UPDATE tasks SET planned_date=?,planned_start=?,planned_end=?,deadline=?,deadline_reviewed=1 WHERE id=?',(*target,task_id))
            else:
                iid = item['personal_insight_id']
                task_id = s.db.execute('''INSERT INTO tasks(insight_id,title,created,planned_date,planned_start,planned_end,deadline,deadline_reviewed)
                    VALUES (?,?,?,?,?,?,?,1)''',(iid,item['personal_title'],time.time(),*target)).lastrowid
                workflow.remember(s,mid,'scheduled')
            return task_id
