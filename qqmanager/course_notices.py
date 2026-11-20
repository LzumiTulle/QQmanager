"""Link classroom preparation messages to lessons in the effective timetable."""
import re
from collections import defaultdict

from .analysis import due_date, timestamp

PREPARATION = re.compile(r'(?:记得|请|务必|需要|别忘了?).{0,12}(?:带|准备好)|(?:上课|实验课|课前).{0,12}(?:携带|带上)')
# The same predicate is used by the partial index and its lookup.
NOTICE_CANDIDATE = "(instr(content,'带')>0 OR instr(content,'准备好')>0 OR instr(content,'实验')>0 OR instr(content,'更正')>0 OR instr(content,'补充')>0 OR instr(content,'改为')>0)"
LAB_SCOPE = re.compile(r'实验(?:课)?(?:的)?(?:内容|范围|要求|项目|题目)|(?:本次|这次|本节|这节)[^，。；！？\n]{0,24}实验(?:课)?(?:是|为|包括|包含|涉及|要做|做)')
LAB_CODE = r'[A-Za-z]{1,3}\s*[0-9]{1,3}'
LAB_CODES = LAB_CODE + r'(?:\s*[+＋、和及]\s*' + LAB_CODE + ')*'
SCOPE_UPDATE = re.compile(r'(?:更正|补充|改为)[一下说明：:,，\s]*(?:是|为|做|改为)?\s*'
                          + LAB_CODES + r'(?:[，,]\s*(?:不是|不止|不只是)(?:只有)?\s*' + LAB_CODES + r')?[。！!]?')
CODED_SCOPE = re.compile(r'[^。；;？?\n]{0,35}实验(?:课)?(?:内容|范围)?(?:是|为|做|包括|包含|涉及|[：:])\s*'
                         + LAB_CODES + r'(?:[，,]\s*(?:不是|不止|不只是)(?:只有)?\s*' + LAB_CODES + r')?[。！!]?')
LAB = re.compile('实验|上机|机房|实训')
SEPARATE_ACTION = re.compile('提交|上交|截止|上传|调课|停课|报名|缴费')
DATE_WORD = re.compile(r'今天|今日|今晚|明天|明晚|后天|(?:周|星期|礼拜)[一二三四五六日天]|\d+月|\d{4}[-/]')


def match_notices(store, lessons, blocks):
    by_date = defaultdict(list)
    if not lessons:
        return by_date
    rows = store.query(f"""SELECT m.id,m.content,m.sent_at,m.group_name,m.sender,j.account live_account,j.group_id live_group FROM messages m
        LEFT JOIN analysis_jobs j ON j.message_id=m.id
        WHERE NOT EXISTS(SELECT 1 FROM insights i WHERE i.message_id=m.id AND i.status='dismissed')
        AND (j.message_id IS NULL OR j.account=?) AND {NOTICE_CANDIDATE}
        ORDER BY julianday(m.sent_at) DESC,m.id DESC""", (store.setting('ws_account'),))
    # Resolve a short correction only from its immediate predecessor in the same
    # conversation. Use sent time, so replayed history follows the live path.
    linked = {}
    for row in reversed(rows):
        body = re.sub(r'^@全体成员\s*','',row['content'].strip())
        if SCOPE_UPDATE.fullmatch(body) and row['sender'] not in ('','匿名','群成员','未知发送者'):
            previous = store.previous_group_message(row)
            if previous and previous['sender']==row['sender'] and previous['id'] in linked:
                key = linked[previous['id']]
                linked[row['id']] = key
                by_date[key].append(dict(row,kind='实验内容'))
            continue
        sent = timestamp(row['sent_at'])
        lab_scope = LAB_SCOPE.search(body) or CODED_SCOPE.fullmatch(body)
        if not (lab_scope or PREPARATION.search(body)) or not re.search('课|上机|实验',body):
            continue
        if not sent or re.search(r'请问|是否|要不要|[？?]|不用带|无需带',body):
            continue
        context = re.sub(r'\s+', '', row['group_name'] + ' ' + body)
        candidates = []
        for lesson in lessons:
            title = re.sub(r'\s+', '', lesson['title'])
            subject = re.sub(r'(?:原理|实验|实训|课程|理论|基础|[（(](?:实验|理论)[）)])+$', '', title)
            if title not in context and (len(subject)<2 or subject not in context):
                continue
            if LAB.search(body) and not LAB.search(title + lesson['location']):
                continue
            if lesson['date'] < sent.date().isoformat():
                continue
            if lesson['date']==sent.date().isoformat() and blocks[(lesson['end_period']-1)//2][1] <= sent.strftime('%H:%M'):
                continue
            candidates.append(lesson)
        if not candidates:
            continue
        if DATE_WORD.search(body):
            value, note = due_date(body,row['sent_at'])
            date = re.search(r'\d{4}-\d\d-\d\d', value or note)
            if not date:
                continue
            candidates = [c for c in candidates if c['date']==date[0]]
        taught = [c for c in candidates if c.get('teacher') and c['teacher'] in row['sender']]
        candidates = taught or candidates
        if not candidates or len({c['title'] for c in candidates}) != 1:
            continue
        if not re.search('每次|每周|每节',body):
            first = min((c['date'],c['start_period']) for c in candidates)
            candidates = [c for c in candidates if (c['date'],c['start_period'])==first]
            if len(candidates)!=1:
                continue
        if lab_scope and len(candidates)==1 and not SEPARATE_ACTION.search(body):
            linked[row['id']] = (candidates[0]['id'],candidates[0]['date'])
        for c in candidates:
            by_date[c['id'],c['date']].append(dict(row, kind='实验内容' if lab_scope else '课前提醒'))
    for notices in by_date.values():
        notices.reverse()
        for notice in notices:
            del notice['live_account'], notice['live_group']
    return by_date
