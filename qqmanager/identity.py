"""Local academic profile and exam audience matching."""
import json
import re
import unicodedata

from .local_notice import local_items

ACADEMIC_FIELDS = ('grade', 'major', 'class_name')
ALL=re.compile(r'@全体成员|全体(?:成员|人员|学生|同学)|所有(?:人|学生|同学)|全员|@所有人')
REFERENCE=re.compile(r'这(?:两|几|些|个|项|份)|那(?:两|几|些|个|项|份)|相关(?:人员|同学)|有关(?:人员|同学)|上述(?:人员|同学)|名单中|按之前|照旧|如上|相互转告|互相转告|按刚才|该交的|还没交的')
VERB=re.compile(r'参加|另交|交纳|缴纳|登记|转告|转发|提交|填写|上交|上传|完成|报送|收集|收取|收.{0,16}电子版|领取|来拿|签到|送至|登录|核查|办理|缴费|报名|签字|反馈|交给')
STAFF={'各学院','学院','教师','老师','学院领导','辅导员','操作人员','各教学单位'}
BROAD={'','全体人员','全体成员','全体学生','全体同学','所有人','所有同学','所有学生','学生','学生本人','同学','大家','同学们','各位同学','全员','全体','全体师生'}


def account(s):
    return str(s.setting('ws_account') or '')


def profile(s):
    stored = s.setting('identity:' + account(s))
    if stored is None:
        existing=s.query("SELECT 1 FROM settings WHERE key LIKE 'identity:%' LIMIT 1")
        stored={} if existing else (s.setting('academic_calendar') or {})
    return {field: stored.get(field, '') for field in ACADEMIC_FIELDS}


def save_profile(s,body):
    fields={}
    for key,limit in (('grade',4),('major',80),('class_name',80)):
        value=body.get(key,'')
        if not isinstance(value,str) or len(value.strip())>limit or any(ord(c)<32 for c in value):
            raise ValueError('学籍信息无效')
        fields[key]=value.strip()
    if fields['grade'] and not re.fullmatch(r'(?:19|20)\d{2}',fields['grade']):
        raise ValueError('年级请填写四位入学年份')
    with s.lock:
        if body.get('account')!=account(s):raise ValueError('账号已切换，请重新打开设置')
        s.set_setting('identity:'+account(s),fields)
    return fields


def clean(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold()


def match_one(scope,p):
    """True, False or unknown. Only consume recognized, fully scoped grammar."""
    text=clean(scope).strip('：:，,。')
    text=re.sub(r'^(?:请|各位|面向|仅限|由)','',text)
    if text in BROAD:return True
    if text in STAFF:return False
    text=text.removeprefix('全体')
    if re.search(r'除|以外|不含|之外|除外|不包括',text):return None
    if text=='学生本人、辅导员、学院领导':return True
    text=re.sub(r'(?:全体)?(?:学生|同学们|同学)$','',text).removesuffix('的')
    checks=[]
    grade=re.match(r'(20\d{2})级',text)
    if grade:
        checks.append(p['grade']==grade[1] if p['grade'] else None)
        text=text[grade.end():]
    major=re.match(r'([\w]{1,35}?)专业',text)
    if major:
        checks.append(clean(p['major']).removesuffix('专业')==major[1] if p['major'] else None)
        text=text[major.end():]
    if re.search(r'班长|委员|宿舍长|团支书|班委|班干部|党员|团员',text):
        return False if False in checks else None
    if p['class_name'] and text.startswith(clean(p['class_name'])):
        text=text[len(clean(p['class_name'])):].removeprefix('班')
        checks.append(True)
    else:
        classroom=re.match(r'((?!班长|班委)[\w]{1,30}?班)(?!长|委)',text)
        if classroom:
            value=clean(p['class_name']).removesuffix('班')
            checks.append(value==classroom[1][:-1] if value else None)
            text=text[classroom.end():]
    if text:return None
    if False in checks:return False
    return None if None in checks or not checks else True


def match_scope(scope,p):
    if not scope:return True
    value=clean(scope)
    # An exclusion belongs to its cohort. A matching grade alone cannot
    # establish eligibility when transfer/admission history is not in the profile.
    parts=re.split(r'[,，]|(?<=[级)])、(?=20\d{2}级)',value)
    verdicts=[]
    for part in parts:
        m=re.fullmatch(r'((?:20\d{2}、)*20\d{2})级(\(不含[^()]+\))?(?:学生)?',part)
        if m:
            verdict=None if not p['grade'] else p['grade'] in m[1].split('、')
            verdicts.append(None if verdict and m[2] else verdict)
        else:
            verdicts.append(match_one(part,p))
    if len(parts)>1 and not all(re.match(r'20\d{2}',part) for part in parts):return None
    return True if True in verdicts else None if None in verdicts else False


def retained(text):
    from .quality import readable
    body=readable(text)
    if not ALL.sub('',body).strip(' ，,。.!！'):return False
    if REFERENCE.search(body) and not re.search(r'使用的参数|帮助命令',body):return True
    if AUDIENCE_WORD.search(body) and VERB.search(body) and not re.match(r'^(?:我|本人|刚刚)',body):return True
    return bool(ALL.search(body) and local_items(dict(id=0,content=text,sent_at=None))!=[])


AUDIENCE_WORD=re.compile(r'全体|所有同学|同学们|班干部|班委|班长|学习委员|宿舍长|团支书|团员|党员|20\d{2}级|专业|班|相关人员|辅导员|各学院|老师')


def migrate(s):
    s.db.execute('''CREATE TABLE IF NOT EXISTS identity_answers (
        account TEXT NOT NULL, clause_key TEXT NOT NULL, profile_key TEXT NOT NULL,
        insight_id INTEGER NOT NULL REFERENCES insights(id), task_id INTEGER UNIQUE REFERENCES tasks(id),
        PRIMARY KEY(account,clause_key,profile_key,insight_id))''')
    # Existing user-created tasks still use these links to their source notices.
    with s.db:
        for row in s.query("SELECT key,value FROM settings WHERE key LIKE 'identity:%'"):
            old=json.loads(row['value'])
            academic={field:old.get(field,'') for field in ACADEMIC_FIELDS}
            if academic!=old:
                s.db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(academic),row['key']))
