"""Ground personal suggestions and document responsibilities in the local source."""
import json
import re

from .local_notice import local_items

ROLE = re.compile(r'负责人|操作人员|各学院|各教学单位|班主任|辅导员|学院领导|教师|老师|团支书|班长|班委|委员|宿舍长|组长|管理员|代表|队长|团员|党员')
LIMIT = re.compile(r'如果|若|仅|只限|只针对|符合|除外|不含|不包括|无需|不用|免交|未报名|报名的|参赛|重修|缓考|补考|转专业|学籍异动|专转本|有意|自愿|[0-9一二三四五六七八九]+[级班]|的同学|的学生|同学中|视情况|[（(]')
ATTACHMENT = re.compile(r'\[文件[：:]|【附件提取正文|【图片文字识别')
ACTION = re.compile(r'办理|完成|登录|提交|审核|核查|核验|校对|填报|上报|报送|签字|收集|确认|核对|送至|送交|上传|留存|自查|缴费|发放|签到|免交|无需')
SUBJECT = re.compile(r'(?:学生本人、辅导员、学院领导|全体团员|全体党员|共青团员|正式党员|预备党员|党员|团员|各学院|学院|各班团支书|团支书|学习委员|宿舍长|辅导员|班长|操作人员|学籍异动学生|学生本人|同学们|学生|教师|老师)')
SCOPE = re.compile(r'(?:核查对象|适用对象|适用范围|面向对象|参加对象)[：:\s]*([^。\n]{2,180})')


def personal_actions(row):
    """Only a complete source template can establish a direct group instruction.

    A model title or an isolated quote can omit the addressee in the preceding
    sentence. Recheck the original even for old/imported findings.
    """
    if row['kind'] not in ('待办','通知') or ATTACHMENT.search(row['content']):
        return []
    body=row['content']
    if LIMIT.search(body) or re.search(r'@(?!全体成员)|对象[：:]|仅限|^(?:致|转发给)',body):
        return []
    parsed=local_items(dict(id=row['message_id'],content=body,sent_at=row['sent_at']))
    if not parsed:
        return []
    tasks=[p for p in parsed if p['kind']=='待办']
    refs=json.loads(row.get('evidence') or '[]')
    quotes={r.get('quote','').strip() for r in refs if r.get('message_id')==row['message_id']}
    allowed=[]
    for p in tasks:
        title=p['title']
        if ROLE.search(title):
            continue
        if not (title.startswith(('全体同学','全体学生','全体人员','全员')) or re.match(r'^(?:请(?:大家|同学们|各位同学)?(?:抓紧时间|按时)?(?:在|于|完成|按照要求登录|提交|上传|填写|缴纳|上交|报送|明天|今天|下周|周|后天|\d)|务必|记得)',title)
                or re.match(r'^(?:完成|[A-Za-z0-9]+作业到)',title) and '@全体成员' in body):
            continue
        if any(ROLE.search(x['title']) or LIMIT.search(x['title']) for x in parsed if x['kind']=='通知'):
            continue
        exact_quote=any(r['quote'].strip() in quotes for r in p['evidence'])
        if (row['title']==title or row['title']==body or
                (row['kind']=='待办' and (len(tasks)==1 or exact_quote or title in row['title']))):
            allowed.append(title)
    return allowed


def document_duties(text):
    """Keep full source sentences and explicit scope for review, including exceptions.

    Missing or inherited subjects are left for review. No document row implies
    the reader personally holds the named role or meets a condition.
    """
    scope=SCOPE.search(text)
    limits=scope[1].strip() if scope else ''
    duties=[]
    previous_form_owner=''
    for sentence in re.split(r'[。！？]',text):
        if not ACTION.search(sentence):
            previous_form_owner=''
            continue
        # Remove document headings, never discard a numbered obligation.
        sentence=re.sub(r'(?m)^[ \t]*(?:[一二三四五六七八九]+、|[（(][一二三四五六七八九]+[)）]|\d+[.．] *)[^，。：；\n]{1,18}\n(?=\s*(?:各学院|学院|学生|辅导员|操作人员))', '', sentence)
        clauses=re.split(r'[；;]|[，,](?=表格经|由(?:各学院|辅导员|班长|宿舍长))',sentence.strip())
        owner=''
        form_owner=''
        for part in clauses:
            quote=re.sub(r'^\s*(?:[（(]\s*\d+\s*[)）]|\d+[.．])\s*','',part).strip()
            if not quote or not ACTION.search(quote) or '主题词：' in quote:
                continue
            subject=re.match(r'(?:请|由)?('+SUBJECT.pattern+r')',quote)
            if subject:
                owner=subject[1]
            elif quote.startswith('《') and re.search(r'报送至|上报至',quote):
                owner=previous_form_owner
            elif re.match(r'休复学、.{0,24}学籍异动学生',quote):
                owner='学籍异动学生'
            elif not owner and not re.match(r'(?:若|如|已|未|仅|除|须|必须|请|表格经|重点关注)',quote):
                continue
            condition=''
            conditional=re.match(r'((?:若|如(?:果)?|仅限|已|未|除)[^，,；;]{1,60}|学生[^，,；;]{1,42}(?:不一致的|情形的|情况的))[，,]',quote)
            if conditional:
                condition=conditional[1]
                remaining=quote[conditional.end():]
                explicit=re.match(r'(?:请|由)?('+SUBJECT.pattern+r')',remaining)
                if explicit:
                    owner=explicit[1]
            subgroup=re.match(r'((?:已|未|符合|参加)[^，。；]{1,24}?)的(学生|同学)(?=无需|不用|须|需|请|可)',quote)
            if subgroup:
                owner=subgroup[2]
                condition=subgroup[1]
            if owner=='学籍异动学生':
                condition=condition or '学籍异动'
            nested=re.match(r'(各学院|学院|老师|辅导员)(通知|要求|组织)(学生|同学们)(?=登录|填写|提交|完成)',quote)
            action=quote
            if nested:
                duties.append(dict(actor=nested[1],condition=condition,action=quote[nested.start(2):],quote=quote))
                owner=nested[3]
                action=quote[nested.end():]
            if owner in ('各学院','学院') and '填报' in quote:
                form_owner=owner
            signatures=re.search(r'经([^，。；]{1,40})签字确认后留存([^，。；]{1,12})',quote)
            if signatures:
                duties.append(dict(actor=signatures[1],condition='',action='签字确认',quote=signatures[0].split('后留存')[0]))
                duties.append(dict(actor=owner or '执行人待核对',condition='签字确认后',action='留存'+signatures[2],quote=signatures[0]))
                continue
            if not owner and not conditional:
                owner='执行人待核对'
            duties.append(dict(actor=owner or '执行人待核对',condition=condition,action=action,quote=quote))
        previous_form_owner=form_owner
    return dict(scope=limits,duties=duties)
