"""Prospective cloud evidence collection; never grants trading eligibility."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parent/'engine'))
import market_data as md

TZ=dt.timezone(dt.timedelta(hours=8))
PUBLIC='https://dopagip00.github.io/gup/data/'
SOURCE='https://ifzq.gtimg.cn/appstock/app/kline/mkline'
PROTOCOL='intraday-raw-evidence-v1'

def instant(value):
    if not isinstance(value,str):raise ValueError('Timestamp required')
    result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone required')
    return result.astimezone(TZ)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'HuyanResearch/1.0','Cache-Control':'no-cache'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=15) as r:raw=r.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('Response too large')
            def reject_constant(value):raise ValueError('Nonfinite JSON constant')
            return json.loads(raw,parse_constant=reject_constant),hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError):
            if attempt==2:raise
            time.sleep(1+attempt)

def cohort(record,observed):
    """Seeing an old timestamp after the open does not prove pre-open visibility."""
    day=observed.date().isoformat()
    if not isinstance(record,dict):raise ValueError('Observation object required')
    if record.get('protocol')!='daily-observation-v1' or record.get('date')!=day:
        raise ValueError('No same-day observation record')
    if record.get('status') not in ('watch_only','cash'):raise ValueError('Observation not released')
    published=instant(record['published_at'])
    deadline=observed.replace(hour=9,minute=30,second=0,microsecond=0)
    if not published.date()==observed.date() or not published<=observed:raise ValueError('Invalid publication time')
    items=record.get('items',[])
    if not isinstance(items,list) or len(items)>5:raise ValueError('Invalid cohort')
    if any(not isinstance(i,dict) for i in items):raise ValueError('Invalid cohort item')
    symbols=[i.get('symbol') for i in items]
    if any(not isinstance(s,str) or not re.fullmatch(r'sh\d{6}',s) for s in symbols) or len(set(symbols))!=len(symbols):
        raise ValueError('Invalid symbols')
    if record['status']=='cash' and symbols:raise ValueError('Cash record has candidates')
    return {'date':day,'symbols':symbols,'published_at':published.isoformat(),
            'observed_at':observed.isoformat(),'preopen_visibility_verified':published<deadline and observed<deadline,
            'record':record}

def inspect_rows(payload,symbol,period,observed):
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),dict):raise ValueError('Minute data object required')
    entry=payload['data'].get(symbol)
    if not isinstance(entry,dict):raise ValueError('Minute symbol object required')
    rows=entry.get(period)
    if payload.get('code')!=0 or not isinstance(rows,list) or not 1<=len(rows)<=1024:
        raise ValueError('Minute response structure invalid')
    previous=None
    for row in rows:
        if not isinstance(row,list) or len(row)<6 or not re.fullmatch(r'\d{12}',str(row[0])):
            raise ValueError('Minute row invalid')
        stamp=dt.datetime.strptime(row[0],'%Y%m%d%H%M').replace(tzinfo=TZ)
        if previous and stamp<=previous:raise ValueError('Duplicate or unordered timestamps')
        if stamp>observed:raise ValueError('Future timestamp')
        previous=stamp
        # Only structural validation: do not infer OHLC field order, units or
        # interval boundaries from an undocumented response.
        for value in row[1:6]:
            try:n=float(value)
            except (TypeError,ValueError):raise ValueError('Invalid numeric value')
            if not 0<=n<float('inf'):raise ValueError('Nonfinite or negative value')
    return {'rows':rows,'first_timestamp':rows[0][0],'last_timestamp':rows[-1][0],
            'count':len(rows),'semantics_verified':False,'usable_for_signals':False}

def collect(output=Path('research/intraday-evidence')):
    started=dt.datetime.now(TZ);day=started.date().isoformat()
    deadline=time.monotonic()+480
    report={'protocol':PROTOCOL,'date':day,'started_at':started.isoformat(),
            'actionable':False,'eligible':False,'valid_evaluation_samples':0,
            'status':'collecting','cohort':None,'sources':[],'errors':[],
            'limitations':['Raw source semantics and interval boundaries await independent validation; no buy signals.']}
    try:
        report['calendar']=md.get_calendar(started.year)
        if not md.is_trading_day(started.date()):
            report['status']='closed'
        else:
            symbols={'sh000001'}
            try:
                data,digest=fetch(PUBLIC+f'watchlists/{day}.json')
                observed=dt.datetime.now(TZ)
                report['cohort']=cohort(data,observed)
                report['cohort']['response_sha256']=digest
                symbols.update(report['cohort']['symbols'])
            except (OSError,ValueError,KeyError,TypeError):report['errors'].append('same_day_cohort_unavailable')
            # Follow preceding cohorts for the next-session outcome, without
            # treating those symbols as new candidates today.
            for path in sorted(output.glob('*/*.json'))[-120:]:
                try:
                    old=json.loads(path.read_text(encoding='utf-8'))
                    age=(started.date()-dt.date.fromisoformat(old['date'])).days
                    if 0<=age<=7 and old.get('protocol')==PROTOCOL:
                        symbols.update(s for s in (old.get('cohort') or {}).get('symbols',[]) if re.fullmatch(r'sh\d{6}',s))
                except (ValueError,KeyError,TypeError):report['errors'].append('archive_record_invalid')
            if len(symbols)>36:raise ValueError('Bounded collection universe exceeded')
            for symbol in sorted(symbols):
                if time.monotonic()>=deadline:
                    report['errors'].append('collection_time_budget_exceeded');break
                for period in ('m1','m5'):
                    if time.monotonic()>=deadline:
                        report['errors'].append('collection_time_budget_exceeded');break
                    url=SOURCE+f'?param={symbol},{period},,1023'
                    receipt={'symbol':symbol,'period':period,'url':url,'requested_at':dt.datetime.now(TZ).isoformat()}
                    try:
                        payload,digest=fetch(url);observed=dt.datetime.now(TZ)
                        receipt.update(fetched_at=observed.isoformat(),sha256=digest,raw_payload=payload)
                        receipt.update(inspection=inspect_rows(payload,symbol,period,observed),status='collected')
                    except (OSError,ValueError,KeyError,TypeError):
                        receipt.update(status='error',fetched_at=dt.datetime.now(TZ).isoformat(),error='source_fetch_or_validation_failed')
                        report['errors'].append(symbol+':'+period)
                    report['sources'].append(receipt)
                    time.sleep(.3)
            report['status']='partial' if report['errors'] else 'raw_collected'
    except (md.MarketDataError,ValueError):
        report['status']='blocked';report['errors'].append('calendar_or_collection_validation_failed')
    report['finished_at']=dt.datetime.now(TZ).isoformat()
    report['within_confirmation_window']=dt.time(9,45)<=dt.datetime.now(TZ).time().replace(tzinfo=None)<=dt.time(10)
    folder=output/day;folder.mkdir(parents=True,exist_ok=True)
    run=re.sub('[^0-9]','',os.environ.get('GITHUB_RUN_ID','local')) or 'local'
    name=started.strftime('%H%M%S%f')+'-'+run+'.json'
    with (folder/name).open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    print(json.dumps({'status':report['status'],'date':day,'sources':len(report['sources']),'errors':report['errors'],'path':str(folder/name)}))
    return report

if __name__=='__main__':
    result=collect()
    sys.exit(1 if result['status'] in ('partial','blocked') else 0)
