"""Complete, narrow notice templates that can bypass semantic analysis."""
import re
from .analysis import due_date, NEGATIVE, prohibited_only

DAY = r'(?:今天|今日|明天|明晚|今晚|后天|(?:本周|下周|周|星期)[一二三四五六日天]|(?:20\d{2}年)?\d{1,2}月\d{1,2}日)'
TIME = r'(?:(?:上午|早上|下午|晚上|中午)?(?:[01]?\d|2[0-3])(?:[:：][0-5]\d|[点时](?:半|[0-5]?\d分)?))'
OBJECT = r'[\w\u4e00-\u9fff（）()《》· -]{1,36}'
TASK = re.compile(r'(?:请(?:大家|同学们|各位同学|各班|参赛同学)?|请务必|务必|需要|记得)?(?:在|于)?(?:'+DAY+r'(?:'+TIME+r')?(?:之?前)?)?(?:提交|填写|上传|缴纳|缴费|上交|报送)'+OBJECT)
DELIVERY = re.compile(r'请(?:大家|同学们)?(?:抓紧时间|按时)?完成'+OBJECT+r'[，,]'+DAY+r'(?:'+TIME+r')?(?:之?前)?(?:全部|统一)?(?:上交|交给我|提交)')
EVENT = re.compile(DAY+TIME+r'(?:在'+OBJECT+r')?(?:开会|召开班会|集合|举行讲座|[A-Za-z]{1,8}赛|公开赛|分享会)')
WEEK_NOTICE = re.compile(r'第?\s*\d{1,2}\s*周(?:考试|补考)(?:日程|安排)(?:如上|见图|见附件|如下)?')
EXAM_HEADING = re.compile(r'第\s*(\d{1,2})\s*周考试安排')
TWO_CHECKINS = re.compile(r'这两个活动(今晚|明晚)签到[，,](?:没有|未)报名的同学请抓紧(?:时间|报名)[!！。]?')
COMPLEX = re.compile(r'如果|假如|若|如已|如未|仅限|仅需|只限|符合|针对|不含|免交|是否|请问|或者|除了|否则|另行|更正|改为|除外|无需|之前的|原定|已经|[？?]|同时|并(?:请|填写|提交|上传)|以及|及其|[、和与]|https?://')


def local_items(row):
    """None means defer. Every accepted item carries an exact source quote."""
    text = row['content']
    def item(kind, title, quote):
        due, note = due_date(quote, row.get('sent_at'))
        return dict(kind=kind, title=title, reason='本地规则与原文匹配', due=due, date_note=note,
                    evidence=[dict(row, message_id=row['id'], quote=quote)])

    # Keep a mixed notice together and suggest only explicit all-student actions.
    actor=r'(?:全体同学|全体学生|全体人员|全员|班干部|班长|学习委员|宿舍长|团支书|团员|党员)'
    plain=re.sub(r'^@全体成员\s*|\s*@全体成员$','',text.strip())
    from .course_notices import CODED_SCOPE, SCOPE_UPDATE
    if CODED_SCOPE.fullmatch(plain) or SCOPE_UPDATE.fullmatch(plain):
        return [item('通知',plain,plain)]
    duties=[part.strip() for part in re.split(r'[；;。！!\n，,]',plain) if part.strip()]
    duty=actor+r'(?:请|须|需|务必)?(?:参加|提交|填写|另交|缴纳|登记|领取|收集|上交)[\w《》]{1,24}'
    if duties and all(re.fullmatch(duty,d) and not re.search(r'已|未|但|除|仅|名单|符合|如果|之后|完成后|方可|条件|不用|无需|不必|请勿|及|或|和|并|否则|再由|另由|吗',d) for d in duties):
        general=[d for d in duties if d.startswith(('全体同学','全体学生','全体人员','全员'))]
        return [item('待办','；'.join(general),plain)] if general else [item('通知',plain,plain)]

    if '【图片文字识别' in text:
        original, *images = text.split('\n【图片文字识别，可能有误：')
        if not re.fullmatch(r'(?:\[图片[：:]未解析\]\s*)+', original.strip()):
            return None
        found = []
        for image in images:
            head = EXAM_HEADING.search(image[:450])
            if not head or not all(word in image for word in ('考试时间', '课程名称', '班级')):
                return None
            result = item('通知',head[0]+'（查看原图）',head[0])
            found.append(result)
            if '重修名单' in image:
                found.append(item('通知',head[0]+' · 跟班重修名单','重修名单'))
        return found or None

    if re.fullmatch(r'(?:\[图片[：:]未解析\]\s*|【图片未提取到可读文字，请勿推测具体日期、地点或科目】\s*)+',text):
        return []
    attached=re.match(r'^\[文件[：:]\s*([^\]\n]+)\]\s*\n【附件提取正文：[^\n]+】\n(.*)$',text,re.S)
    if attached and re.search(r'(?:自查表|登记表|空白模板)\.(?:docx?|xlsx?)$',attached[1],re.I):
        labels=re.sub(r'\s','',attached[2])
        if all(k in labels for k in ('班级','学号','姓名')) and not re.search(r'截止|请|务必|提交|上交|上传|改为|填写后|交给|\d{1,2}月\d{1,2}日',labels):
            return [item('资料',attached[1],attached[1])]
    body = re.sub(r'^@全体成员\s*|\s*@全体成员$', '', text.strip()).strip()
    body=re.sub(r'^\[其他消息类型：未解析\]|\[其他消息类型：未解析\]$', '', body).strip()
    # Require a complete numbered checklist. An unrecognized condition, heading
    # or footer changes the meaning of the list and must go to semantic review.
    lines=[line.strip() for line in body.splitlines() if line.strip()]
    if len(lines)>1 and text.lstrip().startswith('@全体成员'):
        entries=[]
        number=1
        for line in lines:
            numbered=re.fullmatch(r'(\d+)[.、．]\s*(完成[\w《》]{1,35}(?:表|作业|报告))',line)
            if numbered and int(numbered[1])==number:
                entries.append(item('待办',numbered[2],line))
                number+=1
            elif re.fullmatch(r'(?:资料|在线表格)链接[：:]\s*https?://[^\s]+',line):
                entries.append(item('资料',re.split('[：:]',line,1)[0],line))
            elif re.fullmatch(r'(?:宿舍长|班长|学习委员)收[\w]{1,24}(?:电子版|纸质版)给我',line):
                entries.append(item('待办',line,line))
            else:
                break
        else:
            if number>1 and not any(COMPLEX.search(x['title']) or NEGATIVE.search(x['title']) for x in entries):
                return entries
    # Role-specific requests must retain each addressee. Only bypass the
    # model when every sentence is an explicit request, header or courtesy.
    sentences=[s.strip() for s in re.split(r'[。！!\n]',body) if s.strip()]
    if len(sentences)>1 and not (COMPLEX.search(body) or NEGATIVE.search(body)):
        items=[]
        for sentence in sentences:
            if sentence in ('谢谢大家','谢谢','同学们好'):continue
            if re.fullmatch(r'[^。；;]{1,55}(?:工作开始了|安排如下)',sentence):
                items.append(item('通知',sentence,sentence))
            elif re.fullmatch(r'请(?:学习委员|班长|宿舍长|同学们|各班团支书)(?:负责|按照要求)?(?:收集|登录|提交|报送|核查)[^。；;]{1,70}',sentence):
                items.append(item('待办',sentence,sentence))
            else:break
        else:
            if items and all(x['evidence'][0]['quote'] in text for x in items):return items
    body = re.sub(r'^(?:通知|公告)[：:]\s*', '', body).rstrip('。！!').strip()
    if TWO_CHECKINS.fullmatch(body):
        return [item('通知',body,body)]
    if not body or len(body)>140:return None
    if re.fullmatch(r'(?:今天)?(?:体测|运动会)大家都累了[，,]好好休息吧[。！!]睡前(?:捏捏腿|放松一下)[。！!]?(?:\[face[：:]未解析\])?',body):
        return []
    early=re.fullmatch(r'('+DAY+r')有早八[，,](?:大家)?不要忘记去上(?:课|了)?',body)
    lessons=re.fullmatch(r'('+DAY+r')和('+DAY+r')早八上([\w]{1,20})的课[，,]在\s*([A-Za-z]\d{2,4})',body)
    if early or lessons:
        days=lessons.groups()[:2] if lessons else [early[1]]
        results=[]
        for day in dict.fromkeys(days):
            title=day+'08:00上课'+(' · '+lessons[3]+' · '+lessons[4] if lessons else '')
            entry=item('通知',title,body)
            entry['due'],entry['date_note']=due_date(day+'08:00',row.get('sent_at'))
            results.append(entry)
        return results
    if re.fullmatch(r'[A-Za-z0-9\u4e00-\u9fff]{1,16}作业到(?:左边|右边|讲台)(?:第[一二三四五]排)?(?:来拿|领取)',body) and not COMPLEX.search(body):
        return [item('待办',body,body)]
    delivery=re.fullmatch(r'(?:[^，,。；;]{1,28}审核时间已定[，,])?(请(?:各班团支书|班长|学习委员)'
        +DAY+r'将[\w]{1,20}送至[\w]{1,22})',body)
    if delivery and not (NEGATIVE.search(body) or COMPLEX.search(body)):
        return [item('待办',delivery[1],delivery[1])]
    if re.fullmatch(r'请(?:大家|同学们)?(?:相互|互相)转告(?:\[[^\]]+未解析\])?',body):
        return []
    if re.fullmatch(r'[A-Za-z\u4e00-\u9fff]{1,10}活动(?:记得|请)签到',body):
        return [item('待办',body,body)]
    if re.fullmatch(r'.{1,12}定位签到失败(?:的话)?[，,]可以关闭定位后再签到',body):
        return [item('资料',body,body)]
    pair=re.fullmatch(r'([^，,；;。]+)[，,]([^，,；;。]+)',body)
    if pair and TASK.fullmatch(pair[1]) and not COMPLEX.search(body):
        days=re.findall(DAY,pair[1])
        if len(days)==1 and re.fullmatch(TIME+r'(?:开会|召开班会|集合)',pair[2]):
            first=item('待办',pair[1],pair[1])
            later=item('通知',days[0]+pair[2],body)
            later['due'],later['date_note']=due_date(days[0]+pair[2],row.get('sent_at'))
            return [first,later]
    if prohibited_only(body) and re.fullmatch(
        r'(?:[^。；;，,！？!?\n]{0,45}(?:已取消|作废)[，,]\s*)?(?:请勿|切勿|无需|不必|禁止|不得|不用|不需要)(?:再)?(?:提交|填写|上传)'+OBJECT, body
    ):
        return [item('通知',body,body)]
    if NEGATIVE.search(body) or COMPLEX.search(body):
        return None
    if TASK.fullmatch(body) or DELIVERY.fullmatch(body):
        if re.search('已提交|提交过|完成了|过了|完了|好了|之后|以后|了吗|怎么办|之前的|就行吗|失败|不了',body) or body.endswith(('了','吗','呢')):return None
        if len(re.findall(r'填写|提交|上传|上交|缴费|报送',body))>1:return None
        return [item('待办',body,body)]
    if EVENT.fullmatch(body) or WEEK_NOTICE.fullmatch(body):
        return [item('通知',body,body)]
    return None


def supplement(items, row):
    """Keep independently verifiable resources and flag partial document coverage."""
    text=row['content']
    # A change notice may cite one week's exam as a reason, then announce a
    # different week's timetable. Keep those week anchors on their own quotes.
    schedule_week=r'第?\s*(\d{1,2})\s*周考试(?:日程|安排)'
    sentences=[s.strip() for s in re.split(r'[。！？\n]',text) if s.strip()]
    weekly=[s for s in sentences if re.search(schedule_week,s)]
    if len(text)<=600 and '【' not in text and len({w for s in weekly for w in re.findall(schedule_week,s)})>1 and all(
        len(set(re.findall(schedule_week,s)))==1 and len(s)<=150 for s in weekly
    ):
        items[:]=[i for i in items if not re.search(schedule_week,i['title']) and
                  not re.search(r'\d+周考试',i['title'])]
        for sentence in weekly:
            items.append(dict(kind='通知',title=sentence,reason='按原句分开保留不同教学周',
                due=None,date_note='',evidence=[dict(row,message_id=row['id'],quote=sentence)],local_source=True))
        for sentence in sentences:
            if sentence not in weekly and '重修考试' in sentence and '改为' in sentence and not any(
                '重修' in i['title'] and '跟班' in i['title'] for i in items
            ):
                items.append(dict(kind='通知',title=sentence[:160],reason='保留原文中的重修变更和适用范围',
                    due=None,date_note='',evidence=[dict(row,message_id=row['id'],quote=sentence)],local_source=True))
    activity=re.fullmatch(r'((?:这|那)(?:两|几)个活动(?:今晚|明晚|今天|明天)签到)[，,](没有报名的同学请抓紧时间)[!！。]?',text.strip())
    if activity and not items:
        items.append(dict(kind='通知',title=activity[0],reason='活动名称需结合原通知核对',
            due=None,date_note='',evidence=[dict(row,message_id=row['id'],quote=activity[0])],local_source=True))
    link=re.search(r'(?:在线表格链接|课件链接|资料下载)[：:]\s*https?://[^\s，。；]+',text)
    if link and not any(item['kind']=='资料' and link[0] in ref['quote'] for item in items for ref in item.get('evidence',[])):
        items.append(dict(kind='资料',title=link[0].split('http')[0].rstrip('：: '),
            reason='原文提供资料链接',due=None,date_note='',
            evidence=[dict(row,message_id=row['id'],quote=link[0])],local_source=True))
    # A short excerpt cannot establish that all obligations in a long document
    # were extracted. Keep an explicit entry even when the model returns none.
    attachment=re.search(r'【附件提取正文：([^\n]+?)】',text)
    if attachment and len(text)>1600:
        from .responsibilities import document_duties
        document=document_duties(text[attachment.end():])
        actors={d['actor'] for d in document['duties']} - {'执行人待核对'}
        caption=text[:attachment.start()].strip()
        if re.fullmatch(r'\[文件[：:][^\]\n]+\]',caption) and len(actors)>=2:
            # Keep one notice per source. Mixing model paraphrases into personal
            # tasks can silently drop a qualifying cohort or move a deadline.
            # Preserve its validated quotes for details alongside local clauses.
            quotes=list(dict.fromkeys(ref['quote'] for item in items for ref in item['evidence']
                                       if ref['message_id']==row['id']))
            title=re.search(r'关于[^\n]{3,65}?的通知',attachment[1])
            items[:]=[dict(kind='通知',title=title[0] if title else attachment[1],
                reason='附件包含多个执行人，按原文查看分工与适用条件',due=None,date_note='',
                evidence=[dict(row,message_id=row['id'],quote=q) for q in (quotes or [attachment[1]])])]
            return items
        for pattern in (r'[^。；;，,\n]{1,16}学生须(?:及时|按规定)?办理[^。；;，,\n]{2,20}手续',
                        r'表格经[^。；;，,\n]{2,36}签字确认后留存[^。；;，,\n]{1,16}'):
            for match in re.finditer(pattern,text):
                quote=match[0].strip()
                if not any(quote in item['title'] for item in items):
                    items.append(dict(kind='待办',title=quote,reason='附件原文明确了办理对象和要求',
                        due=None,date_note='',evidence=[dict(row,message_id=row['id'],quote=quote)],local_source=True))
        if not any(item['kind']=='资料' and ('附件' in item['title'] or attachment[1] in item['title']) for item in items):
            items.append(dict(kind='资料',title=('核对附件完整要求 · '+attachment[1])[:160],
                reason='附件较长，摘要仅覆盖部分条款',due=None,date_note='',
                evidence=[dict(row,message_id=row['id'],quote=attachment[1])],local_source=True))
    return items
