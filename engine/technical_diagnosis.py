"""Dated, reproducible technical diagnosis; no orders or fitted probabilities.

MACD(12,26,9) EMA seed=first close; histogram=2*(DIF-DEA).
KDJ(9,3,3) recursive seed=50; RSI(14) Wilder; BOLL(20,2) population SD.
Only completed calendar weeks are used. Float-cap emphasis is descriptive,
not a validated return weight or a reason to overwrite a published ranking.
"""
from __future__ import annotations
import datetime as dt
import math
from statistics import mean, pstdev
from urllib.parse import urlsplit
import strategy

PROTOCOL='technical-diagnosis-v1'
TZ=dt.timezone(dt.timedelta(hours=8))


def finite(x, positive=False):
    return type(x) in (int,float) and math.isfinite(x) and (not positive or x>0)


def _rows(bars,target):
    if not isinstance(bars,list):raise ValueError('日线缺失')
    selected=[];seen=set()
    for row in bars:
        if not isinstance(row,dict):raise ValueError('日线格式错误')
        date=row.get('date');day=dt.date.fromisoformat(date)
        if day.isoformat()!=date:raise ValueError('日期格式错误')
        if date>target:continue
        if date in seen:raise ValueError('日线日期重复')
        seen.add(date)
        if (any(not finite(row.get(k),True) for k in ('open','high','low','close'))
                or not finite(row.get('volume')) or row['volume']<0
                or not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']):
            raise ValueError('日线价量异常')
        selected.append(row)
    if len(selected)<120 or [r['date'] for r in selected]!=sorted(seen) or selected[-1]['date']!=target:
        raise ValueError('需要截至研究日的至少120根有序日线')
    return selected[-260:]


def _ema(values,period):
    result=[values[0]];alpha=2/(period+1)
    for value in values[1:]:result.append(alpha*value+(1-alpha)*result[-1])
    return result


def indicators(rows):
    close=[r['close'] for r in rows]
    dif=[a-b for a,b in zip(_ema(close,12),_ema(close,26))];dea=_ema(dif,9)
    cross='golden' if dif[-2]<=dea[-2] and dif[-1]>dea[-1] else 'dead' if dif[-2]>=dea[-2] and dif[-1]<dea[-1] else 'none'
    k=d=50.
    for i in range(8,len(rows)):
        window=rows[i-8:i+1];high=max(r['high'] for r in window);low=min(r['low'] for r in window)
        rsv=100*(rows[i]['close']-low)/(high-low) if high>low else 50.
        k=(2*k+rsv)/3;d=(2*d+k)/3
    mid=mean(close[-20:]);sd=pstdev(close[-20:]);width=4*sd/mid*100
    previous=4*pstdev(close[-25:-5])/mean(close[-25:-5])*100
    return {'macd':{'dif':dif[-1],'dea':dea[-1],'histogram':2*(dif[-1]-dea[-1]),'cross':cross,
                    'above_zero':dif[-1]>0 and dea[-1]>0},
            'kdj':{'k':k,'d':d,'j':3*k-2*d},'rsi14':strategy._rsi(close),
            'boll':{'mid':mid,'upper':mid+2*sd,'lower':mid-2*sd,'width_pct':width,
                    'width_5_sessions_ago_pct':previous,'expanding':width>previous}}


def weekly_trend(rows,target,calendar):
    """A holiday-shortened week closes on its final official session, not Friday."""
    missing={'status':'unavailable','regime':'unknown','completed_weeks':0}
    if not isinstance(calendar,dict):return missing
    groups={};by_date={r['date']:r for r in rows};target_day=dt.date.fromisoformat(target)
    for row in rows:
        day=dt.date.fromisoformat(row['date']);monday=day-dt.timedelta(days=day.weekday())
        if monday<=target_day:groups[monday]=None
    weeks=[]
    try:
        for monday in sorted(groups,reverse=True):
            expected=[]
            for offset in range(5):
                day=monday+dt.timedelta(days=offset);info=calendar.get(str(day.year))
                if not isinstance(info,dict) or info.get('verified') is not True or not isinstance(info.get('holidays'),list):return missing
                if day.isoformat() not in info['holidays']:expected.append(day.isoformat())
            if not expected or expected[-1]>target:continue
            # The first week can be truncated by the fixed daily window.
            if expected[0]<rows[0]['date']:continue
            if any(day not in by_date for day in expected):return dict(missing,reason='周内日线缺口')
            chunk=[by_date[day] for day in expected]
            weeks.append({'date':expected[-1],'open':chunk[0]['open'],'close':chunk[-1]['close'],
                          'high':max(r['high'] for r in chunk),'low':min(r['low'] for r in chunk),
                          'volume':sum(r['volume'] for r in chunk)})
            if len(weeks)==21:break
    except (KeyError,TypeError,ValueError):return missing
    if len(weeks)<21:return dict(missing,completed_weeks=len(weeks))
    weeks.reverse()
    last=dt.date.fromisoformat(weeks[-1]['date'])
    if (target_day-last).days>10:return dict(missing,reason='完整周线过旧')
    closes=[r['close'] for r in weeks];ma5,ma10,ma20=[mean(closes[-n:]) for n in (5,10,20)]
    previous=mean(closes[-21:-1]);regime=('up' if closes[-1]>ma5>ma10>ma20 and ma20>previous
                                        else 'down' if closes[-1]<ma5<ma10<ma20 and ma20<previous else 'range')
    return {'status':'complete','regime':regime,'as_of':weeks[-1]['date'],'completed_weeks':len(weeks),
            'ma5':ma5,'ma10':ma10,'ma20':ma20,'close':closes[-1]}


def cap_context(quote,symbol,target,now):
    unknown={'status':'unavailable','size':'unknown','focus':'流通市值未核验，不按总市值替代','turnover_ratio':None}
    try:
        stamp=dt.datetime.fromisoformat(quote['datetime']);fetched=dt.datetime.fromisoformat(quote['fetched_at'])
        cap=quote.get('circulating_cap_100m');turnover=quote.get('turnover_ratio');host=urlsplit(quote['source'])
        if (quote.get('symbol')!=symbol or quote.get('date')!=target or stamp.tzinfo is None or fetched.tzinfo is None
                or stamp.astimezone(TZ).date().isoformat()!=target or not stamp<=fetched<=now
                or host.scheme!='https' or host.hostname!='qt.gtimg.cn' or host.username or host.password
                or host.port not in (None,443) or host.query or host.fragment or not finite(cap,True)):
            return unknown
        if not finite(turnover) or not 0<=turnover<=100:turnover=None
        size='large' if cap>1000 else 'small' if cap<200 else 'medium'
        active,hot=(3,5) if size=='large' else (7,15) if size=='small' else (None,None)
        activity=('unknown' if turnover is None else 'unclassified' if active is None else
                  'very_active' if turnover>hot else 'active' if turnover>active else 'normal')
        focus={'large':'侧重MA60/MA120与温和放量，降低KDJ短线摆动的解释权重',
               'small':'侧重换手与量能异常，强趋势中的超买不单独判卖出',
               'medium':'趋势与量价并重，不套用大盘或小盘的换手阈值'}[size]
        return {'status':'complete','size':size,'circulating_cap_100m':cap,'turnover_ratio':turnover,
                'activity':activity,'focus':focus,'as_of':stamp.isoformat(),'source':quote['source']}
    except (KeyError,TypeError,ValueError):return unknown


def diagnose(bars,symbol,target,*,calendar=None,quote=None,now=None):
    now=now or dt.datetime.now(TZ)
    result={'protocol':PROTOCOL,'as_of':target,'status':'unavailable','price_basis':'daily_qfq_research',
            'ranking_applied':False,'sections':[]}
    try:
        if now.tzinfo is None or dt.date.fromisoformat(target)>now.astimezone(TZ).date():raise ValueError('研究日期无效')
        rows=_rows(bars,target)
        if target==now.astimezone(TZ).date().isoformat() and now.astimezone(TZ).hour<16:raise ValueError('当日日线尚未确认收盘')
        if calendar:
            expected=[];first=dt.date.fromisoformat(rows[-120]['date']);last=dt.date.fromisoformat(target)
            for offset in range((last-first).days+1):
                day=first+dt.timedelta(days=offset);info=calendar.get(str(day.year),{})
                if info.get('verified') is not True:break
                if day.weekday()<5 and day.isoformat() not in info.get('holidays',[]):expected.append(day.isoformat())
            else:
                if [r['date'] for r in rows[-120:]]!=expected:raise ValueError('最近120日日线不完整')
    except (TypeError,ValueError,KeyError):return dict(result,reason='日线日期、长度或价量未通过核验')
    close=[r['close'] for r in rows];price=close[-1]
    ma={f'ma{n}':mean(close[-n:]) for n in (5,10,20,60,120)}
    slope=(ma['ma20']/mean(close[-25:-5])-1)*100
    daily='up' if price>ma['ma5']>ma['ma10']>ma['ma20'] and slope>0 else 'down' if price<ma['ma5']<ma['ma10']<ma['ma20'] and slope<0 else 'range'
    week=weekly_trend(rows,target,calendar);tech=indicators(rows);cap=cap_context(quote,symbol,target,now)
    average=mean(r['volume'] for r in rows[-21:-1]);ratio=rows[-1]['volume']/average if average>0 else None
    change=(price/close[-2]-1)*100
    volume=('no_trade' if rows[-1]['volume']==0 else 'unavailable' if ratio is None else
            'expanding_up' if change>0 and ratio>=1.5 else 'dry_up' if change>0 and ratio<=.8 else
            'expanding_down' if change<0 and ratio>=1.5 else 'dry_down' if change<0 and ratio<=.8 else 'balanced')
    recent,prior=rows[-20:],rows[-40:-20]
    structure=('higher' if max(r['high'] for r in recent)>max(r['high'] for r in prior) and min(r['low'] for r in recent)>min(r['low'] for r in prior)
               else 'lower' if max(r['high'] for r in recent)<max(r['high'] for r in prior) and min(r['low'] for r in recent)<min(r['low'] for r in prior) else 'mixed')
    levels=[{'label':name,'price':value,'role':'support' if value<price else 'resistance' if value>price else 'at_price',
             'distance_pct':(value/price-1)*100} for name,value in [*ma.items(),('BOLL上轨',tech['boll']['upper']),
             ('BOLL下轨',tech['boll']['lower']),('前20日高点',max(r['high'] for r in rows[-21:-1])),
             ('前20日低点',min(r['low'] for r in rows[-21:-1])),('前60日高点',max(r['high'] for r in rows[-61:-1])),
             ('前60日低点',min(r['low'] for r in rows[-61:-1]))] if finite(value,True)]
    risks=[]
    if daily=='down':risks.append('日线均线空头排列，短线趋势偏弱')
    if price<ma['ma60']:risks.append('收盘低于MA60，中期趋势尚未修复')
    if week['regime']=='down':risks.append('周线下行，日线走强暂按反弹处理')
    if volume=='expanding_down':risks.append('放量下跌，需观察承接')
    if tech['rsi14']>80:risks.append('RSI处于极端高位，警惕追高')
    if cap.get('activity')=='very_active':risks.append('换手显著活跃，不能据此认定资金净流入')
    result.update(status='complete',sample_bars=len(rows),reference_close=price,daily_regime=daily,weekly=week,structure=structure,
                  ma=ma,ma20_slope_5d_pct=slope,indicators=tech,cap=cap,volume_state=volume,volume_ratio20=ratio,
                  levels=levels,risks=risks,shadow_weekly_filter={'status':'research_only','would_pass':None if week['status']!='complete' else week['regime']!='down'})
    result['sections']=sections(result)
    return result


def sections(report):
    labels={'up':'向上','down':'向下','range':'震荡','unknown':'待核验'}
    week=report['weekly'];tech=report['indicators'];cap=report['cap'];ma=report['ma']
    fmt=lambda x:f'{x:.2f}' if finite(x) else '—'
    crossing={'golden':'本日金叉','dead':'本日死叉','none':'本日无新交叉'}[tech['macd']['cross']]
    crossing+=' · '+('零轴上' if tech['macd']['above_zero'] else '零轴下' if tech['macd']['dif']<0 and tech['macd']['dea']<0 else '零轴附近或跨轴')
    volume={'expanding_up':'放量上涨','dry_up':'缩量上涨','expanding_down':'放量下跌','dry_down':'缩量下跌',
            'balanced':'量价变化温和','no_trade':'当日无成交','unavailable':'成交量待核验'}[report['volume_state']]
    supports=sorted((r for r in report['levels'] if r['role']=='support'),key=lambda x:-x['price'])[:3]
    pressures=sorted((r for r in report['levels'] if r['role']=='resistance'),key=lambda x:x['price'])[:3]
    facts=lambda pairs:[{'label':a,'value':b} for a,b in pairs]
    return [
        {'title':'趋势与周期','text':f"日线{labels[report['daily_regime']]} · 完整周线{labels[week['regime']]}"+(f"（截至{week['as_of']}）" if week.get('as_of') else '（数据不足）'),
         'facts':facts([('MA60 / MA120',fmt(ma['ma60'])+' / '+fmt(ma['ma120'])),('MA20近5日变化',fmt(report['ma20_slope_5d_pct'])+'%'),
                        ('区间高低点结构',{'higher':'高低点均抬升','lower':'高低点均降低','mixed':'高低点未同向'}[report['structure']])])},
        {'title':'量价与流通盘','text':volume+'；'+cap['focus'],
         'facts':facts([('成交量 / 前20日均量',fmt(report['volume_ratio20'])+' 倍'),('流通市值',fmt(cap.get('circulating_cap_100m'))+' 亿元'),
                        ('同日换手率',fmt(cap.get('turnover_ratio'))+'%')])},
        {'title':'技术指标','text':'MACD(12,26,9) · KDJ(9,3,3) · RSI(14) · BOLL(20,2)。同源指标不重复加分。',
         'facts':facts([('MACD DIF / DEA',fmt(tech['macd']['dif'])+' / '+fmt(tech['macd']['dea'])),('MACD交叉',crossing),
                        ('K / D / J',' / '.join(fmt(tech['kdj'][k]) for k in ('k','d','j'))),('RSI14',fmt(tech['rsi14'])+(' · 极端高位' if tech['rsi14']>80 else ' · 极端低位' if tech['rsi14']<20 else '')),
                        ('BOLL下 / 中 / 上',' / '.join(fmt(tech['boll'][k]) for k in ('lower','mid','upper'))),
                        ('BOLL带宽',fmt(tech['boll']['width_pct'])+'% · '+('扩大' if tech['boll']['expanding'] else '收窄或持平'))])},
        {'title':'支撑与压力','text':'前复权收盘 '+fmt(report['reference_close'])+' 元；按收盘上下分组，未核验盘中成交。',
         'facts':facts([('下方参考','；'.join(r['label']+' '+fmt(r['price']) for r in supports) or '当前区间无下方参考'),
                        ('上方参考','；'.join(r['label']+' '+fmt(r['price']) for r in pressures) or '当前区间无上方参考')])},
        {'title':'综合诊断','text':'；'.join(report['risks']) or '当前未触发本模块列出的风险条件；继续核对原筛选与公告。'}]


def calendar_for(target,provider):
    """One caller-owned cached calendar bundle, shared across a whole export."""
    try:year=dt.date.fromisoformat(target).year
    except (TypeError,ValueError):return {}
    result={}
    for y in (year-1,year):
        try:result[str(y)]=provider(y)
        except Exception:pass
    return result
