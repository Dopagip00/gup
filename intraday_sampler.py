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
import intraday_selection as selection

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

def inspect_rows(payload,symbol,period,observed,*,quarantine_pending=False):
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),dict):raise ValueError('Minute data object required')
    entry=payload['data'].get(symbol)
    if not isinstance(entry,dict):raise ValueError('Minute symbol object required')
    rows=entry.get(period)
    if payload.get('code')!=0 or not isinstance(rows,list) or not 1<=len(rows)<=1024:
        raise ValueError('Minute response structure invalid')
    previous=None;past=[];pending=[]
    for row in rows:
        if not isinstance(row,list) or len(row)<6 or not re.fullmatch(r'\d{12}',str(row[0])):
            raise ValueError('Minute row invalid')
        stamp=dt.datetime.strptime(row[0],'%Y%m%d%H%M').replace(tzinfo=TZ)
        if previous and stamp<=previous:raise ValueError('Duplicate or unordered timestamps')
        if stamp>observed:
            # Retain a possible open interval separately, never as a closed bar.
            # This is a timestamp bound, not verification of provider semantics.
            bound={'m1':1,'m5':5}.get(period,0)
            if not quarantine_pending or stamp>observed+dt.timedelta(minutes=bound):
                raise ValueError('Future timestamp')
            pending.append(row)
        else:past.append(row)
        previous=stamp
        # Only structural validation: do not infer OHLC field order, units or
        # interval boundaries from an undocumented response.
        for value in row[1:6]:
            try:n=float(value)
            except (TypeError,ValueError):raise ValueError('Invalid numeric value')
            if not 0<=n<float('inf'):raise ValueError('Nonfinite or negative value')
    if len(pending)>1:raise ValueError('Multiple future intervals')
    if not past:raise ValueError('No past observations')
    return {'rows':past,'first_timestamp':past[0][0],'last_timestamp':past[-1][0],
            'count':len(past),'pending_rows':pending,
            'pending_reason':'timestamp_after_fetch' if pending else None,
            'semantics_verified':False,'usable_for_signals':False}

def previous_session(day):
    """Bound calendar access, but do not confuse long holidays with expiry."""
    for offset in range(1,41):
        candidate=day-dt.timedelta(days=offset)
        if md.is_trading_day(candidate):return candidate
    raise ValueError('Previous official session unavailable')

def read_archives(output,day,observed):
    records=[];errors=[]
    # Only today and the preceding official session are needed for D1/D2.
    for session in (previous_session(day),day):
        for path in sorted((output/session.isoformat()).glob('*.json')):
            try:
                if path.stat().st_size>40_000_000:raise ValueError('Oversized archive')
                old=json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(old,dict) or old.get('protocol')!=PROTOCOL or old.get('date')!=session.isoformat():
                    raise ValueError('Archive envelope invalid')
                start=instant(old.get('started_at'));finish=instant(old.get('finished_at'))
                if not start<=finish<=observed or start.date()!=session:raise ValueError('Archive time invalid')
                entry=old.get('cohort')
                if entry is None:continue
                if not isinstance(entry,dict):raise ValueError('Archive cohort invalid')
                seen=instant(entry.get('observed_at'))
                if not start<=seen<=finish:raise ValueError('Cohort time outside receipt')
                checked=cohort(entry.get('record'),seen)
                if checked['date']!=session.isoformat():raise ValueError('Cohort day mismatch')
                checked['evidence_path']=path.relative_to(output).as_posix()
                records.append(checked)
            except (OSError,ValueError,KeyError,TypeError):
                errors.append('archive_record_invalid:'+path.relative_to(output).as_posix())
    return records,errors

def cohort_identity(entry):
    record=entry['record']
    # Risk alerts may be appended, but the published ranks and inputs are fixed.
    frozen={key:record.get(key) for key in ('protocol','date','status','published_at','items')}
    return hashlib.sha256(json.dumps(frozen,sort_keys=True,ensure_ascii=False,
                                    allow_nan=False,separators=(',',':')).encode()).hexdigest()

def frozen_cohort(records,current,observed):
    candidates=[r for r in records if r['date']==observed.date().isoformat()]
    if current is not None:candidates.append(current)
    early=sorted((r for r in candidates if r['preopen_visibility_verified']),key=lambda r:instant(r['observed_at']))
    if not early:return {'status':'missing','cohort':None,'conflict':False}
    first=early[0];identity=cohort_identity(first)
    conflict=any(cohort_identity(r)!=identity for r in candidates)
    return {'status':'conflict' if conflict else 'frozen','cohort':first,
            'identity_sha256':identity,'conflict':conflict}

def research_state(report,observed):
    """Operational state only; never a substitute for signal validation."""
    if report['status']=='closed':return 'closed'
    if report['errors']:return 'data_error'
    frozen=report['frozen_cohort']
    if frozen['conflict']:return 'cohort_conflict'
    entry=frozen['cohort']
    if entry is None:
        return 'awaiting_preopen_evidence' if observed.time()<dt.time(9,30) else 'missing_preopen_evidence'
    if entry['record']['status']=='cash':return 'cash'
    if observed.time()<dt.time(9,45):return 'awaiting_confirmation_window'
    if observed.time()>dt.time(10):return 'confirmation_window_closed'
    return 'minute_semantics_unverified'

def select_research(report,output,now):
    states=[]
    for path in sorted((output/report['date']).glob('*.json')):
        saved=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved,dict):raise ValueError('Invalid selection journal')
        if saved.get('selection_error'):raise ValueError('Unresolved selection journal error')
        if 'selection' in saved:
            state=saved['selection']
            if instant(state['checked_at'])>instant(saved['finished_at']):
                raise ValueError('Selection after receipt')
            states.append(state)
    previous=selection.restore(states,report['date'],now)
    frozen=report['frozen_cohort'];entry=frozen.get('cohort')
    record=(report.get('cohort') or {}).get('record') or {}
    vetoes={s:'watchlist_risk_withdrawal' for s in record.get('withdrawn_symbols',[])}
    for alert in record.get('alerts',[]):
        if alert.get('withdrawn') is True:vetoes[alert['symbol']]=alert.get('reason') or 'watchlist_risk_withdrawal'
    # No source row is promoted to a verified confirmation. The independent
    # minute/reference verifier must supply assessments in a subsequent phase.
    return selection.advance(previous,now=now,cohort_id=frozen.get('identity_sha256'),
        symbols=entry['symbols'] if entry else [],assessments={},vetoes=vetoes,
        ready=report['status']=='raw_collected' and frozen['status']=='frozen',
        reason=report['research_state'])

def collect(output=Path('research/intraday-evidence')):
    started=dt.datetime.now(TZ);day=started.date().isoformat()
    deadline=time.monotonic()+480
    report={'protocol':PROTOCOL,'date':day,'started_at':started.isoformat(),
            'actionable':False,'eligible':False,'valid_evaluation_samples':0,
            'status':'collecting','cohort':None,'sources':[],'errors':[],
            'frozen_cohort':{'status':'missing','cohort':None,'conflict':False},
            'limitations':['Raw source semantics and interval boundaries await independent validation; no buy signals.']}
    try:
        report['calendar']=md.get_calendar(started.year)
        if not md.is_trading_day(started.date()):
            report['status']='closed'
        else:
            symbols={'sh000001'}
            archives,errors=read_archives(output,started.date(),started)
            report['errors'].extend(errors)
            try:
                data,digest=fetch(PUBLIC+f'watchlists/{day}.json')
                observed=dt.datetime.now(TZ)
                report['cohort']=cohort(data,observed)
                report['cohort']['response_sha256']=digest
                symbols.update(report['cohort']['symbols'])
            except (OSError,ValueError,KeyError,TypeError):report['errors'].append('same_day_cohort_unavailable')
            report['frozen_cohort']=frozen_cohort(archives,report['cohort'],dt.datetime.now(TZ))
            # Continue original cohorts across weekends/holidays, regardless of
            # current cash state or later removal. They are not new candidates.
            for old in archives:symbols.update(old['symbols'])
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
                        receipt.update(inspection=inspect_rows(payload,symbol,period,observed,quarantine_pending=True),status='collected')
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        receipt.setdefault('fetched_at',dt.datetime.now(TZ).isoformat())
                        receipt.update(status='error',error='source_fetch_or_validation_failed')
                        if 'raw_payload' in receipt and isinstance(exc,ValueError):receipt['validation_error']=str(exc)
                        report['errors'].append(symbol+':'+period)
                    report['sources'].append(receipt)
                    time.sleep(.3)
            report['status']='partial' if report['errors'] else 'raw_collected'
    except (md.MarketDataError,ValueError):
        report['status']='blocked';report['errors'].append('calendar_or_collection_validation_failed')
    finished=dt.datetime.now(TZ)
    report['finished_at']=finished.isoformat()
    report['within_confirmation_window']=dt.time(9,45)<=finished.time()<=dt.time(10)
    report['research_state']=research_state(report,finished)
    try:
        report['selection']=select_research(report,output,finished)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        report['selection_error']='selection_journal_or_input_invalid'
        report['errors'].append(report['selection_error'])
        report['status']='partial';report['research_state']='data_error'
    folder=output/day;folder.mkdir(parents=True,exist_ok=True)
    run=re.sub('[^0-9]','',os.environ.get('GITHUB_RUN_ID','local')) or 'local'
    name=started.strftime('%H%M%S%f')+'-'+run+'.json'
    with (folder/name).open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    print(json.dumps({'status':report['status'],'research_state':report['research_state'],'date':day,'sources':len(report['sources']),'errors':report['errors'],'path':str(folder/name)}))
    return report

if __name__=='__main__':
    result=collect()
    sys.exit(1 if result['status'] in ('partial','blocked') else 0)
