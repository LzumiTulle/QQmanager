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


RULES = [
    ('变更', r'延期|改期|取消|更正|改为|调整为|推迟|作废', 95, '包含时间或安排变更'),
    ('待办', r'提交|截止|作业|报名|填写|请.{0,8}(?:完成|确认|反馈)|交材料|交付', 85, '包含待处理动作或截止要求'),
    ('通知', r'通知|公告|开会|会议|务必|提醒|发布', 75, '包含通知或安排'),
    ('活动', r'活动|聚会|比赛|招聘|招募|面试|优惠', 65, '包含活动或机会'),
    ('资料', r'https?://|资料|课件|教程|文档|附件|分享|下载|\.pdf|\.docx|\.zip', 60, '包含资料或链接'),
    ('问答', r'解决方法|解决了|结论|总结|建议|如何|怎么|请问|求助', 50, '包含问题、解答或讨论结论'),
]


def analyze(text, sent_at=None, interests=''):
    matches = [(kind, score, reason) for kind, pattern, score, reason in RULES if re.search(pattern, text, re.I)]
    terms = [x.strip() for x in re.split(r'[,，\n]', interests) if x.strip()]
    hits = [x for x in terms if x.casefold() in text.casefold()]
    if not matches and not hits:
        return None
    kind, score, reason = matches[0] if matches else ('关注', 60, '匹配个人关注')
    if hits:
        reason += '；关注词：' + '、'.join(hits)
        score = min(score + 10, 100)
    due, note = due_date(text, sent_at) if kind in ('待办', '活动', '通知', '变更') else (None, '')
    if kind == '变更':
        note = '变更需核对已有待办；不会自动覆盖提醒。' + note
    return {'kind': kind, 'score': score, 'reason': reason, 'title': re.sub(r'\s+', ' ', text).strip()[:110], 'due': due, 'date_note': note}
