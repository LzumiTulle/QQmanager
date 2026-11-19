"""Receipt-day coverage without the ordinary history list's 500-row limit."""
from datetime import datetime,time,timedelta
from . import planner,matters

def snapshot(s,day=None,span='day',offset=0):
    offset=max(0,min(int(offset),1000000))
    matters.sync(s)
    d=planner.date.fromisoformat(day) if day else planner.today()
    start=d;end=d+timedelta(days=1)
    if span=='week':start=d-timedelta(days=d.weekday());end=start+timedelta(days=7)
    elif span=='month':
        start=d.replace(day=1);end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
    lo=datetime.combine(start,time.min,planner.TZ).timestamp();hi=datetime.combine(end,time.min,planner.TZ).timestamp()
    args=[lo,hi];where='m.created>=? AND m.created<?'
    account=s.setting('ws_account');groups=s.setting('bridge_groups') or []
    if account:
        if not groups:where+=' AND 0'
        else:
            where+=' AND j.account=? AND j.group_id IN ('+','.join('?' for _ in groups)+')';args+=[account,*groups]
    rows=s.query('''SELECT m.id,m.created,m.group_name,j.state analysis_state,
        EXISTS(SELECT 1 FROM insights i WHERE i.message_id=m.id AND i.quality_reason='' AND i.status!='dismissed') important
        FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE '''+where,args)
    counts={'received':len(rows),'important':sum(r['important'] for r in rows),
            'pending':sum(r['analysis_state'] in ('pending','running') for r in rows),
            'failed':sum(r['analysis_state']=='failed' for r in rows)}
    daily={};by_group={}
    for r in rows:
        date=datetime.fromtimestamp(r['created'],planner.TZ).date().isoformat()
        values=daily.setdefault(date,{'received':0,'important':0});values['received']+=1;values['important']+=r['important']
        values=by_group.setdefault(r['group_name'],{'received':0,'important':0});values['received']+=1;values['important']+=r['important']
    ids={r['id'] for r in rows if r['important']};mapping=s.query('SELECT DISTINCT i.message_id,mm.matter_id FROM insights i JOIN matter_members mm ON mm.insight_id=i.id')
    active={r['matter_id'] for r in mapping if r['message_id'] in ids}
    important=[m for m in matters.list_matters(s) if m['id'] in active and m['status']!='dismissed']
    latest=s.query('''SELECT m.*,j.state analysis_state FROM messages m LEFT JOIN analysis_jobs j ON j.message_id=m.id WHERE '''+where+' ORDER BY m.created DESC,m.id DESC LIMIT 20 OFFSET ?',[*args,offset])
    return {'start':start.isoformat(),'end':(end-timedelta(days=1)).isoformat(),'counts':counts,'daily':daily,
            'groups':[dict(group_name=k,**v) for k,v in sorted(by_group.items())],'important':important,'messages':latest}
