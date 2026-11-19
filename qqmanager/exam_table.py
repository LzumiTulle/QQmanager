"""Read a weekly exam table using the geometry of its own OCR cells."""
import re
from datetime import timedelta
from . import planner


WEEK_HEADING = re.compile(r'第\s*(\d+)\s*周\s*考试')
DATE_CELL = re.compile(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]')
TIME_RANGE = re.compile(r'([0-2]?\d)[:：]([0-5]\d)\s*[-–—~至]\s*([0-2]?\d)[:：]([0-5]\d)')
HEADERS = {'考试时间', '考场', '课程名称', '班级', '地点'}


def time_groups(time_cells, monday, message_id):
    """Each printed date and the time range below it form a merged cell."""
    dates = []
    for index, (_, text) in enumerate(time_cells):
        match = DATE_CELL.fullmatch(re.sub(r'\s', '', text))
        if match:
            dates.append((index, match))
    week = [monday + timedelta(days=n) for n in range(7)]
    groups = []
    for n, (index, match) in enumerate(dates):
        year, month, day = match.groups()
        exam_day = next((d for d in week if (d.month, d.day) == (int(month), int(day))), None)
        if not exam_day or (year and int(year) != exam_day.year):
            return []
        stop = dates[n + 1][0] if n + 1 < len(dates) else len(time_cells)
        details = time_cells[index + 1:stop]
        weekdays = re.findall(r'(?:周|星期)\s*([一二三四五六日天])', ' '.join(t for _, t in details))
        if any(w.replace('天', '日') != '一二三四五六日'[exam_day.weekday()] for w in weekdays):
            return []
        clocks = [(y, t, m) for y, t in details if (m := TIME_RANGE.fullmatch(t))]
        if len(clocks) != 1:
            return []
        clock_y, clock_text, clock = clocks[0]
        start = f'{int(clock[1]):02d}:{clock[2]}'
        finish = f'{int(clock[3]):02d}:{clock[4]}'
        if not start < finish < '24:00':
            return []
        date_y, date_text = time_cells[index]
        evidence = [dict(message_id=message_id, quote=t) for t in (date_text, clock_text)]
        session = dict(date=exam_day.isoformat(), start_time=start, end_time=finish, evidence=evidence)
        groups.append(((date_y + clock_y) / 2, session))
    return groups


def exam_rows(headers, body, groups, message_id):
    if not HEADERS.issubset(headers):
        return []

    # Use narrow bands around the printed headers to omit unrelated cells.
    def column(name, radius):
        x_header = headers[name][0]
        return sorted((y, x, t) for x, y, t in body if abs(x - x_header) < radius)

    classes = column('班级', .066)
    if not classes:
        return []
    numbers = column('考场', .035)
    courses = column('课程名称', .085)
    locations = column('地点', .072)

    def same_row(cells, index):
        y = classes[index][0]
        gaps = [abs(y - classes[j][0]) for j in (index - 1, index + 1) if 0 <= j < len(classes)]
        tolerance = min(gaps, default=.04) * .42
        return [text for _, text in sorted((x,t) for cy,x,t in cells if abs(cy-y)<=tolerance)]

    # Numbering restarts at each merged time cell. Missing digits don't shift
    # rows. Each legible room number independently identifies its first row.
    starts = {0}
    for index in range(len(classes)):
        number = same_row(numbers, index)
        if len(number) == 1 and re.fullmatch('[0-9]{1,3}', number[0]):
            starts.add(index + 1 - int(number[0]))
    bounds = sorted(starts) + [len(classes)]
    if bounds[0] != 0 or len(bounds) != len(groups) + 1:
        return []
    rows = []
    for i, (center, session) in enumerate(groups):
        a, b = bounds[i:i + 2]
        if not a < b <= len(classes) or not classes[a][0] - .02 <= center <= classes[b-1][0] + .02:
            return []
        for index in range(a, b):
            names, places = same_row(courses, index), same_row(locations, index)
            # Vision sometimes separates a parenthesized qualifier in the same cell.
            if not (len(names)==1 or len(names)==2 and names[1].startswith(('（','('))):
                continue
            if not (len(places)==1 or len(places)==2 and places[1].startswith(('（','('))):
                continue
            audience = classes[index][2]
            normalized = re.sub(r'^([BTW])(?:计[tf]?|it|if)(?:算|具|草|萛|鄭)机(?=\d)', r'\1计算机', audience, flags=re.I)
            evidence = [dict(message_id=message_id, quote=t) for t in (*names, audience, *places)]
            rows.append(dict(session, course=''.join(names), audience=normalized, location=''.join(places),
                             evidence=session['evidence'] + evidence))
    return rows


def read(lines, calendar, week, message_id):
    if not week or not lines:
        return None
    cells = [(s['x'] + s['w']/2, s['y'] + s['h']/2, s['text'].strip()) for s in lines]
    bottom = min((y for _, y, t in cells if '重修名单' in t), default=1)
    headers = {t: (x, y) for x, y, t in cells if t in HEADERS and y < bottom}
    if '考试时间' not in headers or '考场' not in headers:
        return None
    top = headers['考试时间'][1]
    heading = ' '.join(t for _, y, t in cells if y < top)
    number = WEEK_HEADING.search(heading)
    if not number or int(number[1]) != week:
        return None
    term_start = planner.week_one(calendar)
    monday = term_start + timedelta(weeks=week - 1)
    printed_year = re.search(r'(20\d{2})\s*年', heading)
    if printed_year and int(printed_year[1]) not in (term_start.year, monday.year, (monday + timedelta(days=6)).year):
        return None
    body = [(x,y,t) for x,y,t in cells if top < y < bottom]
    edge = (headers['考试时间'][0] + headers['考场'][0]) / 2
    groups = time_groups(sorted((y,t) for x,y,t in body if x < edge), monday, message_id)
    if groups:
        return [session for _, session in groups], exam_rows(headers, body, groups, message_id)
    return None


def extract(layouts, src):
    week = src.get('exam_week')
    messages = {m['id']: m for m in src['messages']}
    tables = []
    unverified = []
    for item in layouts:
        mid = item['message_id']
        if mid not in messages:
            continue
        stamp = messages[mid]['sent_at'] or ''
        lines = item['layout']['lines']
        parsed = read(lines, src['calendar'], week, mid)
        if parsed:
            tables.append((stamp, parsed))
        elif any(int(m[1]) == week for line in lines for m in WEEK_HEADING.finditer(line['text'])):
            unverified.append(stamp)
    if not tables:
        return None
    latest = max(stamp for stamp, _ in tables)
    # Don't reuse an older version after a correction or an unreadable replacement.
    if any(stamp >= latest for stamp in unverified):
        return None
    for m in messages.values():
        if (m['sent_at'] or '') > latest and re.search('更正|改为|延期|取消|作废|调整为', m['content'].split('【')[0]):
            return None
    sessions, exams = {}, {}
    for stamp, (slots, rows) in tables:
        if stamp != latest:
            continue
        for slot in slots:
            sessions[slot['date'], slot['start_time'], slot['end_time']] = slot
        for row in rows:
            exams[tuple(row[k] for k in ('date','start_time','end_time','course','audience','location'))] = row
    return dict(exams=list(exams.values()), sessions=list(sessions.values()), local=True,
                note='已在本机核对原图的时间列、教学周和考场行。')
