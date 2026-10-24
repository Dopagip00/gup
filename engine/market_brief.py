"""Bounded public morning headline snapshots; no network calls or stock signals."""
from __future__ import annotations
import datetime as dt
import re
from urllib.parse import urlsplit

PROTOCOL='market-brief-v1'
KEY_PREFIX='market_brief:'
TZ=dt.timezone(dt.timedelta(hours=8))
HOSTS={'www.sse.com.cn','static.sse.com.cn','finance.cctv.com','www.pbc.gov.cn','www.csrc.gov.cn','www.cls.cn','wallstreetcn.com'}
STATUSES={'complete','partial','unavailable','historical_unavailable','closed'}


def _day(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('消息日期必须为YYYY-MM-DD')
    return dt.date.fromisoformat(value)


def _stamp(value):
    if not isinstance(value,str):raise ValueError('消息时间缺失')
    parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('消息时间必须带时区')
    return parsed.astimezone(TZ)


def _text(value,limit):
    if not isinstance(value,str):raise ValueError('消息文本格式错误')
    return re.sub(r'[\x00-\x1f\x7f]',' ',value).strip()[:limit]


def _url(value):
    parts=urlsplit(value)
    if (parts.scheme!='https' or parts.hostname not in HOSTS or parts.username or parts.password
            or parts.port not in (None,443) or parts.query or parts.fragment):raise ValueError('消息链接不是允许的来源原文')
    return value


def empty(date,status='unavailable',warning='该日尚无真实采集的早间消息，未使用其他日期回填。'):
    return {'protocol':PROTOCOL,'date':date,'generated_at':None,'status':status,'items':[],
            'coverage':[],'warnings':[warning],'scope':'官方与财经媒体的有界标题取样；未阅读全文或覆盖全部消息。'}


def public(value,*,date,now):
    """Validate and project only public fields; reject future or relabelled data."""
    day=_day(date);moment=_stamp(now.isoformat())
    if (not isinstance(value,dict) or value.get('protocol')!=PROTOCOL or value.get('date')!=date
            or value.get('status') not in STATUSES):raise ValueError('早间消息协议或所属日期无效')
    generated=_stamp(value['generated_at']) if value.get('generated_at') else None
    if generated and (generated.date()!=day or generated>moment):raise ValueError('消息生成时间无效或来自未来')
    rows=value.get('items');sources=value.get('coverage')
    if not isinstance(rows,list) or len(rows)>24 or not isinstance(sources,list) or len(sources)>8:raise ValueError('消息条目超过限制')
    if value['status']=='complete' and not rows:raise ValueError('没有标题不能声称完整采集')
    result=empty(date,value['status']);result['generated_at']=generated.isoformat() if generated else None
    result['late']=value.get('late') is True
    result['warnings']=[_text(item,300) for item in value.get('warnings',[])[:12]]
    for row in rows:
        if not isinstance(row,dict) or not generated:raise ValueError('消息条目缺少真实收集时间')
        collected=_stamp(row.get('collected_at'));precision=row.get('published_precision')
        if not collected<=generated or collected.date()!=day:raise ValueError('消息收集时间无效')
        if precision=='day':
            published=_day(row.get('published_at'))
            if published>collected.date():raise ValueError('消息发布时间来自未来')
            published=published.isoformat()
        elif precision in ('minute','second'):
            published=_stamp(row.get('published_at'))
            if published>collected:raise ValueError('消息发布时间晚于收集时间')
            published=published.isoformat()
        else:raise ValueError('消息发布时间精度无效')
        if row.get('importance') not in ('high','normal') or row.get('impact') not in ('policy','macro','market','risk','general'):
            raise ValueError('消息分类无效')
        result['items'].append({'id':_text(row.get('id'),64),'title':_text(row.get('title'),250),
            'url':_url(row.get('url')),'source':_text(row.get('source'),60),'published_at':published,
            'published_precision':precision,'collected_at':collected.isoformat(),'importance':row['importance'],
            'importance_reason':_text(row.get('importance_reason'),220),'topics':[_text(item,30) for item in row.get('topics',[])[:4]],
            'impact':row['impact'],'scope':_text(row.get('scope'),220)})
    for source in sources:
        if not isinstance(source,dict) or source.get('status') not in ('complete','partial','error'):raise ValueError('消息来源覆盖格式无效')
        count=source.get('count',0)
        if type(count) is not int or not 0<=count<=10000:raise ValueError('消息来源数量无效')
        collected=_stamp(source['collected_at']) if source.get('collected_at') else None
        if collected and (not generated or collected>generated or collected.date()!=day):raise ValueError('来源采集时间无效')
        result['coverage'].append({'source':_text(source.get('source'),60),'url':_url(source.get('url')),
            'status':source['status'],'collected_at':collected.isoformat() if collected else None,'count':count,
            'error':_text(source.get('error',''),220),'scope':_text(source.get('scope',''),250)})
    return result


def read(date=None,*,core=None,now=None):
    if core is None:import core
    moment=_stamp((now or core.now()).isoformat());date=date or moment.date().isoformat();day=_day(date)
    value=core.getkv(KEY_PREFIX+date)
    if value is None:
        news=core.getkv('morning_news',{})
        value=news.get('market_brief') if isinstance(news,dict) and news.get('date')==date else None
    if value is None:
        if day<moment.date():return empty(date,'historical_unavailable')
        try:
            if not core.md.is_trading_day(date):return empty(date,'closed','上交所官方日历休市，没有该日早间采集。')
        except Exception:return empty(date,warning='官方交易日历无法核验，早间消息状态未知。')
        return empty(date)
    try:return public(value,date=date,now=moment)
    except (TypeError,ValueError,KeyError):return empty(date,warning='已存消息未通过所属日期、来源或真实时间核验，暂停展示。')
