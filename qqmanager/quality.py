"""Conservative noise filtering and reversible display grouping; never deletes originals."""
import re
from datetime import timedelta
from .analysis import timestamp

POLICY_VERSION = 2

def readable(text):
    return re.sub(r'\[[^\]\n]{0,40}[：:]未解析\]', '', str(text)).strip()

def noise_reason(text):
    text = readable(text)
    # A mixed message can contain a real instruction alongside a bot quotation.
    actionable = bool(re.search(r'(?:通知|公告|请|务必|必须|需要).{0,45}(?:提交|填写|缴费|停课|停电|维护|开会|集合|报名|更新)', text, re.S))
    if not re.search(r'[\w\u4e00-\u9fff]', text):
        return '无可解析正文'
    if re.fullmatch(r'(?:@\S+\s*)?[/!！.](?:今日运势|运势|签到|抽卡|互动|随机\S*|今日老婆|今日老公|今日猪猪)(?:\s.*)?', text):
        return '机器人娱乐指令'
    if re.fullmatch(r'@\S+', text):
        return '仅提及成员'
    fortune = sum(bool(re.search(p, text)) for p in (r'财运\s*[:：]\s*\d+', r'桃花运\s*[:：]\s*\d+', r'事业运\s*[:：]\s*\d+'))
    if not actionable and (fortune >= 2 or re.search(r'(?:您|你).{0,4}(?:今日|今天)的?运势|(?:今日|今天)运势[：:]|免艾特游玩|抽卡结果|抓到一只.{0,12}猪', text)):
        return '运势、抽卡等机器人娱乐结果'
    if not actionable and re.search(r'语录\s*[\n：:]|签到成功|今日已签到', text):
        return '机器人日常互动'
    compact = re.sub(r'[\W_]+', '', text).casefold()
    if re.fullmatch(r'(?:嗯|哦|好|哈|哈哈|收到|好的|谢谢|晚安|早安|早上好|中午好|下午好|晚上好|真的|神了|废了|确实|笑死|ok|okk|666|1|\+1)+', compact) or (len(compact) <= 5 and compact.isascii()):
        return '寒暄或简短回应'
    if re.search(r'^(?:我|本人)?(?:今天|明天|今晚|明晚|后天).{0,12}(?:睡觉|吃饭|打游戏)[了吧呀啊。！!]*$', text):
        return '个人闲聊安排'
    if re.fullmatch(r'(?:领不到|抢不到|没抢到|人太多了?|\d+点刚开现在领的太多了|现在领的人太多了)[啊呀了。！!]*', text):
        return '活动领取闲聊，未提供可操作信息'
    if re.search(r'会员|优惠券|返利|红包|优惠|促销|抽奖|送.{0,8}会员', text):
        usable = re.search(r'https?://\S+|(?:入口|路径|步骤|方式)[：:]|(?:截止|截至).{0,16}(?:\d|明天|今天)|(?:打开|进入).{2,30}(?:点击|选择|领取)', text)
        if not actionable and not usable:
            return '泛优惠或福利播报，缺少领取方式、期限等有效信息'
    return ''

def duplicate_key(text):
    text = readable(text).casefold()
    text = re.sub(r'^(?:(?:再次提醒|再提醒一次|温馨提醒|转发|通知|公告|提醒)[：:\s]*)+', '', text)
    text = re.sub(r'请(?:各位|大家|同学们)?', '', text)
    text = re.sub(r'(?:麻烦|辛苦)(?:大家|各位)?', '', text)
    text = re.sub(r'(?:谢谢(?:大家)?|感谢配合)[。！!\s]*$', '', text)
    return re.sub(r'[\s，,。.!！：:；;、]+', '', text)

def same_notice(a, b):
    if a['message_id']==b['message_id']:
        return False
    if a['kind'] != '通知' or b['kind'] != '通知':
        return False
    # Never merge across accounts or groups, including similarly named groups.
    scope = lambda x: (x.get('live_account') or '', x.get('live_group') or x['group_name'])
    if scope(a) != scope(b) or (a['status']=='dismissed') != (b['status']=='dismissed'):
        return False
    ta,tb=timestamp(a.get('sent_at')),timestamp(b.get('sent_at'))
    if not ta or not tb or abs(ta-tb)>timedelta(hours=24):
        return False
    # Repeated relative dates on a later day describe a different occurrence.
    if ta.date()!=tb.date() and re.search(r'今天|今日|明天|今晚|明晚|后天',a['content']+b['content']):
        return False
    if a.get('due')!=b.get('due'):
        return False
    # Only formatting, salutations and reminder prefixes are normalized.
    # Semantic similarity alone is insufficient: changes in place/recipient/requirements matter.
    key=duplicate_key(a['content'])
    return len(key)>=6 and key==duplicate_key(b['content']) and duplicate_key(a['title'])==duplicate_key(b['title'])

def merge_notices(rows):
    """Merge display rows, retaining stable IDs, user state and every original source."""
    groups=[]
    for row in sorted(rows,key=lambda x:x['id']):
        row=dict(row)
        match=next((g for g in reversed(groups) if same_notice(g,row)),None)
        if match:
            match['merged_sources'].append(row)
            match['merged_ids'].append(row['id'])
            match['merged_count']+=1
            if row['status']=='saved' or (row['status']=='read' and match['status']=='new'):
                match['status']=row['status']
        else:
            row.update(merged_sources=[],merged_ids=[row['id']],merged_count=1)
            groups.append(row)
    return sorted(groups,key=lambda x:(x['score'],x['id']),reverse=True)
