import csv
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .analysis import timestamp

MAX_FILE = 20 * 1024 * 1024
TEXT_EXT = {'.txt', '.md', '.csv', '.json', '.log'}
WATCH_EXT = TEXT_EXT | {'.pdf', '.docx'}


def decode(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            return data.decode('utf-16')
        except UnicodeError:
            raise ValueError('UTF-16 文本不完整，请重新导出')
    for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    raise ValueError('无法识别文本编码，请转换为 UTF-8')


def segments(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ''
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        typ, data = item.get('type', ''), item.get('data', {})
        if typ == 'text':
            parts.append(str(data.get('text', '')))
        elif typ == 'at':
            parts.append('@全体成员' if data.get('qq')=='all' else '@' + str(data.get('qq', '')))
        elif typ == 'json':
            raw = data.get('data')
            try:
                card = json.loads(raw) if isinstance(raw,str) else raw
                news = (card.get('meta') or {}).get('news') or {}
                title = news.get('title') or card.get('prompt')
            except (ValueError,AttributeError):
                title = None
            parts.append(('[分享卡片] ' + str(title or '')[:300]).strip())
        elif typ in ('face','mface','bface','sface','sticker'):
            parts.append('[表情]')
        elif typ == 'file':
            parts.append('[文件：' + str(data.get('name', data.get('file', '未知文件'))) + ']')
        elif typ == 'image':
            parts.append('[图片：未解析]')
        else:
            parts.append('[' + typ + '：未解析]')
    return ''.join(parts)


def normalize(row, group):
    if not isinstance(row, dict):
        raise ValueError('每条消息须为 JSON 对象')
    content = segments(row.get('content', row.get('message', row.get('text', row.get('raw_message', '')))))
    if not content.strip():
        raise ValueError('消息缺少 content / message / text 正文')
    sender = row.get('sender', row.get('nickname', '未知发送者'))
    if isinstance(sender, dict):
        sender = sender.get('card') or sender.get('nickname') or str(row.get('user_id', '未知发送者'))
    dt = timestamp(row.get('timestamp', row.get('time', row.get('sent_at'))))
    result = {'group_name': str(row.get('group_name') or row.get('group_id') or group), 'sender': str(sender), 'sent_at': dt.isoformat() if dt else None, 'content': content.strip()}
    if row.get('message_id') is not None:
        result['event_id'] = str(row['message_id'])
    return result


DATE = r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?'
HEAD = re.compile(r'^\s*(' + DATE + r')\s+(.+?)\s*$')
TAIL = re.compile(r'^\s*(.+?)\s+(' + DATE + r')\s*$')


def parse_chat(name, data, group='未分组'):
    text = decode(data)
    ext = Path(name).suffix.lower()
    if ext == '.json':
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError('JSON 格式无效：' + str(exc))
        if isinstance(rows, dict):
            rows = rows.get('messages', rows.get('message_list', [rows]))
        if not isinstance(rows, list) or not rows:
            raise ValueError('JSON 需要非空消息数组，或包含 messages 数组的对象')
        return [normalize(row, group) for row in rows]
    if ext == '.csv':
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ValueError('CSV 为空；需要 content、sender、timestamp、group_name 表头')
        return [normalize(row, group) for row in rows]
    result, current, body = [], None, []

    def flush():
        if current and '\n'.join(body).strip():
            result.append(normalize(dict(current, content='\n'.join(body)), group))

    for line in text.splitlines():
        found = re.match(r'^(?:消息对象|群名称)[：:]\s*(.+)', line)
        if found:
            group = found[1].strip()
            continue
        a, b = HEAD.match(line), TAIL.match(line)
        if a or b:
            flush()
            current = {'timestamp': a[1] if a else b[2], 'sender': a[2] if a else b[1], 'group_name': group}
            body = []
        elif current:
            body.append(line)
    flush()
    if not result:
        chunks = re.split(r'\n\s*\n', text.strip())
        result = [normalize({'content': x}, group) for x in chunks if x.strip()]
    if not result:
        raise ValueError('没有可导入的聊天内容')
    return result


def extract_file(name, data):
    ext = Path(name).suffix.lower()
    if ext in TEXT_EXT:
        return decode(data)[:1_000_000], '已索引正文'
    if ext == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            item = archive.getinfo('word/document.xml')
            if item.file_size > MAX_FILE:
                raise ValueError('DOCX 解压后正文超过 20 MB')
            root = ElementTree.fromstring(archive.read(item))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(''.join(n.text or '' for n in p.findall('.//w:t', ns)) for p in root.findall('.//w:p', ns))
            return text[:1_000_000], '已索引正文' if text.strip() else '未提取到文字'
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            return '', '仅文件名：未安装 PDF 解析依赖'
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return '', '仅文件名：PDF 已加密'
        text = '\n'.join(page.extract_text() or '' for page in reader.pages[:100])[:1_000_000]
        return text, ('已索引正文（最多前 100 页）' if text.strip() else '仅文件名：扫描件需要 OCR，第一版未支持')
    return '', '仅索引文件名：暂不支持此格式正文'
