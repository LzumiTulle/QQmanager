"""Local evidence for grouping separate messages about the same event."""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import timedelta

from . import planner
from .analysis import timestamp
from .course_notices import SEPARATE_ACTION, match_notices
from .quality import duplicate_key
from .reschedule import occurrences

NUMBER = r'[0-9一二两三四五六七八九十]+'
ROUND = re.compile(rf'第({NUMBER})次(?:的)?作业|作业(?:第)?({NUMBER})(?![0-9一二两三四五六七八九十章节题分月号])')
REFERENCE_UPDATE = re.compile(r'(?:@全体成员)?(?:补充|更正|提醒)?[：:,，]?(?:刚才(?:说的|通知的)?(?:那份|那项|的)?|上述|这份|该份)'
                              r'(作业|报告|申请表|报名表|登记表)(?:的)?(?:截止|提交|上交|改交|延期|改为|请交|还需)')
FORM_NAME = re.compile(r'《([^《》]{4,50}(?:自查表|申请表|报名表|登记表|反馈表|调查表|报告书))》')
RELATIVE = re.compile('今天|今日|今晚|明早|明天|明晚|后天|昨天|本次|这次|本周|这周|下周|星期|周[一二三四五六日天]')
SPAN = {'lesson':timedelta(days=21), 'assignment':timedelta(days=21), 'document':timedelta(days=3), 'repost':timedelta(hours=48)}


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


def round_number(raw):
    digits = '零一二三四五六七八九'
    raw = raw.replace('两','二')
    if raw.isascii():
        return int(raw) if len(raw) <= 3 else None
    if len(raw)==1 and raw in digits:
        return digits.index(raw)
    if re.fullmatch('[一二三四五六七八九]?十[一二三四五六七八九]?', raw):
        a,b = raw.split('十')
        return (digits.index(a) if a else 1)*10 + (digits.index(b) if b else 0)
    return None


def event_keys(s, rows):
    """Return at most one key per item. A mixed source is not a unique event."""
    keys = {}
    courses = {compact(c['title']) for c in planner.course_rows(s) if c['enabled']}
    aliases = defaultdict(set)
    for course in courses:
        base = re.sub(r'\([^()]*\)$','',course) or course
        aliases[base].add(course)
        if latin := re.match(r'[a-z][a-z0-9+#.]{2,}',base):
            aliases[latin[0]].add(course)
    counts = Counter(r['message_id'] for r in rows)
    labs = defaultdict(list)
    if courses and any('实验' in r['content'] or '更正' in r['content'] or '补充' in r['content'] for r in rows):
        for occurrence, notices in match_notices(s, occurrences(s), planner.config(s)['blocks']).items():
            for notice in notices:
                if notice['kind']=='实验内容':
                    labs[notice['id']].append(occurrence)

    attached = {r['message_id'] for r in s.query('SELECT DISTINCT message_id FROM attachments')}
    followups = []
    by_message = {}
    for row in rows:
        body = compact(row['content'])
        date = timestamp(row['sent_at'])
        if not date or counts[row['message_id']] != 1 or len(body)>500 or '[' in body or '【' in body:
            continue
        by_message[row['message_id']] = row
        reference = REFERENCE_UPDATE.match(body)
        if reference and not re.search(r'另外|另一|另有|其余|第二份|[;；？?]',body):
            followups.append((date,row,reference[1]))
        rounds = [round_number(a or b) for a,b in ROUND.findall(body)]
        names = FORM_NAME.findall(body)
        lab = labs.get(row['message_id'],[])
        if len(lab)==1 and not SEPARATE_ACTION.search(body) and not re.search(rf'(?:实验{NUMBER}|第{NUMBER}(?:个|项|次)实验)', body):
            key = ('lesson', *lab[0])
        elif rounds:
            subjects = {c for alias, choices in aliases.items() if alias in body for c in choices}
            if not subjects:
                group = compact(row['group_name'])
                subjects = {c for alias, choices in aliases.items() if alias in group for c in choices}
            if len(subjects)!=1 or len(set(rounds))!=1 or None in rounds:
                continue
            key = ('assignment', subjects.pop(), rounds[0], date.year, date.month>=8)
        elif len(names)==1 and re.search('提交|上交|填写|截止|补充|更正|催交', body):
            if re.search('第.+?[轮批届]|上次|下次|上学期|下学期|[？?]',body):
                continue
            key = ('document', names[0])
        else:
            text = duplicate_key(body.removeprefix('@全体成员'))
            if len(text)<6 or row['message_id'] in attached:
                continue
            key = ('repost', text, date.date() if RELATIVE.search(body) else None)
        keys[row['id']] = key
    for _,row,kind in sorted(followups,key=lambda x:(x[0],x[1]['message_id'])):
        if row['sender'] in ('','匿名','群成员','未知发送者'):
            continue
        prev = s.previous_group_message(row)
        parent = by_message.get(prev['id']) if prev else None
        if (parent and parent['sender']==row['sender'] and parent['id'] in keys
                and parent['content'].count(kind)==1
                and not re.search('两份|多份|两项|多项|分别|另[一有]|[；;]',parent['content'])):
            keys[row['id']] = keys[parent['id']]
    return keys


def local_groups(rows, memberships, keys):
    """Only combine intact, single-event matters. Never bridge two rounds via a third."""
    by_matter = defaultdict(list)
    for row in rows:
        by_matter[memberships[row['id']]['matter_id']].append(row)
    by_event = defaultdict(list)
    for mid, members in by_matter.items():
        meta = memberships[members[0]['id']]
        if meta['locked'] or meta['anchor'] or any(r['status']=='dismissed' for r in members):
            continue
        identities = {keys.get(r['id']) for r in members}
        if len(identities)!=1 or None in identities:
            continue
        key = identities.pop()
        if len({(r['live_account'],r['live_group'] or r['group_name']) for r in members})!=1:
            continue
        dates = [timestamp(r['sent_at']) for r in members]
        by_event[(meta['scope'],key)].append((min(dates),max(dates),mid))
    for (_,key), events in by_event.items():
        windows = []
        for start,end,mid in sorted(events):
            if not windows or end-first > SPAN[key[0]]:
                first = start
                windows.append([])
            windows[-1].append(mid)
        for window in windows:
            target,*others = sorted(window)  # Keep the original ID even on late history imports.
            if not others:
                continue
            def contents(mid):
                return {duplicate_key(compact(r['content']).removeprefix('@全体成员')) for r in by_matter[mid]}
            known = contents(target)
            for mid in others:
                texts = contents(mid)
                yield mid,target,key[0]=='repost' and texts==known
                known.update(texts)
