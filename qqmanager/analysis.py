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


def due_date(text, sent_at):
    base = timestamp(sent_at)
    date = None
    explicit = re.search(r'(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?', text)
    short = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})日', text)
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
        clock = re.search(r'(?<!\d)([01]?\d|2[0-3])[:：点时]([0-5]\d)?', text)
        if not clock:
            return None, f'识别到 {date:%Y-%m-%d}，具体时间待确认'
        hour, minute = int(clock[1]), int(clock[2] or 0)
        if re.search(r'下午|晚上|今晚|明晚', text) and hour < 12:
            hour += 12
        return date.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(), '从原文推测，请确认提醒时间'
    except ValueError:
        return None, '日期格式有歧义，需手动确认'


def analyze(text, sent_at=None, interests=''):
    from .quality import noise_reason, readable
    if noise_reason(text):
        return None
    body = readable(text)
    if re.search(r'^(?:请问|怎么|如何|有没有|谁知道)|[？?]$',body) and not re.search(r'解决方法|解决步骤|解答|答[：:]',body):
        return None
    # A keyword alone is not evidence of a useful announcement.
    action = re.search(r'(?:提交|填写|交付|交材料|报送|缴费|上传|确认|反馈|完成).{1,60}', body)
    directive = re.search(r'请|务必|必须|需要|应于|截止|截至|前|记得|^(?:提交|填写|报送|交付)|^(?:通知|公告)[：:]', body)
    personal_report = re.search(r'^(?:我|本人|已经|刚刚|已提交|已填写)|(?:提交|填写|报送|交付).{0,20}(?:了|完了|好了)[。！!]*$', body)
    schedule = re.search(r'会议|开会|停课|停电|停水|维护|考试|培训|集合|比赛|赛事|公开赛|决赛|联赛|[A-Za-z]赛|报名|课程|讲座|分享会', body)
    timing = re.search(r'今天|今日|明天|后天|周[一二三四五六日天]|\d{1,2}[:：点时月日]|延期|改期|取消|推迟|调整', body)
    resource = re.search(r'资料|课件|教程|文档|附件|指南|模板|学习路线|解决方法|解决步骤|解决方案', body)
    concrete = re.search(r'https?://|分享|下载|附件|\.(?:pdf|docx|zip|md)|包含|使用|选择|执行|步骤|先.{1,20}再', body, re.I)
    if action and directive and not personal_report:
        kind,score,reason='待办',85,'包含明确的处理动作与对象'
    elif (schedule and timing) or (re.search(r'^(?:通知|公告)[：:]',body) and len(body)>=10 and schedule):
        kind,score,reason='通知',75,'包含具体安排、时间或安排变更'
    elif resource and concrete and len(body)>=12:
        kind,score,reason='资料',60,'包含可使用的资料或具体解决步骤'
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
