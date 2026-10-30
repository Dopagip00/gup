"""Publish public market research only; never serialize Service.portfolio/config."""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
META_KEYS = {'app_name','now','status','last_success','data_date','report_date','expected_date','stale',
             'universe','coverage','mode','schedule','next_run','warnings','daily_buy',
             'daily_plan_status','daily_plan_message','exclusion_policy','model_quality','main_wave_market'}

QUOTE_EVIDENCE_KEYS = {'quote_basis','quote_date','price_reference_date','quote_consistent',
                       'quote_source','quote_datetime','quote_fetched_at','previous_close'}
SHORT_CONFIRMATION_KEYS = {'protocol','model_version','validation_protocol','target_kind','as_of_date',
    'expected_return_pct','round_trip_cost_stress_pct','session_dates','stock_closes','benchmark_closes',
    'ma5','stock_return_5d_pct','benchmark_return_5d_pct','relative_return_5d_pct'}


def _public_short_confirmation(raw, data_date):
    """Export only the reproducible, dated direction check, never a buy flag."""
    if not isinstance(raw, dict) or not SHORT_CONFIRMATION_KEYS <= raw.keys():
        raise ValueError('短线方向证据不完整')
    value = {key:copy.deepcopy(raw[key]) for key in SHORT_CONFIRMATION_KEYS}
    number = lambda x:type(x) in (int,float) and math.isfinite(x)
    if (value['protocol'] != 'short-confirmation-v1' or value['model_version'] != 'ridge-cycle-v3'
            or value['validation_protocol'] != 'purged-expanding-3x60-decision-v1'
            or value['target_kind'] != 'next_open_to_following_close' or value['as_of_date'] != data_date
            or not number(value['expected_return_pct']) or not number(value['round_trip_cost_stress_pct'])
            or value['round_trip_cost_stress_pct'] != .30 or value['expected_return_pct'] <= .30):
        raise ValueError('短线模型日期、协议或成本方向不一致')
    dates = value['session_dates']
    if (not isinstance(dates,list) or len(dates)!=6 or any(not isinstance(day,str) for day in dates)
            or sorted(set(dates))!=dates or dates[-1]!=data_date
            or any(dt.date.fromisoformat(day).isoformat()!=day for day in dates)):
        raise ValueError('短线方向交易日证据错误')
    for key in ('stock_closes','benchmark_closes'):
        if not isinstance(value[key],list) or len(value[key])!=6 or any(not number(x) or x<=0 for x in value[key]):
            raise ValueError('短线方向价格证据错误')
    stock, index = value['stock_closes'],value['benchmark_closes']
    ma5=sum(stock[-5:])/5
    r5=(stock[-1]/stock[0]-1)*100
    b5=(index[-1]/index[0]-1)*100
    derived={'ma5':ma5,'stock_return_5d_pct':r5,'benchmark_return_5d_pct':b5,'relative_return_5d_pct':r5-b5}
    if (stock[-1]<ma5 or r5<=0 or r5-b5<0 or any(not number(value[key])
            or not math.isclose(value[key],expected,rel_tol=1e-9,abs_tol=1e-9) for key,expected in derived.items())):
        raise ValueError('短线方向算数或相对强度不一致')
    return value


def _quote_instant(value):
    if not isinstance(value, str):
        raise ValueError('观察报价证据缺少真实带时区时间')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('观察报价证据不能使用无时区时间')
    return result.astimezone(dt.timezone(dt.timedelta(hours=8)))


def _public_quote_evidence(raw, *, date, data_date, cutoff):
    """Keep corroborating quote dates distinct from the original price date."""
    if not isinstance(raw, dict) or not QUOTE_EVIDENCE_KEYS <= raw.keys():
        raise ValueError('观察报价证据字段不完整')
    for day in (date, data_date):
        if not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError('观察报价证据日期无效')
    result = {key: copy.deepcopy(raw[key]) for key in QUOTE_EVIDENCE_KEYS}
    basis = result['quote_basis']
    same = basis == 'same_session_raw_close'
    if basis not in ('same_session_raw_close','next_session_previous_close','auction_previous_close_unverified'):
        raise ValueError('观察报价证据协议无效')
    if (result['price_reference_date'] != data_date
            or result['quote_date'] != (data_date if same else date)
            or result['quote_consistent'] is not (basis != 'auction_previous_close_unverified')):
        raise ValueError('观察报价日期或一致性标记不匹配')
    stamp, fetched, checked = map(_quote_instant,
        (result['quote_datetime'], result['quote_fetched_at'], cutoff))
    if (stamp.date().isoformat() != result['quote_date'] or not stamp <= fetched <= checked
            or not isinstance(data_date, str) or not isinstance(date, str) or not data_date < date):
        raise ValueError('观察报价证据日期或实际时刻无效')
    source = result['quote_source']
    # Older same-session snapshots did not record a source URL. Keep that
    # absence explicit; a next-session corroboration always needs provenance.
    if source is not None or not same:
        if not isinstance(source, str) or len(source) > 300:
            raise ValueError('观察报价证据来源无效')
        parsed = urlsplit(source)
        if (parsed.scheme != 'https' or parsed.hostname != 'qt.gtimg.cn'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('观察报价证据来源未经核验')
    previous = result['previous_close']
    if (same and previous is not None or not same and
            (type(previous) not in (int, float) or not math.isfinite(previous) or previous <= 0)):
        raise ValueError('观察报价昨收证据无效')
    return result


def public_watchlist(value):
    """A small independent ranking, never a duplicate of news/model payloads."""
    keys={'protocol','date','data_date','signal_date','generated_at','prepared_at','prepared_data_date',
          'release_at','published_at','publication_timing','late','status','model_validated','actionable',
          'eligible','coverage','verification','warnings','method','scope','message','rankings_immutable',
          'release_basis','review_at','current_risk_verification','ranking_policy','main_wave_market'}
    result={key:copy.deepcopy(value[key]) for key in keys if key in value}
    item_keys={'symbol','name','asof','score','components','reasons','risk_notes','verification'}
    verification_keys={'quote_date','quote_consistent','price_history_complete','chase_passed',
        'financial_complete','financial_data_date','financial_report_date','financial_publication_date',
        'model_validation_status','announcements_status','session_status','ai_status',
        'known_risk_veto_count','evidence_cutoff','financial_status','ai_reviewed',
        'financial_quality_available','financial_evidence_basis','financial_valuation_date','financial_provider_update_date',
        'risk_window_start','risk_window_end','risk_return_samples',
        'financial_roe_report_date','financial_roe_publication_date'}
    def verification(raw, cutoff):
        raw = raw if isinstance(raw, dict) else {}
        projected = {key:copy.deepcopy(v) for key,v in raw.items() if key in verification_keys}
        if 'quote_basis' in raw:
            projected.update(_public_quote_evidence(raw, date=value.get('date'),
                data_date=value.get('data_date'), cutoff=cutoff))
        return projected
    def item(raw):
        row={key:copy.deepcopy(raw[key]) for key in item_keys if key in raw}
        if 'short_confirmation' in raw:
            row['short_confirmation']=_public_short_confirmation(raw['short_confirmation'],value.get('data_date'))
        elif value.get('ranking_policy') in ('short-confirmation-observation-v3','main-wave-observation-v4'):
            raise ValueError('新版榜单缺少短线方向证据')
        if value.get('ranking_policy') == 'main-wave-observation-v4':
            from engine import main_wave
            if not main_wave.valid(raw.get('main_wave'), value.get('data_date')):
                raise ValueError('主升浪证据无效')
            if _quote_instant(raw['main_wave']['sector']['membership_at']) > _quote_instant(value.get('generated_at')):
                raise ValueError('行业证据来自未来')
            row['main_wave'] = copy.deepcopy(raw['main_wave'])
        row['verification']=verification(raw.get('verification'), value.get('generated_at'))
        components=raw.get('components',{})
        row['components']={key:copy.deepcopy(components[key]) for key in ('technical','financial','atr_pct','return_5d_pct','ma20_deviation_pct',
            'technical_raw','risk_penalty','downside_deviation_60d_pct','max_drawdown_60d_pct','liquidity_proxy_20d_cny','market_regime') if key in components}
        row['components']['weights']={key:components.get('weights',{}).get(key) for key in ('technical','financial')}
        for key,limit in (('reasons',5),('risk_notes',8)):
            row[key]=[str(text)[:300] for text in raw.get(key,[])[:limit]]
        row.update(model_validated=False,eligible=False,actionable=False,
                   entry_low=None,entry_high=None,stop_loss=None,take_profit=None,max_position_pct=None)
        return row
    result['items']=[item(raw) for raw in (value.get('items') or [])[:5] if isinstance(raw,dict)]
    result['top_pick']=result['items'][0] if result['items'] else None
    result['alerts']=[{key:raw[key] for key in ('symbol','reason','source','withdrawn') if key in raw}
                      for raw in (value.get('alerts') or [])[:5] if isinstance(raw,dict)]
    result['withdrawn_symbols']=[s for s in (value.get('withdrawn_symbols') or [])[:5] if isinstance(s,str) and re.fullmatch(r'sh\d{6}',s)]
    result['verification']=verification(value.get('verification'), value.get('generated_at'))
    if isinstance(value.get('current_risk_verification'),dict):
        current=value['current_risk_verification']
        result['current_risk_verification']=verification(current, value.get('review_at'))
        if 'quotes' in current:
            quotes=current['quotes']
            if not isinstance(quotes,dict) or len(quotes)>5:
                raise ValueError('原队列报价复核超出公开范围')
            symbols={row.get('symbol') for row in result['items']}
            quote_cutoff=current.get('evidence_cutoff') or value.get('review_at')
            if quotes and _quote_instant(quote_cutoff)>_quote_instant(value.get('review_at')):
                raise ValueError('原队列报价证据晚于实际复核')
            result['current_risk_verification']['quotes']={symbol:_public_quote_evidence(evidence,
                date=value.get('date'),data_date=value.get('data_date'),cutoff=quote_cutoff)
                for symbol,evidence in quotes.items() if symbol in symbols and isinstance(symbol,str)
                and re.fullmatch(r'sh(?:60[0135]|68[89])\d{3}',symbol)}
    result['warnings']=[str(text)[:500] for text in value.get('warnings',[])[:12]]
    result.update(model_validated=False,eligible=False,actionable=False)
    return result


def watchlist_export_dates(core, moment):
    today=moment.date()
    trading=core.md.is_trading_day(today.isoformat())
    if trading and moment.time().replace(tzinfo=None)<dt.time(16):
        return today.isoformat(),None
    for offset in range(1,41):
        candidate=today+dt.timedelta(days=offset)
        if core.md.is_trading_day(candidate.isoformat()):
            # A trading day's published ranking remains the primary all day.
            # A separate next file and dated archive expose future preparation.
            return (today.isoformat(),candidate.isoformat()) if trading else (candidate.isoformat(),None)
    return today.isoformat(),None


def public_dashboard(value):
    result = {key: copy.deepcopy(value.get(key)) for key in ('index','stocks')}
    table_keys = {'symbol','name','date','close','raw_close','change_pct','score','eligible','excluded',
                  'exclusion_reason','stale','entry_low','entry_high','stop_loss','take_profit','research_levels','levels_status','chase_risk'}
    prediction_keys = {'available','eligible','horizon_days',
                       'expected_return_pct','interval_low_pct','interval_high_pct','validation_mae_pct',
                       'validation_samples','rank_value','model_version','validation_protocol',
                       'validation_protocol_complete','validation_aggregate','validation_decision','reason'}
    result['stocks'] = [{**{k:v for k,v in s.items() if k in table_keys},
        'prediction':{k:v for k,v in (s.get('prediction') or {}).items() if k in prediction_keys}}
        for s in result.get('stocks') or []]
    def short_reason(value):
        if not isinstance(value, str):return value
        parts=[x.strip() for x in re.split(r'[；\n]',value) if x.strip()]
        brief='；'.join(parts[:2]);suffix='；更多见详情'
        if len(parts)>2 or len(brief)>300:return brief[:300-len(suffix)]+suffix
        return brief
    for stock in result['stocks']:
        prediction=stock['prediction']
        if 'reason' in prediction:prediction['reason']=short_reason(prediction['reason'])
        decision = prediction.get('validation_decision')
        if isinstance(decision, dict):
            prediction['validation_decision'] = {k: decision[k] for k in ('protocol','eligible') if k in decision}
        aggregate=prediction.get('validation_aggregate')
        if isinstance(aggregate,dict):prediction['validation_aggregate']={k:aggregate[k] for k in ('samples','mae_pct') if k in aggregate}
        risk=stock.get('chase_risk')
        if isinstance(risk,dict):
            compact={k:risk[k] for k in ('version','as_of_date','status','blocked','gate_passed') if k in risk}
            compact['reasons']=[{k:((str(v)[:132]+'；更多见详情') if len(str(v))>140 else v) if k=='detail' else v for k,v in item.items()
                                 if k in {'code','severity','detail'}}
                                for item in risk.get('reasons',[])[:2] if isinstance(item,dict)]
            if len(risk.get('reasons',[]))>2 and compact['reasons']:
                last=compact['reasons'][-1];last['detail']=str(last.get('detail',''))[:132]+'；更多见详情'
            stock['chase_risk']=compact
        qualified=(stock.get('eligible') is True and prediction.get('eligible') is True
                   and isinstance(decision,dict) and decision.get('eligible') is True
                   and isinstance(risk,dict) and risk.get('gate_passed') is True
                   and not stock.get('stale') and not stock.get('excluded'))
        if stock.get('raw_close') is not None:stock.pop('close',None)
        if not qualified:
            # Missing flags remain fail-closed in the UI. Repeated protocol names
            # and empty actionable levels add no evidence to a rejected table row.
            for key in ('eligible','horizon_days','model_version','validation_protocol','validation_decision','rank_value'):
                prediction.pop(key,None)
            if prediction.get('available') is True:prediction.pop('available')
            if isinstance(aggregate,dict) and aggregate.get('samples') is not None:
                prediction.pop('validation_samples',None)
                if aggregate.get('mae_pct') is not None:prediction.pop('validation_mae_pct',None)
            for key in ('eligible','entry_low','entry_high','stop_loss','take_profit','research_levels','levels_status'):
                stock.pop(key,None)
            if isinstance(risk,dict) and risk.get('blocked') is True:
                stock['chase_risk']={'blocked':True,'reasons':stock['chase_risk']['reasons']}
            else:stock.pop('chase_risk',None)
        for key in list(stock):
            if stock[key] is None or stock[key] is False:stock.pop(key)
    result['meta'] = {k: copy.deepcopy(v) for k,v in value.get('meta',{}).items() if k in META_KEYS}
    result['meta'].update(hosted=True, is_trading=False, hosting='github-pages',
        schedule='北京时间交易日08:00采集、09:00发布（GitHub Actions可能延迟；以实际报告时间为准）')
    result['meta']['warnings'] = [x for x in result['meta'].get('warnings',[])
                                 if '云端08:00/09:00计划尚未完成注册验证' not in str(x)]
    result['meta']['warnings'].append('静态研究快照，无实时行情；采集或发布失败时不会自动补造建议。')
    if isinstance(result['meta'].get('last_success'),dict):
        result['meta']['last_success']={k:v for k,v in result['meta']['last_success'].items()
            if k in {'target_date','finished_at','universe','status','ok','failed','total'}}
    result['runs'] = [{k:v for k,v in run.items() if k in {'started_at','ended_at','target_date','status','total','ok','failed'}}
                      for run in value.get('runs',[])]
    return result


def export_site(output_dir, service, engine_dir=None):
    import core
    import daily_plan
    import forecast_audit
    import daily_watchlist
    import market_brief
    output = Path(output_dir).resolve()
    engine = Path(engine_dir or core.BASE).resolve()
    version = json.loads((engine/'version.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False), encoding='utf-8')
    html = (engine/'static/index.html').read_text(encoding='utf-8')
    html = html.replace('href="/styles.css"','href="./styles.css"').replace('src="/app.js"',
        'src="./app.js"').replace('<script defer src="./app.js"></script>',
        '<script defer src="./pages.js"></script>\n  <script defer src="./app.js"></script>')
    html = html.replace('href="/api/migration-guide"','href="./MIGRATION.md"')
    for asset in ('app.js', 'pages.js', 'styles.css'):
        html = html.replace(f'="./{asset}"', f'="./{asset}?v={version["version"]}"')
    html = html.replace('一次成功的数据更新后，报告将自动保留在本地。','交易日研究快照由云端任务保存并发布。')
    html = html.replace('设置研究范围与数据获取频率。','全部沪市 A 股研究；网站显示云端生成的静态快照。')
    html = html.replace('盘中行情刷新间隔（分钟）','本地版本行情间隔（网站无盘中行情）')
    (output/'index.html').write_text(html,encoding='utf-8')
    script = (engine/'static/app.js').read_text(encoding='utf-8')
    script = script.replace("async function api(path, options = {}) {", "async function api(path, options = {}) {\n  if(window.PagesResearch) return window.PagesResearch.api(path, options);")
    script = script.replace("$('export-btn').href=`/api/export.csv${state.historyDate?`?date=${encodeURIComponent(state.historyDate)}`:''}`;",
        "$('export-btn').href=window.PagesResearch.download(state.historyDate||m.report_date);")
    script = script.replace('download.href=`/api/export.csv?date=${encodeURIComponent(date)}`;',
        'download.href=window.PagesResearch.download(date);')
    script = script.replace("'↻ 更新研究'","'↻ 重新读取结果'")
    script = script.replace("const predictionRank = stock => numeric(stock?.prediction?.rank_value) ? Number(stock.prediction.rank_value) : -Infinity;",
        "const predictionRank = stock => numeric(stock?.prediction?.rank_value) ? Number(stock.prediction.rank_value) : numeric(stock?.prediction?.raw_rank_value) ? Number(stock.prediction.raw_rank_value) : -Infinity;")
    script = script.replace("'云端调度'","'GitHub 云端快照'")
    script = script.replace('云端托管：数据获取与交易日研究由云端任务运行，不依赖你的电脑开机。计划生成结果以每日交易计划的报告日期与状态为准。',
        'GitHub Pages 静态快照：Actions 云端任务采集与发布，不依赖电脑开机。定时任务可能延迟或失败，请核对报告日期、实际更新时间和运行状态。重新读取不会触发采集。')
    (output/'app.js').write_text(script,encoding='utf-8')
    for name in ('styles.css','favicon.svg','favicon.ico','favicon-32.png','apple-touch-icon.png'):
        if (engine/'static'/name).exists(): shutil.copyfile(engine/'static'/name,output/name)
    shutil.copyfile(HERE/'pages.js',output/'pages.js')
    shutil.copyfile(engine/'MIGRATION.md',output/'MIGRATION.md')
    (output/'.nojekyll').write_text('',encoding='utf-8')
    full_dashboard = service.dashboard()
    dashboard = public_dashboard(full_dashboard)
    write('data/dashboard.json',dashboard)
    write('data/daily-plan.json',daily_plan.read())
    moment=core.now()
    write('data/market-brief.json',market_brief.read(core=core,now=moment))
    with core.connect() as con:
        brief_rows=con.execute("SELECT key FROM kv WHERE key LIKE 'market_brief:%' ORDER BY key DESC LIMIT 366").fetchall()
    for row in brief_rows:
        brief_date=row[0][len('market_brief:'):]
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',brief_date):
            write('data/market-briefs/'+brief_date+'.json',market_brief.read(brief_date,core=core,now=moment))
    watch_date,next_watch_date=watchlist_export_dates(core,moment)
    write('data/daily-watchlist.json',public_watchlist(daily_watchlist.read(date=watch_date,service=service,now=moment)))
    if next_watch_date:
        write('data/daily-watchlist-next.json',public_watchlist(daily_watchlist.read(date=next_watch_date,service=service,now=moment)))
    watch_index=[]
    with core.connect() as con:
        watch_rows=con.execute("SELECT key,value FROM kv WHERE key LIKE 'daily_watchlist:%' ORDER BY key DESC").fetchall()
    for key,raw in watch_rows:
        date=key[len('daily_watchlist:'):]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):continue
        value=json.loads(raw)
        write('data/watchlists/'+date+'.json',public_watchlist(daily_watchlist.read(date=date,service=service,now=moment)))
        watch_index.append({'date':date,'data_date':value.get('data_date'),'published_at':value.get('published_at')})
    write('data/watchlists.json',watch_index)
    write('data/forecast-audit.json',forecast_audit.evaluate(core))
    write('data/longterm.json',service.longterm(monthly=True))
    with core.connect() as con:
        longterm_months=con.execute("SELECT key,value FROM kv WHERE key LIKE 'longterm_month:%' ORDER BY key").fetchall()
    for key,value in longterm_months:
        month=key[len('longterm_month:'):]
        if re.fullmatch(r'\d{4}-\d{2}',month):write('data/longterm-months/'+month+'.json',json.loads(value))
    write('data/quotes.json',{'quotes':[], 'status':'GitHub Pages 为收盘研究快照，不提供实时或盘中自动报价。'})
    write('data/config.json',{'hosted':True,'universe':'shanghai','watchlist':[],'refresh_minutes':5})
    from ai_review import public_status
    write('data/system.json',{'version':version,'hosted':True,'project_path':'GitHub / Dopagip00/gup',
        'ai_review':public_status(core.getkv('ai_review',{})),
        'migration':{'message':'新电脑下载仓库即可继续开发；本机研究数据请使用完整迁移包。云端最近研究状态在 Actions artifact，公开快照在本网站。',
                     'backup_command':'git clone https://github.com/Dopagip00/gup.git'}})
    reports = service.reports()
    write('data/reports.json',reports)
    for report in reports:
        date = report['date']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): continue
        write('data/reports/'+date+'.json',public_dashboard(service.dashboard(date)))
        folder=output/'data/csv';folder.mkdir(parents=True,exist_ok=True)
        (folder/(date+'.csv')).write_text(service.export_csv(date),encoding='utf-8')
    count=0
    from engine import main_wave, technical_diagnosis, factor_screen
    factor_financial = core.getkv('longterm', {})
    wave_index = core.load_history('sh000001').get('bars', [])
    wave_date = dashboard['meta'].get('data_date')
    technical_calendar=technical_diagnosis.calendar_for(wave_date,core.md.get_calendar)
    technical_quotes=core.getkv('valuation_quotes',{}).get('quotes',{})
    wave_sectors = main_wave.sectors(full_dashboard.get('stocks', []), core.getkv('universe_info', {}), wave_date, moment)
    for stock in [full_dashboard.get('index'), *full_dashboard.get('stocks',[])]:
        if not stock or not re.fullmatch(r'sh\d{6}',stock.get('symbol','')): continue
        history = core.load_history(stock['symbol'])
        chart_bars=[{k:b[k] for k in ('date','close') if k in b} for b in history.get('bars',[])[-180:]]
        wave = main_wave.assess(history.get('bars', []), wave_index, stock.get('date'), wave_sectors.get(stock['symbol']))
        main_wave.apply_stock_gates(wave, stock)
        wave['sections'] = main_wave.decision(wave)
        diagnosis=technical_diagnosis.diagnose(history.get('bars',[]),stock['symbol'],stock.get('date'),
            calendar=technical_calendar,quote=technical_quotes.get(stock['symbol']),now=moment)
        factors=factor_screen.report(stock,wave,factor_financial,moment)
        write('data/stocks/'+stock['symbol']+'.json',{'stock':stock,'main_wave':wave,'factor_screen':factors,'technical_diagnosis':diagnosis,'bars':chart_bars,
            'source':history.get('source'),'backtest':None,
            'backtest_error':'网页未发布逐股原始规则回测；预测模型的时间验证指标见上方。原始规则回测可在本机版查看。'})
        count+=1
    calendars={}
    for year in (core.now().year-1,core.now().year,core.now().year+1):
        try: calendars[str(year)]=core.md.get_calendar(year)
        except core.md.CalendarUnavailable: pass
    write('data/calendar.json',calendars)
    status=core.getkv('github_run_status',{})
    write('data/run-status.json',status)
    result={'generated_at':core.now().isoformat(),'data_date':dashboard['meta'].get('data_date'),
            'snapshot_schema_version':1,'app_version':version['version'],'data_schema_version':version['data_schema_version'],
            'stock_files':count,'reports':len(reports),'privacy':'public-market-research-only',
            'repository':'Dopagip00/gup','run':status}
    write('data/manifest.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine-dir',default=str(HERE/'engine'))
    parser.add_argument('--output-dir',default=str(HERE/'public'))
    args=parser.parse_args()
    sys.path.insert(0,str(Path(args.engine_dir).resolve()))
    import core
    print(json.dumps(export_site(args.output_dir,core.Service(),args.engine_dir),ensure_ascii=False))
