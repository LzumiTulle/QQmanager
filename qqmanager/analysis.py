"""Explainable, offline extraction. Suggestions never execute actions."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


def timestamp(value):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, (float, int)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), TZ)
        normalized = str(value).replace('/', '-').replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            dt = None
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
                try:
                    dt = datetime.strptime(normalized, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
        return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _clause_date(text, sent_at):
    base = timestamp(sent_at)
    date = None
    text=text.replace('星期','周').replace('礼拜','周')
    if re.search(r'\d+\s*周(?:的)?\s*周[一二三四五六日天]',text):
        return None,'需结合教学周核对日期'
    mentioned=re.findall(r'(?:20\d{2}[-/年])?\d{1,2}[-/月]\d{1,2}[日号]?|今天|今日|今晚|明天|明晚|后天|(?:下周|本周|这周|周)[一二三四五六日天]',text)
    weekdays=[label for label in set(mentioned) if re.fullmatch(r'周[一二三四五六日天]',label)]
    weekday_annotation=False
    if len(mentioned)==2 and len(weekdays)==1:
        first,second=map(re.escape,mentioned[:2])
        weekday_annotation=bool(re.search(first+r'\s*[（(]?\s*'+second,text))
    if len(set(mentioned))>1 and not weekday_annotation:
        return None,'原文有多个日期，请手动确认'
    explicit = re.search(r'(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?', text)
    short = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})[日号]', text)
    try:
        if explicit:
            date = datetime(*map(int, explicit.groups()), tzinfo=TZ)
        elif short and base:
            date = datetime(base.year, *map(int, short.groups()), tzinfo=TZ)
        elif base:
            relative = re.search(r'今天|今日|今晚|明天|明晚|后天', text)
            week = re.search(r'(下周|本周|这周|周|星期)([一二三四五六日天])', text)
            if relative:
                offset = {'明天': 1, '明晚': 1, '后天': 2}.get(relative[0], 0)
                date = base + timedelta(days=offset)
            elif week:
                day = '一二三四五六日'.index(week[2].replace('天', '日'))
                delta = day - base.weekday()
                if week[1] == '下周':
                    delta += 7
                elif week[1] in ('周', '星期') and delta < 0:
                    delta += 7
                date = base + timedelta(days=delta)
        if date is None:
            return None, '未识别明确日期，需手动确认'
        if weekday_annotation and weekdays[0][-1].replace('天','日')!='一二三四五六日'[date.weekday()]:
            return None,'日期与星期不一致，请核对原文'
        clocks = list(re.finditer(r'(?<!\d)(上午|早上|中午|下午|晚上|凌晨|今晚|明晚)?\s*([01]?\d|2[0-3])(?:[:：]([0-5]\d)(?!\d)|[点时]([0-5]?\d分?|半)?(?!\d))', text))
        if not clocks:
            return None, f'识别到 {date:%Y-%m-%d}，具体时间待确认'
        if len(clocks)>1:
            return None,'原文有多个时刻，请手动确认'
        clock=clocks[0]
        hour = int(clock[2])
        minute_text = clock[3] or clock[4] or '0'
        minute = 30 if minute_text == '半' else int(minute_text.rstrip('分'))
        if minute > 59:
            return None, '时间格式有歧义，需手动确认'
        if clock[1]=='凌晨' and hour == 12:
            hour = 0
        if (clock[1] in ('下午','晚上','今晚','明晚') or (clock[1]=='中午' and hour<=3)) and hour < 12:
            hour += 12
        return date.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(), '从原文推测，请确认提醒时间'
    except ValueError:
        return None, '日期格式有歧义，需手动确认'


CLAUSE = re.compile(r'[，,。；;！？!?\n]+')
ACTION = re.compile(r'提交|填写|交付|改交|补交|交(?:材料|作业|读书笔记|给)|领取|去领|报送|缴费|上传|确认|反馈|完成|核查|核对|收集|上交|报名|签到')
NEGATIVE = re.compile(r'请勿|切勿|不要(?!忘)|无需|不必|不用|不得|禁止|暂缓|暂停|取消|作废|不再|不能|不需要|别再')
TRANSITION = re.compile(r'但(?:是)?|而是|改为|改成|(?=改交)|同时请|并请')


def action_clauses(text):
    return [part.strip() for clause in CLAUSE.split(text) for part in TRANSITION.split(clause) if part.strip()]


def negative_instruction(text):
    return bool(NEGATIVE.search(text) and (ACTION.search(text) or re.search('会议|活动|考试|课程',text)))


def prohibited_only(text):
    parts=action_clauses(text)
    return any(negative_instruction(p) for p in parts) and not any(
        ACTION.search(p) and not NEGATIVE.search(p) for p in parts)


def due_date(text, sent_at):
    # A period modifier belongs to its clock. A deadline in a separate clause
    # takes precedence over an independent meeting or publication time.
    parts = [p for p in action_clauses(text) if not NEGATIVE.search(p)]
    limits = [p for p in parts if re.search(r'截止|截至|最迟|前.{0,8}(?:提交|交给|交齐|上交|报送|上传|缴费)',p)]
    candidates = [_clause_date(p,sent_at) for p in (limits or parts)]
    dated = list(dict.fromkeys(x for x in candidates if x[0] or re.search(r'20\d{2}-\d{2}-\d{2}',x[1])))
    if len(dated)==1:return dated[0]
    if len(dated)>1:return None,'原文有多个时间，请手动确认'
    return None, '未识别明确日期，需手动确认'


def analyze(text, sent_at=None, interests=''):
    from .quality import noise_reason, readable
    if noise_reason(text):
        return None
    body = readable(text)
    if re.search(r'(?:考试|补考).{0,12}(?:好烦|好难|紧张|要寄|完蛋|不想考)[啊呀了。！!]*$',body) and not re.search(r'通知|安排|日程|请|务必',body):
        return None
    if re.search(r'^(?:请问|怎么|如何|有没有|谁知道)|[？?]$',body) and not re.search(r'解决方法|解决步骤|解答|答[：:]',body):
        return None
    from .identity import retained
    # A keyword alone is not evidence of a useful announcement.
    action = re.search(r'(?:提交|填写|交付|交(?:材料|作业|读书笔记)|领取|去领|报送|缴费|上传|确认|反馈|完成|核查|核对|收集).{1,60}', body)
    directive = re.search(r'请|务必|必须|需要|有空|应于|截止|截至|前|记得|^(?:提交|填写|报送|交付)|^(?:通知|公告)[：:]', body)
    personal_report = re.search(r'^(?:我|本人|已经|刚刚|已提交|已填写)|(?:提交|填写|报送|交付).{0,20}(?:了|完了|好了)[。！!]*$', body)
    schedule = re.search(r'会议|开会|停课|停电|停水|维护|考试|补考|培训|集合|比赛|赛事|公开赛|决赛|联赛|[A-Za-z]赛|报名|课程|讲座|分享会', body)
    timing = re.search(r'今天|今日|明天|后天|周[一二三四五六日天]|\d{1,2}[:：点时月日]|第?\d{1,2}周|(?:考试|补考)(?:日程|安排)|延期|改期|取消|推迟|调整', body)
    resource = re.search(r'资料|课件|教程|文档|附件|指南|模板|学习路线|解决方法|解决步骤|解决方案', body)
    concrete = re.search(r'https?://|分享|下载|附件|\.(?:pdf|docx|zip|md)|包含|使用|选择|执行|步骤|先.{1,20}再', body, re.I)
    if prohibited_only(body):
        kind,score,reason='通知',75,'原文说明取消或无需执行'
    elif action and directive and not personal_report:
        kind,score,reason='待办',85,'包含明确的处理动作与对象'
    elif (schedule and timing) or (re.search(r'^(?:通知|公告)[：:]',body) and len(body)>=10 and schedule):
        kind,score,reason='通知',75,'包含具体安排、时间或安排变更'
    elif resource and concrete and len(body)>=12:
        kind,score,reason='资料',60,'包含可使用的资料或具体解决步骤'
    elif retained(text):
        kind,score,reason='通知',70,'保留全体通知或需要结合上文的消息'
    else:
        return None
    terms = [x.strip() for x in re.split(r'[,，\n]', interests) if x.strip()]
    hits = [x for x in terms if x.casefold() in body.casefold()]
    if hits:
        reason += '；关注词：' + '、'.join(hits)
        score = min(score + 10, 100)
    due,note=due_date(body,sent_at) if kind!='资料' else (None,'')
    if re.search(r'延期|改期|取消|更正|改为|调整|推迟|作废',body):
        note='安排变更，请核对已有待办；不会自动修改提醒。'+note
    return {'kind':kind,'score':score,'reason':reason,'title':re.sub(r'\s+',' ',body).strip()[:110],'due':due,'date_note':note}
