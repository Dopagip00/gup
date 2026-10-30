"""One-year quality research, frozen monthly cohorts and permanent risk events."""
from copy import deepcopy
import datetime as dt
import json
import math

POLICY = 'longterm-quality-year-v1'


def number(value):
    if isinstance(value, bool):return None
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):return None


def assess(item, periods):
    """Predeclared three-year quality screen; never changes the legacy score."""
    result={'policy':POLICY,'status':'unknown','score':None,'years':[],'reasons':[]}
    annuals=sorted((key for key in periods if key.endswith('12-31')),reverse=True)
    if not annuals:
        result['reasons']=['缺少年度财报'];return result
    year=int(annuals[0][:4])
    fields=('PARENTNETPROFIT','NETCASH_OPERATE_PK','KCFJCXSYJLR','ROEJQ')
    for y in range(year-2,year+1):
        row=periods.get(f'{y}-12-31',{})
        values={key:number(row.get(key)) for key in fields}
        result['years'].append(dict(values,report_date=f'{y}-12-31',publication_date=row.get('_published'),
                                    provider_update_date=row.get('_updated')))
        if any(v is None for v in values.values()):result['reasons'].append(f'{y}年度盈利或现金流数据不足')
    if (dt.date.fromisoformat(item['date'])-dt.date.fromisoformat(annuals[0])).days>550:
        result['reasons'].append('年度财报偏旧')
    if item.get('score') is None:result['reasons'].append('当期财务数据不足')
    if result['reasons']:return result
    years=result['years'];profit=sum(r['PARENTNETPROFIT'] for r in years)
    cash=sum(r['NETCASH_OPERATE_PK'] for r in years)
    failures=[]
    if any(r['PARENTNETPROFIT']<=0 or r['KCFJCXSYJLR']<=0 for r in years):failures.append('近三年存在亏损或扣非亏损')
    if any(r['NETCASH_OPERATE_PK']<=0 for r in years):failures.append('近三年存在经营现金流非正年度')
    cash_ratio=cash/profit if profit>0 else 0
    recurring=min((r['KCFJCXSYJLR']/r['PARENTNETPROFIT'] if r['PARENTNETPROFIT']>0 else 0) for r in years)
    roe=min(r['ROEJQ'] for r in years)
    if cash_ratio<0.8:failures.append('三年累计经营现金流不足累计净利的80%')
    if recurring<0.6:failures.append('年度扣非利润占比不足60%')
    if roe<8:failures.append('近三年存在年度ROE低于8%')
    # Recompute PE from unrounded disclosed components for threshold decisions.
    parts=item.get('ttm_profit_components',[])
    ttm=sum(p['parent_netprofit']*p['sign'] for p in parts) if parts else 0
    pe=item['market_cap_100m']*1e8/ttm if ttm>0 else math.inf
    for field,bound,label in (('NETCASH_OPERATE_PK',0.5,'当期TTM现金流不足净利50%'),
                              ('KCFJCXSYJLR',0.6,'当期TTM扣非利润不足净利60%')):
        values=[(number(periods.get(part['report_date'],{}).get(field)),part['sign']) for part in parts]
        if not values or any(value is None for value,_ in values):
            result['reasons']=['当期TTM质量原始数据不足'];return result
        if ttm<=0 or sum(value*sign for value,sign in values)/ttm<bound:failures.append(label)
    if not 5<=pe<=30:failures.append('TTM估值不在5至30倍研究范围')
    if item['debt_ratio']>=65:failures.append('资产负债率达到65%')
    if item.get('eligible') is not True:failures.append('当期估值、增长或财务质量未通过原有检查')
    valuation=(25 if pe<=20 else 18)+ (10 if item['pb']<=1 else 8 if item['pb']<=3 else 4)
    quality=min(20,max(0,roe/15*20))+min(20,max(0,cash_ratio*20))+min(10,max(0,recurring/0.8*10))
    growth=min(7.5,max(0,item['revenue_growth']*0.375))+min(7.5,max(0,item['profit_growth']*0.25))
    score=round(valuation+quality+growth,2)
    if score<65:failures.append('一年期质量研究分不足65分')
    result.update(status='fail' if failures else 'pass',score=score,reasons=failures,
                  cash_profit_ratio=cash_ratio,min_annual_roe=roe,min_recurring_ratio=recurring,
                  factors={'valuation':round(valuation,2),'quality':round(quality,2),'growth':round(growth,2)})
    return result


def board(data, previous, quotes, vetoes, moment, expected):
    month=previous['month'] if previous else moment.strftime('%Y-%m');stamp=moment.isoformat()
    rows={r['symbol']:r for r in data.get('stocks',[])}
    coverage=data.get('coverage',{})
    fresh=data.get('as_of')==expected
    complete=(fresh and data.get('policy')==POLICY and coverage.get('valuation_qualified',0)>0
              and coverage.get('financial_complete')==coverage.get('valuation_qualified')
              and coverage.get('not_researched',0)==0 and not data.get('quote_coverage',{}).get('failed_batches'))
    old=deepcopy(previous) if previous else None
    if old and (old.get('policy')!=POLICY or old.get('month')>moment.strftime('%Y-%m')):
        raise ValueError('Monthly cohort identity mismatch')
    if old:
        if dt.datetime.fromisoformat(old['created_at'])>moment:raise ValueError('Future monthly cohort')
    elif complete:
        chosen=[r for r in rows.values() if r.get('long_horizon',{}).get('status')=='pass' and r['symbol'] not in vetoes]
        chosen.sort(key=lambda r:(-r['long_horizon']['score'],r['symbol']))
        old={'policy':POLICY,'month':month,'created_at':stamp,'basis_date':data['as_of'],
             'original':[dict(deepcopy(r),monthly_rank=n,rank_score=r['long_horizon']['score']) for n,r in enumerate(chosen,1)],
             'events':[]}
    result=old or {'policy':POLICY,'month':month,'created_at':None,'original':[],'events':[]}
    withdrawn={e['symbol'] for e in result['events']}
    output=[];members=set()
    for original in result['original']:
        symbol=original['symbol'];members.add(symbol);current=rows.get(symbol)
        quote=quotes.get(symbol,{})
        reasons=[]
        if symbol in vetoes:reasons.append(vetoes[symbol]['reason'])
        if quote.get('date')==expected:
            if 'ST' in str(quote.get('name','')).upper() or '退' in str(quote.get('name','')):reasons.append('新增ST或退市风险')
            pe=number(quote.get('pe_ttm') if quote.get('pe_ttm') is not None else quote.get('pe'));pb=number(quote.get('pb'))
            if pe is not None and not 0<pe<=40:reasons.append('当前报价估值已超出原预筛范围')
            if pb is not None and not 0<pb<=6:reasons.append('当前PB已超出原预筛范围')
        if fresh and current and current.get('long_horizon',{}).get('status')=='fail':
            reasons.extend(current['long_horizon']['reasons'])
        if reasons and symbol not in withdrawn:
            result['events'].append({'symbol':symbol,'observed_at':stamp,'reasons':reasons,'kind':'withdrawal'})
            withdrawn.add(symbol)
        available=fresh and current is not None and current.get('long_horizon',{}).get('status')=='pass'
        row=deepcopy(current or original)
        row.update(monthly_rank=original['monthly_rank'],rank_score=original['rank_score'],
                   legacy_score=row.get('score'),score=(row.get('long_horizon') or {}).get('score'),
                   eligible=available and symbol not in withdrawn,stale=not available,
                   withdrawn=symbol in withdrawn)
        row['risks']=list(row.get('risks',[]))+reasons
        for event in result['events']:
            if event['symbol']==symbol:row['risks']=list(dict.fromkeys(row['risks']+event['reasons']))
        output.append(row)
    for symbol,current in rows.items():
        if symbol not in members:
            row=deepcopy(current);row.update(eligible=False,monthly_rank=None)
            row['risks']=list(row.get('risks',[]))+current.get('long_horizon',{}).get('reasons',[])
            if current.get('long_horizon',{}).get('status')=='pass':row['risks'].append('等待下月排名')
            output.append(row)
    result.update(as_of=data.get('as_of'),checked_at=stamp,stocks=output,coverage=dict(coverage),
                  status='active' if result['created_at'] else 'awaiting_complete_snapshot',
                  warnings=[] if fresh else ['财务快照待更新，原月榜保留'],horizon_years=1,
                  evaluation={'status':'pending','max_drawdown_pct':None,'total_return_pct':None,
                              'reason':'尚无完整一年、可核准分红及公司行动的同口径评价数据'})
    result['coverage']['eligible']=sum(r['eligible'] for r in output)
    return result


def refresh_board(core):
    from daily_watchlist import _known_risks
    moment=core.now();expected=core.expected_date();key='longterm_month:'+moment.strftime('%Y-%m')
    vetoes,verification=_known_risks(moment,expected)
    quotes=core.getkv('valuation_quotes',{}).get('quotes',{})
    if isinstance(quotes,list):quotes={q['symbol']:q for q in quotes}
    with core.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        data=con.execute("SELECT value FROM kv WHERE key='longterm'").fetchone()
        previous=json.loads(row[0]) if row else None
        result=board(json.loads(data[0]) if data else {},previous,quotes,vetoes,moment,expected)
        result['risk_verification']=verification
        if verification.get('announcements_status')!='verified':
            result['warnings'].append('最新公告覆盖未全部核准')
        # Empty incomplete attempts do not freeze the month's universe.
        if result['created_at']:
            con.execute('INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)',(key,core.encode(result)))
        for archive_key,raw in con.execute("SELECT key,value FROM kv WHERE key LIKE 'longterm_month:%' AND key<>?",(key,)).fetchall():
            prior=json.loads(raw)
            created=dt.datetime.fromisoformat(prior['created_at'])
            if moment-created<=dt.timedelta(days=366):
                updated=board(json.loads(data[0]) if data else {},prior,quotes,vetoes,moment,expected)
                con.execute('UPDATE kv SET value=? WHERE key=?',(core.encode(updated),archive_key))
    return result


def evaluate_year(curve, sessions, *, total_return_verified=False, cost_pct=0.30):
    """Evaluate a verified wealth path, including distributions/corporate actions.

    First observation is the recorded entry reference. Later observations are
    session closes; reported drawdown is therefore close-based, not intraday.
    Caller supplies the complete official one-year session grid. Raw/qfq price
    series are not automatically treated as a verified total-return series.
    """
    pending={'status':'unavailable','max_drawdown_pct':None,'total_return_pct':None,
             'max_adverse_pct':None,'net_return_pct':None}
    if total_return_verified is not True:return dict(pending,reason='分红及公司行动口径未核准')
    if not sessions or len(sessions)!=len(set(sessions)) or sessions!=sorted(sessions):
        return dict(pending,reason='交易日序列缺失或无序')
    start,end=map(dt.date.fromisoformat,(sessions[0],sessions[-1]))
    anniversary=start.replace(year=start.year+1,day=28) if start.month==2 and start.day==29 else start.replace(year=start.year+1)
    if not anniversary<=end<=anniversary+dt.timedelta(days=40):return dict(pending,reason='未覆盖一年评价窗口')
    import market_data as md
    try:
        terminal=anniversary
        while terminal<=anniversary+dt.timedelta(days=40) and not md.is_trading_day(terminal):terminal+=dt.timedelta(days=1)
        official=[(start+dt.timedelta(days=i)).isoformat() for i in range((end-start).days+1)
                  if md.is_trading_day(start+dt.timedelta(days=i))]
    except md.MarketDataError:return dict(pending,reason='官方交易日历不可核准')
    if end!=terminal or sessions!=official:return dict(pending,reason='未覆盖完整官方交易日序列')
    if [r.get('date') for r in curve]!=sessions:return dict(pending,reason='收益路径缺失交易日')
    values=[number(r.get('wealth')) for r in curve]
    if any(v is None or v<=0 for v in values):return dict(pending,reason='收益路径数值无效')
    if number(cost_pct) is None or cost_pct<0:return dict(pending,reason='成本口径无效')
    peak=values[0];drawdown=0
    for value in values:
        peak=max(peak,value);drawdown=max(drawdown,(peak-value)/peak*100)
    total=(values[-1]/values[0]-1)*100
    return {'status':'evaluated','max_drawdown_pct':drawdown,'total_return_pct':total,
            'max_adverse_pct':max(0,(1-min(values)/values[0])*100),
            'net_return_pct':total-cost_pct,'cost_pct':cost_pct,'basis':'verified_total_wealth_session_close'}


def compare_year(new_curve, old_curve, sessions, *, total_return_verified=False, cost_pct=0.30):
    new=evaluate_year(new_curve,sessions,total_return_verified=total_return_verified,cost_pct=cost_pct)
    old=evaluate_year(old_curve,sessions,total_return_verified=total_return_verified,cost_pct=cost_pct)
    ready=new['status']==old['status']=='evaluated'
    return {'status':'evaluated' if ready else 'unavailable','new':new,'old':old,
            'lower_drawdown':new['max_drawdown_pct']<old['max_drawdown_pct'] if ready else None,
            'net_return_delta_pct':new['net_return_pct']-old['net_return_pct'] if ready else None}
