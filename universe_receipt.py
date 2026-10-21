"""Same-run transfer of a genuinely collected current-day official SSE list.

This is an operational receipt, not a backup or an alternative securities list.
Ubuntu still tries the official live source first. Only a network failure can
use the Windows job's validated receipt from this exact GitHub workflow run.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys

TZ=dt.timezone(dt.timedelta(hours=8))
PROTOCOL='sse-same-run-universe-v1'
SOURCE='https://www.sse.com.cn/assortment/stock/list/share/'
APIS={'https://query.sse.com.cn/sseQuery/commonQuery.do','https://query.sse.com.cn/commonQuery.do'}
MIN_ROWS,MAX_ROWS,MAX_BYTES=2000,10000,8*1024*1024
ROW_FIELDS={'symbol','name','listing_date','industry'}
FIELDS={'protocol','date','source','source_api','count','rows','collection_started_at','fetched_at',
        'collection_finished_at','collector_runner','collector_context','workflow_run_id','sha256'}


def _now():return dt.datetime.now(TZ)


def _moment(value):
    if not isinstance(value,dt.datetime) or value.tzinfo is None:raise ValueError('收据检查时间必须带时区')
    return value.astimezone(TZ)


def _instant(value):
    if not isinstance(value,str):raise ValueError('收据时间缺失')
    result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    return _moment(result)


def _canonical(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))


def _hash(value):return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _runner():
    actual='Windows' if sys.platform=='win32' else 'Linux' if sys.platform.startswith('linux') else sys.platform
    context='github_actions' if os.environ.get('GITHUB_ACTIONS','').lower()=='true' else 'local'
    reported=os.environ.get('RUNNER_OS')
    if context=='github_actions' and reported and reported!=actual:raise ValueError('执行器平台与真实Python平台不一致')
    return actual,context


def _run_id():
    value=os.environ.get('GITHUB_RUN_ID','')
    if not re.fullmatch(r'[0-9]{1,24}',value):raise ValueError('缺少当前GitHub workflow run标识')
    return value


def _listing_date(value,day):
    """Normalize SSE's actual YYYYMMDD date without changing its date value."""
    if value in (None,''):return value
    if not isinstance(value,str):raise ValueError('官方上市日期格式无效')
    if re.fullmatch(r'\d{8}',value):value=value[:4]+'-'+value[4:6]+'-'+value[6:]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('官方上市日期格式无效')
    parsed=dt.date.fromisoformat(value)
    if parsed>day:raise ValueError('官方上市日期来自未来')
    return parsed.isoformat()


def _validate_row(row,day):
    if not isinstance(row,dict) or set(row)!=ROW_FIELDS:raise ValueError('证券收据字段不在白名单')
    symbol,name=row['symbol'],row['name']
    if (not isinstance(symbol,str) or not re.fullmatch(r'sh(?:60[0135]|68[89])\d{3}',symbol)
            or not isinstance(name,str) or name!=name.strip() or not 1<=len(name)<=80 or name==symbol
            or not re.search('[\u3400-\u9fffA-Za-z]',name) or re.search(r'[\x00-\x1f\x7f]',name)):
        raise ValueError('证券代码或名称未核准')
    listing=row['listing_date']
    if listing not in (None,''):
        if not isinstance(listing,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',listing) or dt.date.fromisoformat(listing)>day:
            raise ValueError('上市日期无效或来自未来')
    if row['industry'] is not None and (not isinstance(row['industry'],str) or len(row['industry'])>200 or re.search(r'[\x00-\x1f\x7f]',row['industry'])):
        raise ValueError('行业字段无效')


def validate(record,now,md):
    moment=_moment(now)
    if not isinstance(record,dict) or set(record)!=FIELDS or record.get('protocol')!=PROTOCOL:
        raise ValueError('证券收据协议或字段不兼容')
    if len(_canonical(record).encode('utf-8'))>MAX_BYTES:raise ValueError('证券收据超过大小上限')
    if record.get('date')!=moment.date().isoformat():raise ValueError('拒绝跨日证券收据')
    if (record.get('source')!=SOURCE or record.get('source_api') not in APIS or
            getattr(md,'SSE_LIST_SOURCE',None)!=SOURCE or record['source_api'] not in getattr(md,'SSE_LIST_APIS',())):
        raise ValueError('证券收据不是允许的官方SSE来源')
    if (record.get('collector_context')!='github_actions' or record.get('collector_runner')!='Windows'
            or record.get('workflow_run_id')!=_run_id()
            or os.environ.get('GITHUB_ACTIONS','').lower()!='true'):
        raise ValueError('证券收据不是当前workflow的Windows云执行器原始收据')
    start,fetched,finished=[_instant(record.get(k)) for k in ('collection_started_at','fetched_at','collection_finished_at')]
    if not (start.date()==fetched.date()==finished.date()==moment.date() and start<=fetched<=finished<=moment):
        raise ValueError('证券收据原始采集时间无效或来自未来')
    rows=record.get('rows');count=record.get('count')
    if type(count) is not int or not MIN_ROWS<=count<=MAX_ROWS or not isinstance(rows,list) or len(rows)!=count:
        raise ValueError('证券收据未覆盖完整沪市范围')
    seen=set()
    for row in rows:
        _validate_row(row,moment.date())
        if row['symbol'] in seen:raise ValueError('证券收据包含重复代码')
        seen.add(row['symbol'])
    if [row['symbol'] for row in rows]!=sorted(seen):raise ValueError('证券收据代码顺序未规范化')
    digest=record.get('sha256')
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or digest!=_hash({k:v for k,v in record.items() if k!='sha256'}):
        raise ValueError('证券收据SHA256不匹配')
    output=[]
    for row in rows:
        item=dict(row,source=SOURCE,source_api=record['source_api'],fetched_at=record['fetched_at'],universe_total=count)
        item.update(md._flags(row['symbol'],row['name']))
        output.append(item)
    metadata={'mode':'shanghai','protocol':PROTOCOL,'date':record['date'],'count':count,
        'source':SOURCE,'source_api':record['source_api'],'cache_reused':True,
        'source_provenance':'sse_same_day_other_runner','fetched_at':record['fetched_at'],
        'original_fetched_at':record['fetched_at'],'checked_at':moment.isoformat(),
        'collection_started_at':record['collection_started_at'],'collection_finished_at':record['collection_finished_at'],
        'collector_runner':record['collector_runner'],'collector_context':record['collector_context'],
        'workflow_run_id':record['workflow_run_id'],'receipt_sha256':digest}
    return output,metadata


def load_receipt(path,now,md):
    path=Path(path);moment=_moment(now)
    if path.name!=moment.date().isoformat()+'.json' or path.is_symlink() or path.parent.is_symlink():
        raise ValueError('证券收据不是当日固定普通文件')
    if path.stat().st_size>MAX_BYTES:raise ValueError('证券收据超过大小上限')
    with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('证券收据超过大小上限')
    record=json.loads(raw.decode('utf-8-sig'),parse_constant=lambda _:(_ for _ in ()).throw(ValueError('非有限JSON数值')))
    return validate(record,moment,md)


def collect(output_dir,md=None):
    if md is None:
        from engine import market_data as md
    start=_now()
    if not md.is_trading_day(start.date().isoformat()):
        return {'status':'closed','date':start.date().isoformat(),'receipt_ready':False,'started_at':start.isoformat()}
    runner,context=_runner();run_id=_run_id()
    if runner!='Windows' or context!='github_actions':raise ValueError('名单转发收据仅由当前GitHub Windows执行器生成')
    received=md._sse_universe();finished=_now()
    if not isinstance(received,list) or not MIN_ROWS<=len(received)<=MAX_ROWS:raise ValueError('官方SSE名单数量不完整')
    rows,stamps,apis=[],[],set()
    for row in received:
        if not isinstance(row,dict) or row.get('source')!=SOURCE or row.get('universe_total')!=len(received):raise ValueError('官方SSE原始名单来源/总数不完整')
        stamp=_instant(row.get('fetched_at'))
        if not start<=stamp<=finished:raise ValueError('官方SSE名单没有本次真实采集时间')
        stamps.append(stamp);apis.add(row.get('source_api'))
        compact={k:row.get(k) for k in ROW_FIELDS}
        compact['listing_date']=_listing_date(compact['listing_date'],start.date())
        rows.append(compact)
    if len(apis)!=1 or next(iter(apis)) not in APIS:raise ValueError('官方SSE名单接口来源不一致')
    rows.sort(key=lambda row:row['symbol'])
    record={'protocol':PROTOCOL,'date':start.date().isoformat(),'source':SOURCE,'source_api':next(iter(apis)),
        'count':len(rows),'rows':rows,'collection_started_at':start.isoformat(),'fetched_at':max(stamps).isoformat(),
        'collection_finished_at':finished.isoformat(),'collector_runner':runner,'collector_context':context,'workflow_run_id':run_id}
    record['sha256']=_hash(record)
    validate(record,finished,md)
    directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True);path=directory/(record['date']+'.json')
    temporary=path.with_suffix('.json.tmp');temporary.write_text(_canonical(record),encoding='utf-8');temporary.replace(path)
    _,metadata=load_receipt(path,_now(),md)
    return dict(metadata,status='complete',receipt_ready=True)


@contextmanager
def use_receipt(core,directory):
    original=core.md.get_universe;used=None;before=core.getkv('universe_info',{})
    def get_universe(mode='shanghai'):
        nonlocal used
        try:
            result=original(mode)
            if mode=='shanghai':used=None
            return result
        except core.md.MarketNetworkError as exc:
            if mode!='shanghai' or not directory:raise
            moment=_moment(core.now())
            try:rows,metadata=load_receipt(Path(directory)/(moment.date().isoformat()+'.json'),moment,core.md)
            except (OSError,ValueError,TypeError) as invalid:
                raise core.md.MarketDataError('当前run当日官方名单转发收据不可用：'+type(invalid).__name__) from invalid
            statuses=list(dict.fromkeys(re.findall(r'\bHTTP\s+(\d{3})\b',str(exc))))
            metadata.update(last_live_attempt_at=moment.isoformat(),source_error='上交所官方名单网络失败'+('（HTTP '+ '/'.join(statuses[:4])+'）' if statuses else ''))
            used=metadata
            return rows
    core.md.get_universe=get_universe
    try:yield
    finally:
        core.md.get_universe=original
        if used is not None:
            current=core.getkv('universe_info',{})
            if (isinstance(current,dict) and current!=before and current.get('mode')=='shanghai'
                    and current.get('count')==used['count']):
                core.putkv('universe_info',dict(current,**used))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['collect'])
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args(argv)
    try:result=collect(args.output_dir);code=0
    except Exception as exc:
        result={'status':'error','receipt_ready':False,'error':type(exc).__name__,'message':'本次官方名单未完整核准，不生成可用转发收据'};code=1
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:stream.write('receipt_ready='+str(result.get('receipt_ready') is True).lower()+'\n')
    print(_canonical(result));return code


if __name__=='__main__':raise SystemExit(main())
