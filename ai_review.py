"""Read a bounded public cloud-AI risk review; AI can only veto candidates."""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qsl

PROTOCOL = 'cloud-ai-risk-review-v1'
TZ = dt.timezone(dt.timedelta(hours=8))
MAX_BYTES = 256 * 1024
MAX_SYMBOLS = 2400
FIELDS = {'protocol','date','signal_date','status','started_at','completed_at','reviewed_symbols',
          'risk_vetoes','sources','summary','scope','run_url','run_url_scope'}
SOURCE_FIELDS = {'id','url','published_date','observed_at','scope'}
VETO_FIELDS = {'symbol','reason','source_ids'}
SSE_HOSTS = {'www.sse.com.cn','static.sse.com.cn','query.sse.com.cn'}
HOSTS = SSE_HOSTS | {'finance.cctv.com'}
SYMBOL = re.compile(r'sh(?:60\d{4}|68\d{4})')


def _text(value, limit):
    return isinstance(value,str) and bool(value.strip()) and len(value) <= limit


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('日期格式无效')
    return dt.date.fromisoformat(value)


def _instant(value):
    if not isinstance(value,str):raise ValueError('时间缺失')
    stamp=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('时间缺少时区')
    return stamp.astimezone(TZ)


def _url(value, hosts):
    if not _text(value,2048):raise ValueError('来源URL无效')
    u=urlsplit(value)
    if (u.scheme!='https' or u.hostname not in hosts or u.username or u.password or
            u.port not in (None,443) or u.fragment or not u.path or u.path=='/'):
        raise ValueError('来源URL不是允许的公开HTTPS页面')
    if any(re.search(r'token|password|secret|cookie|authorization|api.?key',key,re.I) for key,_ in parse_qsl(u.query)):
        raise ValueError('来源URL含凭据参数')
    return u


def validate_review(record, *, now, signal_date):
    moment=_instant(now.isoformat())
    receipt={'protocol':PROTOCOL,'date':moment.date().isoformat(),'signal_date':signal_date,
             'checked_at':moment.isoformat(),'status':'invalid','valid':False,
             'morning_qualified':False,'errors':[],'review':None}
    try:
        if not isinstance(record,dict) or set(record)!=FIELDS:raise ValueError('AI复核字段缺失或含非白名单字段')
        if len(json.dumps(record,ensure_ascii=False,allow_nan=False).encode('utf-8'))>MAX_BYTES:raise ValueError('AI复核超过256KiB')
        if record['protocol']!=PROTOCOL:raise ValueError('AI复核协议不兼容')
        if _date(record['date'])!=moment.date() or record['signal_date']!=signal_date or _date(record['signal_date'])>=moment.date():
            raise ValueError('AI复核日期或原始信号日期不匹配')
        if record['status'] not in ('complete','partial','late','unavailable'):raise ValueError('AI复核状态无效')
        start,end=_instant(record['started_at']),_instant(record['completed_at'])
        if not (start.date()==end.date()==moment.date() and start<=end<=moment):raise ValueError('AI复核起止时间无效或晚于实际检查')
        morning=dt.time(8)<=start.time().replace(tzinfo=None)<=end.time().replace(tzinfo=None)<dt.time(9,30)
        if record['status']=='complete' and not morning:raise ValueError('complete复核不在真实08:00至09:29窗口')
        if not _text(record['summary'],4000) or not _text(record['scope'],2000):raise ValueError('AI复核摘要或范围缺失/超限')
        _url(record['run_url'],{'chatgpt.com'})
        if record['run_url_scope'] not in ('task_run','task_chat'):raise ValueError('AI任务链接范围未声明')
        reviewed=record['reviewed_symbols']
        if (not isinstance(reviewed,list) or len(reviewed)>MAX_SYMBOLS or
                any(not isinstance(s,str) or not SYMBOL.fullmatch(s) for s in reviewed) or len(set(reviewed))!=len(reviewed)):
            raise ValueError('已复核证券列表无效或重复')
        sources=record['sources'];ids=set()
        if not isinstance(sources,list) or len(sources)>MAX_SYMBOLS or (reviewed and not sources):raise ValueError('AI复核缺少公开来源或超过上限')
        for source in sources:
            if not isinstance(source,dict) or set(source)!=SOURCE_FIELDS:raise ValueError('来源字段不在白名单')
            sid=source['id']
            if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',sid) or sid in ids:raise ValueError('来源编号无效或重复')
            u=_url(source['url'],HOSTS);observed=_instant(source['observed_at'])
            expected_scope='official_directory_title_only' if u.hostname in SSE_HOSTS else 'publisher_article_only'
            if source['scope']!=expected_scope:raise ValueError('来源核验范围无效；不得声称核验PDF正文')
            if not (start<=observed<=end and _date(source['published_date'])<=observed.date()):raise ValueError('来源披露/观察时间无效或来自未来')
            ids.add(sid)
        vetoes=record['risk_vetoes'];seen=set()
        if not isinstance(vetoes,list) or len(vetoes)>MAX_SYMBOLS:raise ValueError('AI风险否决超过上限')
        for veto in vetoes:
            if not isinstance(veto,dict) or set(veto)!=VETO_FIELDS:raise ValueError('风险否决字段不在白名单')
            symbol=veto['symbol'];refs=veto['source_ids']
            if symbol not in reviewed or symbol in seen or not _text(veto['reason'],500):raise ValueError('风险否决不是已复核证券或原因无效')
            if (not isinstance(refs,list) or not refs or len(refs)>MAX_SYMBOLS or
                    any(not isinstance(s,str) or s not in ids for s in refs) or len(set(refs))!=len(refs)):
                raise ValueError('风险否决没有可核验来源引用')
            seen.add(symbol)
        receipt.update(status=record['status'],valid=True,morning_qualified=record['status']=='complete' and morning,
                       review=json.loads(json.dumps(record,ensure_ascii=False,allow_nan=False)))
        if not receipt['morning_qualified']:receipt['errors']=['当前记录不是盘前完整AI风险复核']
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        receipt['errors']=[str(exc)[:300]]
    return receipt


def load_review(repository_root, *, now, signal_date):
    root=Path(repository_root).resolve();date=_instant(now.isoformat()).date().isoformat()
    relative=Path('research')/'ai-reviews'/(date+'.json');path=root/relative
    missing={'protocol':PROTOCOL,'date':date,'signal_date':signal_date,'checked_at':now.isoformat(),
             'status':'missing','valid':False,'morning_qualified':False,'errors':['当日云端AI风险复核记录尚未读取'],'review':None,
             'record_path':relative.as_posix()}
    try:
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path,path.parent,path.parent.parent)):
            raise ValueError('AI复核路径不是仓库内的普通文件')
        if not path.is_file():return missing
        if path.stat().st_size>MAX_BYTES:raise ValueError('AI复核文件超过256KiB')
        with path.open('rb') as source:raw=source.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('AI复核文件超过256KiB')
        record=json.loads(raw.decode('utf-8-sig'),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('JSON非有限数值')))
        return dict(validate_review(record,now=now,signal_date=signal_date),record_path=relative.as_posix())
    except (OSError,UnicodeError,ValueError,TypeError) as exc:
        return dict(missing,status='invalid',errors=[str(exc)[:300]])


def public_status(receipt):
    if not isinstance(receipt,dict):receipt={}
    review=receipt.get('review') if isinstance(receipt.get('review'),dict) else {}
    reviewed=review.get('reviewed_symbols');vetoes=review.get('risk_vetoes')
    return {key:receipt.get(key) for key in ('protocol','date','signal_date','status','valid','morning_qualified','checked_at','errors')} | {
        'summary':review.get('summary'),'scope':review.get('scope'),'run_url':review.get('run_url'),
        'run_url_scope':review.get('run_url_scope'),'reviewed_count':len(reviewed) if isinstance(reviewed,list) else 0,
        'veto_count':len(vetoes) if isinstance(vetoes,list) else 0}
