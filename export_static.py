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
             'daily_plan_status','daily_plan_message','exclusion_policy'}


def public_dashboard(value):
    result = {key: copy.deepcopy(value.get(key)) for key in ('index','stocks')}
    table_keys = {'symbol','name','date','close','raw_close','change_pct','score','eligible','excluded',
                  'exclusion_reason','stale','signal','entry_low','entry_high','stop_loss','take_profit','research_levels'}
    prediction_keys = {'available','eligible','horizon_days','horizon','return_basis','return_definition',
                       'expected_return_pct','interval_low_pct','interval_high_pct','validation_mae_pct',
                       'validation_samples','rank_value','raw_rank_value'}
    result['stocks'] = [{**{k:v for k,v in s.items() if k in table_keys},
        'prediction':{k:v for k,v in (s.get('prediction') or {}).items() if k in prediction_keys}}
        for s in result.get('stocks') or []]
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
    output = Path(output_dir).resolve()
    engine = Path(engine_dir or core.BASE).resolve()
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
    write('data/longterm.json',service.longterm())
    write('data/quotes.json',{'quotes':[], 'status':'GitHub Pages 为收盘研究快照，不提供实时或盘中自动报价。'})
    write('data/config.json',{'hosted':True,'universe':'shanghai','watchlist':[],'refresh_minutes':5})
    version = json.loads((engine/'version.json').read_text(encoding='utf-8'))
    write('data/system.json',{'version':version,'hosted':True,'project_path':'GitHub / Dopagip00/gup',
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
