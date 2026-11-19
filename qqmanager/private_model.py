"""Minimize model input and keep a request-scoped, reversible vocabulary for evidence."""
import re

# Public scheduling vocabulary. Everything else (including arbitrary names and
# uncommon words) becomes an opaque reference; no name dictionary is required.
WORDS = """
通知 待办 资料 消息 群聊 公告 作业 报告 实验 报名表 表格 表 填写 收集 确认 反馈 核对
提交 交给 上交 交齐 上传 完成 缴纳 缴费 报送 截止 截至 最迟 之前 提前 参赛 报名
学校 学院 班级 同学 老师 教师 学生 班长 宿舍长 学习委员 团支书 各班 全体成员 大家 各位
无需 请勿 尚未 不必 取消 作废 不要 不再 禁止 不能 切勿 暂停 暂缓 忘记 记得 务必
如果 如果已经 还未 已经 还要 改为 更正 改交 更新 延期 推迟 延后 改到 调至 调到 调整 通知如下
课程名称 考试时间 监考教师 考场 人数 地点 课程 教室 调课 上课 停课 补课 节次 学期
日期 周次 星期 学年 考试 安排 日程 原定 校历 开学 放假 校区 单周 双周 单双周 春季 秋季 春 秋
科目 重修 跟班 名单 讲座 分享会 比赛 公开赛 开会 班会 会议 集合 签到 活动 领取 参加
链接 在线表格 附件 图片 原图 文件 资料 文档 模板 方法 教程 步骤 下载 获取 如上 上图 见图
学位 学业 核查 自查 统计 材料 读书笔记 审核 实验报告 电子版 纸质版 复习 报名表 作业
今天 今日 今晚 明天 明日 明晚 后天 下周 本周 这周 下星期 周末 早八 上午 下午 中午 晚上 早上 凌晨
下次 这次 上次 前面 原来 原先 照旧 按照 另行 其余 但是 而是 同时 只要 仍然 单独 只需
今年 本学期 统一 送至 送到 交到 纸质 线上 线下 再次 暂时 群里 尚未 对应 沿用 参加者
无需到场 到场 失败 定位 关闭 睡觉 休息 帮忙 代签 请问 学年度 签到失败 另外 可 可以 无须 使用 选择 导入 解决方法 步骤 不用到场
课 上 原文 公布 见 看 查看 更多 另有 负责 登录 教务系统 核查工作 具体要求 抓紧时间 统一交 全部 材料清单 自查表
来拿 来取 第一排 左边 右边 变动 补充 见下文 请抓紧时间 没有报名 其他消息类型 未解析 全体成员 教学周 单节 调到 晚自习
现在 这份 那份 这项 申请 补交 补报 重发 旧版 新版 审批 签字 打印
学籍异动 不含 专转本 级 建筑学专业 课程代码 不一致 办理 课程替代 手续 各学院 学生本人 辅导员 学院领导 教务处 留存 复核 核查对象 适用范围 仅限 例外 免交 受处分 未通过 绩点 核验 修读 登记 签字确认 报送至
请 需 要 未 已 于 至 前 后 由 给 在 到 从 改 原 先 再 别 不 无 并 将 按 把 向 为 和 或 的 了 是 也 都 交 换
年 月 日 号 周 第 节 点 时 分 半 一 二 三 四 五 六 七 八 九 十 天
""".split()
LITERAL = re.compile('|'.join(map(re.escape,sorted(set(WORDS),key=len,reverse=True)))+
    r'|(?<!\d)20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|(?<!\d)20\d{2}(?=年)|(?<!\d)\d{1,2}[:：]\d{2}(?!\d)|(?<![0-9A-Za-z])\d{1,2}(?=[:：点时年月日号周节分/-])'
    r'|[\s，,。；;：:！!？?、（）()《》—–~/-]+')
PRIVATE = re.compile(r'(?:\d{1,3}°[^\n，。]{0,22}?[NS]\s*\d{1,3}°[^\n，。]{0,22}?[EW])|(?:\d{1,3}\.){3}\d{1,3}|https?://[^\s，。；<>]+|[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)\d{5,}[Xx]?|@[\w.-]+')
ALIAS = re.compile(r'〔词\d+〕')
SCOPE_WORDS = re.compile(r'提交|截止|报名|日期|通知|上交|作业|会议|开会|上课|调课|调整|教室|周[一二三四五六日]|考[试场]|导入|学业|读书笔记|取消|改为|延期|附件|材料|请勿|负责|文件|签到')
REFERENCE = re.compile(r'如上|如图|上图|见图|见附件|上条|上一条|上述|上面|刚才|照旧|同上|前面|改到|改为|改在|改成|还是原来|那个|这份|那份')


def excerpt(text, limit):
    """Keep complete source lines for quotes. Exclude unrelated table rosters."""
    if '【图片文字识别' in text or '【附件提取正文' in text:
        text=re.split(r'(?:\n\s*(?:跟班重修名单|学生姓名.{0,4}学号|学号.{0,4}姓名))',text,maxsplit=1)[0].strip()
    if len(text)<=limit:return text
    if '【附件提取正文' in text:
        from .responsibilities import document_duties
        doc=document_duties(text)
        if doc['duties']:
            snippets=[text.splitlines()[0],doc['scope']]
            priority=lambda d:bool(d['condition'] or re.search(r'报送|签字|日前|学生登录',d['quote']))
            snippets.extend(d['quote'] for d in sorted(doc['duties'],key=priority,reverse=True))
            kept=[]
            size=0
            for part in dict.fromkeys(snippets):
                if part and size+len(part)+1<=limit:
                    kept.append(part)
                    size+=len(part)+1
            return '\n'.join(kept)
    lines=re.split(r'(?<=[。！？；])|\n',text)
    selected=[]
    for line in lines:
        if line.strip() and (not selected or SCOPE_WORDS.search(line)):
            selected.append(line[:limit])
    return '\n'.join(selected)[:limit]


class WireText:
    def __init__(self):
        self.tokens={}
        self.originals={}

    def alias(self, text):
        if text not in self.tokens:
            token=f'〔词{len(self.tokens)+1}〕'
            self.tokens[text]=token
            self.originals[token]=text
        return self.tokens[text]

    def mask(self, text):
        parts=[]
        def general(section):
            pos=0
            for match in LITERAL.finditer(section):
                if match.start()>pos:parts.append(self.alias(section[pos:match.start()]))
                parts.append(match[0])
                pos=match.end()
            if pos<len(section):parts.append(self.alias(section[pos:]))
        pos=0
        for match in PRIVATE.finditer(text):
            general(text[pos:match.start()])
            parts.append(self.alias(match[0]))
            pos=match.end()
        general(text[pos:])
        return ''.join(parts)

    def encode(self, value):
        if isinstance(value,str):return self.mask(value)
        if isinstance(value,list):return [self.encode(v) for v in value]
        if isinstance(value,dict):
            return {k:(v if k=='kind' and v in ('courses','calendar') else self.encode(v)) for k,v in value.items()}
        return value

    def restore(self, value):
        if isinstance(value,str):return ALIAS.sub(lambda m:self.originals.get(m[0],m[0]),value)
        if isinstance(value,list):return [self.restore(v) for v in value]
        if isinstance(value,dict):return {k:self.restore(v) for k,v in value.items()}
        return value
