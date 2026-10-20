"""Publish public market research only; never serialize Service.portfolio/config."""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
META_KEYS = {'app_name','now','status','last_success','data_date','report_date','expected_date','stale',
             'universe','coverage','mode','schedule','next_run','warnings','daily_buy',
             'daily_plan_status','daily_plan_message','exclusion_policy','model_quality'}


def public_watchlist(value):
    """A small independent ranking, never a duplicate of news/model payloads."""
    keys={'protocol','date','data_date','signal_date','generated_at','prepared_at','prepared_data_date',
          'release_at','published_at','publication_timing','late','status','model_validated','actionable',
          'eligible','coverage','verification','warnings','method','scope','message','rankings_immutable',
          'release_basis','review_at','current_risk_verification'}
    result={key:copy.deepcopy(value[key]) for key in keys if key in value}
    item_keys={'symbol','name','asof','score','components','reasons','risk_notes','verification'}
    verification_keys={'quote_date','quote_consistent','price_history_complete','chase_passed',
        'financial_complete','financial_data_date','financial_report_date','financial_publication_date',
        'model_validation_status','announcements_status','session_status','ai_status',
        'known_risk_veto_count','evidence_cutoff','financial_status','ai_reviewed'}
    def item(raw):
        row={key:copy.deepcopy(raw[key]) for key in item_keys if key in raw}
        row['verification']={key:copy.deepcopy(v) for key,v in raw.get('verification',{}).items() if key in verification_keys}
        components=raw.get('components',{})
        row['components']={key:copy.deepcopy(components[key]) for key in ('technical','financial','atr_pct','return_5d_pct','ma20_deviation_pct') if key in components}
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
    result['verification']={key:copy.deepcopy(v) for key,v in value.get('verification',{}).items() if key in verification_keys}
    if isinstance(value.get('current_risk_verification'),dict):
        result['current_risk_verification']={key:copy.deepcopy(v) for key,v in value['current_risk_verification'].items() if key in verification_keys}
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
    for name in ('styles.css','favicon.svg'):
        if (engine/'static'/name).exists(): shutil.copyfile(engine/'static'/name,output/name)
    shutil.copyfile(HERE/'pages.js',output/'pages.js')
    shutil.copyfile(engine/'MIGRATION.md',output/'MIGRATION.md')
    (output/'.nojekyll').write_text('',encoding='utf-8')
    full_dashboard = service.dashboard()
    dashboard = public_dashboard(full_dashboard)
    write('data/dashboard.json',dashboard)
    write('data/daily-plan.json',daily_plan.read())
    moment=core.now()
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
    write('data/longterm.json',service.longterm())
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
    for stock in [full_dashboard.get('index'), *full_dashboard.get('stocks',[])]:
        if not stock or not re.fullmatch(r'sh\d{6}',stock.get('symbol','')): continue
        history = core.load_history(stock['symbol'])
        chart_bars=[{k:b[k] for k in ('date','close') if k in b} for b in history.get('bars',[])[-180:]]
        write('data/stocks/'+stock['symbol']+'.json',{'stock':stock,'bars':chart_bars,
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
