"""Immutable daily recommendation cohorts. References are never actual holdings."""
from __future__ import annotations
import datetime as dt
import math
import re
from urllib.parse import urlsplit
import core
import market_data as md

NEWS_SOURCES = {
    'https://www.sse.com.cn/disclosure/listedinfo/announcement/':
        ('上海证券交易所·上市公司公告', {'www.sse.com.cn', 'static.sse.com.cn'}),
    'https://finance.cctv.com/': ('央视网财经', {'finance.cctv.com'}),
}
SESSION_RISK_SOURCE = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/stock/'
NEWS_VERIFICATION_PROTOCOL = 'official-sources-v3-risk31-v1'
RISK_PROTOCOL = 'sse-risk-title-observation-v1'
RISK_QUERIES = ('股票交易', '停牌', '复牌', '立案', '风险警示', '终止上市', '退市整理', '债务逾期', '无法按期偿还')
RISK_SEMANTICS = 'rolling_research_observation_not_risk_resolution'
ANNOUNCEMENT_SOURCE = 'https://www.sse.com.cn/disclosure/listedinfo/announcement/'
AI_PROTOCOL = 'cloud-ai-risk-review-v1'


def session_after(day):
    value = dt.date.fromisoformat(day)
    for _ in range(370):
        value += dt.timedelta(days=1)
        if md.is_trading_day(value):
            return value.isoformat()
    raise ValueError('交易日历不足')


def read(date=None):
    date = date or core.now().date().isoformat()
    saved = core.getkv('daily_plan:'+date)
    if saved:
        return saved
    if not md.is_trading_day(date):
        return {'date':date,'status':'closed','buy':None,'sell':[],'news':[], 'message':'上交所非交易日，无当日交易计划。','timezone':'Asia/Shanghai','mode':'manual_research'}
    return {'date':date,'status':'pending','buy':None,'sell':[], 'news':core.getkv('morning_news',{}).get('items',[]),
            'message':'当日09:00日报尚未发布；当前排名是最近收盘研究。','timezone':'Asia/Shanghai','mode':'manual_research'}


def _instant(value):
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(core.TZ) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def _publication_boundary(item):
    """Compare an explicitly date-only official disclosure without inventing a timestamp."""
    value = item.get('published_at')
    if item.get('published_precision') == 'date' and isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            return dt.datetime.combine(dt.date.fromisoformat(value), dt.time(), core.TZ)
        except ValueError:
            return None
    return _instant(value)


def _risk_observation_ready(moment, begun, fetched, monitoring):
    if not isinstance(monitoring, dict):
        return False
    window = (moment.date() - dt.timedelta(days=31)).isoformat()
    observed = _instant(monitoring.get('observed_at'))
    if not (monitoring.get('protocol') == RISK_PROTOCOL and monitoring.get('status') == 'complete' and
            monitoring.get('source_url') == ANNOUNCEMENT_SOURCE and monitoring.get('window_start') == window and
            monitoring.get('window_end') == moment.date().isoformat() and
            type(monitoring.get('lookback_days')) is int and monitoring['lookback_days'] == 31 and
            monitoring.get('semantics') == RISK_SEMANTICS and observed and begun <= observed <= fetched):
        return False
    queries = monitoring.get('queries')
    if not isinstance(queries, list) or len(queries) != len(RISK_QUERIES):
        return False
    receipts = {}
    for query in queries:
        if not isinstance(query, dict) or query.get('keyword') not in RISK_QUERIES or query['keyword'] in receipts:
            return False
        stamp = _instant(query.get('observed_at'))
        total, scanned, pages = (query.get(key) for key in ('groups_total', 'groups_scanned', 'pages'))
        if not (query.get('status') == 'complete' and query.get('source_url') == ANNOUNCEMENT_SOURCE and
                query.get('url') == ANNOUNCEMENT_SOURCE and query.get('title_query') == query['keyword'] and
                query.get('window_start') == window and query.get('window_end') == moment.date().isoformat() and
                type(total) is int and type(scanned) is int and total >= 0 and scanned == total and
                type(pages) is int and pages == max(1, (total + 99) // 100) and pages <= 80 and
                stamp and begun <= stamp <= observed):
            return False
        receipts[query['keyword']] = stamp
    events, scanned = monitoring.get('risk_exclusions'), monitoring.get('items_scanned')
    if not (isinstance(events, list) and type(scanned) is int and scanned >= len(events)):
        return False
    for event in events:
        if not isinstance(event, dict):
            return False
        try:
            url = urlsplit(str(event.get('url', '')))
            published = _publication_boundary(event)
            event_observed = _instant(event.get('observed_at'))
            query = event.get('query_keyword')
            if not (re.fullmatch(r'sh(?:60\d{4}|68\d{4})', str(event.get('symbol', ''))) and
                    isinstance(event.get('title'), str) and event['title'] and query in receipts and query in event['title'] and
                    event.get('source') == NEWS_SOURCES[ANNOUNCEMENT_SOURCE][0] and
                    event.get('source_url') == ANNOUNCEMENT_SOURCE and url.scheme == 'https' and
                    url.hostname in NEWS_SOURCES[ANNOUNCEMENT_SOURCE][1] and not url.username and not url.password and
                    url.port in (None, 443) and published and event_observed == receipts[query] and
                    dt.datetime.combine(dt.date.fromisoformat(window), dt.time(), core.TZ) <= published <= event_observed and
                    event.get('observation_status') == 'research_window_veto' and event.get('resolution_status') == 'not_determined' and
                    event.get('observation_window_start') == window and event.get('observation_window_end') == moment.date().isoformat()):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _morning_ready(moment, target, research, news):
    date = moment.date().isoformat()
    start = dt.datetime.combine(moment.date(), dt.time(8), core.TZ)
    end = dt.datetime.combine(moment.date(), dt.time(9, 30), core.TZ)
    finished, fetched = _instant(research.get('finished_at')), _instant(news.get('as_of') or news.get('fetched_at'))
    if not (research.get('target_date') == target and finished and start <= finished < end and finished <= moment):
        return False
    if research.get('model_version') != core.forecast.MODEL_VERSION or research.get('chase_policy_version') != getattr(core.strategy, 'CHASE_POLICY_VERSION', 'anti-chase-v1'):
        return False
    if not (news.get('date') == date and news.get('status') == 'verified' and
            news.get('verification_protocol') == NEWS_VERIFICATION_PROTOCOL and fetched and start <= fetched < end and fetched <= moment):
        return False
    begun = _instant(news.get('collection_started_at'))
    try:
        window = dt.date.fromisoformat(news.get('window_start', ''))
        target_day = dt.date.fromisoformat(target)
    except (TypeError, ValueError):
        return False
    if not (begun and start <= begun <= fetched and news.get('window_end') == date and
            moment.date() - dt.timedelta(days=31) <= window <= min(target_day, moment.date() - dt.timedelta(days=4))):
        return False
    receipts = {}
    for source in news.get('sources', []) if isinstance(news.get('sources'), list) else []:
        if not isinstance(source, dict) or source.get('url') not in NEWS_SOURCES or source['url'] in receipts:
            return False
        expected_source, _ = NEWS_SOURCES[source['url']]
        observed = _instant(source.get('observed_at'))
        if not (source.get('source') == expected_source and source.get('status') == 'complete' and
                source.get('window_start') == window.isoformat() and source.get('window_end') == date and
                observed and begun <= observed <= fetched):
            return False
        receipts[source['url']] = observed
    if set(receipts) != set(NEWS_SOURCES):
        return False
    if not _risk_observation_ready(moment, begun, fetched, news.get('risk_monitoring')):
        return False
    session_risks = news.get('session_risks')
    if not isinstance(session_risks, dict):
        return False
    risk_time = _instant(session_risks.get('as_of'))
    risk_scope = session_risks.get('scope')
    if not (session_risks.get('status') == 'complete' and session_risks.get('session_date') == date and
            session_risks.get('source_url') == SESSION_RISK_SOURCE and
            isinstance(risk_scope, dict) and risk_scope.get('source_scope_complete') is True and
            isinstance(session_risks.get('records'), list) and session_risks['records'] and
            risk_time and begun <= risk_time <= fetched):
        return False
    items = news.get('items')
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict) or not item.get('title') or not item.get('published_at'):
            return False
        try:
            url = urlsplit(str(item.get('url', '')))
            published = _publication_boundary(item)
            source_url = item.get('source_url')
            observed = _instant(item.get('observed_at'))
            if source_url not in receipts:
                return False
            expected_source, hosts = NEWS_SOURCES[source_url]
            if (url.scheme != 'https' or url.hostname not in hosts or url.username or url.password or
                    url.port not in (None, 443) or item.get('source') != expected_source or not published or
                    not observed or observed != receipts[source_url] or
                    not dt.datetime.combine(window, dt.time(), core.TZ) <= published <= observed):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _ai_risk_review(moment, target, candidates, receipt):
    """An AI receipt can veto current qualified candidates, never create one."""
    failure='当日云端AI风险复核缺失、未完成或未覆盖全部合格候选，暂停新买入建议。'
    if not isinstance(receipt,dict) or receipt.get('valid') is not True or receipt.get('morning_qualified') is not True:
        return False,set(),failure
    review=receipt.get('review');checked=_instant(receipt.get('checked_at'))
    if not isinstance(review,dict) or not checked or checked>moment:
        return False,set(),failure
    started,completed=_instant(review.get('started_at')),_instant(review.get('completed_at'))
    start=dt.datetime.combine(moment.date(),dt.time(8),core.TZ)
    cutoff=start.replace(hour=9,minute=30)
    if not (receipt.get('protocol')==review.get('protocol')==AI_PROTOCOL and
            receipt.get('status')==review.get('status')=='complete' and
            receipt.get('date')==review.get('date')==moment.date().isoformat() and
            receipt.get('signal_date')==review.get('signal_date')==target and
            started and completed and start<=started<=completed<cutoff and completed<=checked<=moment):
        return False,set(),failure
    reviewed=review.get('reviewed_symbols');sources=review.get('sources');vetoes=review.get('risk_vetoes')
    if not (isinstance(reviewed,list) and len(reviewed)<=2400 and
            all(isinstance(s,str) and re.fullmatch(r'sh(?:60\d{4}|68\d{4})',s) for s in reviewed) and
            len(set(reviewed))==len(reviewed) and {s.get('symbol') for s in candidates}.issubset(set(reviewed)) and
            isinstance(sources,list) and len(sources)<=2400 and (not reviewed or sources) and
            isinstance(vetoes,list) and len(vetoes)<=2400):
        return False,set(),failure
    ids=set()
    try:
        run=urlsplit(str(review.get('run_url','')))
        if not (run.scheme=='https' and run.hostname=='chatgpt.com' and run.path not in ('','/') and
                not run.username and not run.password and not run.fragment and run.port in (None,443) and
                review.get('run_url_scope') in ('task_run','task_chat')):
            return False,set(),failure
        for source in sources:
            if not isinstance(source,dict):return False,set(),failure
            url=urlsplit(str(source.get('url','')));observed=_instant(source.get('observed_at'))
            published=dt.date.fromisoformat(source.get('published_date',''))
            scope='publisher_article_only' if url.hostname=='finance.cctv.com' else 'official_directory_title_only'
            sid=source.get('id')
            if not (isinstance(sid,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',sid) and sid not in ids and
                    url.scheme=='https' and url.hostname in {'www.sse.com.cn','static.sse.com.cn','query.sse.com.cn','finance.cctv.com'} and
                    url.port in (None,443) and not url.username and not url.password and not url.fragment and
                    source.get('scope')==scope and observed and started<=observed<=completed and published<=observed.date()):
                return False,set(),failure
            ids.add(sid)
        veto_symbols=set()
        for veto in vetoes:
            if not isinstance(veto,dict):return False,set(),failure
            symbol=veto.get('symbol');refs=veto.get('source_ids')
            if not (symbol in reviewed and symbol not in veto_symbols and isinstance(veto.get('reason'),str) and
                    veto['reason'].strip() and len(veto['reason'])<=500 and isinstance(refs,list) and refs and
                    all(isinstance(s,str) and s in ids for s in refs)):
                return False,set(),failure
            veto_symbols.add(symbol)
    except (ValueError,TypeError):
        return False,set(),failure
    return True,veto_symbols,''


def _sell_reference(old, latest, target):
    close = latest.get('raw_close') if latest and latest.get('date') == target else None
    take, stop = old.get('take_profit'), old.get('stop_loss')
    valid = _positive(close) and _positive(take)
    breached = valid and _positive(stop) and close <= stop
    ref = (close if breached else min(take, close * 1.015)) if valid else None
    return {'symbol': old['symbol'], 'name': old['name'], 'recommended_date': old['recommended_date'],
            'entry_low': old.get('entry_low'), 'entry_high': old.get('entry_high'),
            'sell_low': round(ref * .9975, 2) if ref else None, 'sell_high': round(ref * 1.0025, 2) if ref else None,
            'take_profit': take, 'stop_loss': stop, 'status': 'reference' if ref else 'data_unavailable',
            'reference_basis': '前一完整交易日未复权收盘价×1.015与原止盈价取较低者，再给±0.25%观察带；固定规则参考，不是09:00可成交报价预测。',
            'reasons': (['前收已低于原失效价，应优先复核风险，不等待盈利目标。'] if breached else []) + [
                '只对前一交易日已实际买入者适用；未买入则忽略。',
                '09:00尚未开始集合竞价，无法取得当日开盘成交价；卖出范围是前收数据的规则参考，不保证触价或成交。']}


def publish(date=None):
    """Called at/after 09:00; one publication per session, never retroactive buys."""
    moment = core.now().astimezone(core.TZ)
    date = date or moment.date().isoformat()
    if date != moment.date().isoformat() or moment.strftime('%H:%M') < '09:00':
        raise ValueError('只能在当日北京时间09:00之后发布')
    if not md.is_trading_day(date):
        return {'date':date,'status':'closed','buy':None,'sell':[], 'message':'上交所非交易日，无买卖计划'}
    existing = core.getkv('daily_plan:'+date)
    if existing and existing.get('status') == 'published':
        return existing
    # A late recovery keeps the morning's knowledge boundary even after today's close.
    target = md.last_completed_session(dt.datetime.combine(moment.date(), dt.time(9), core.TZ))
    allow_buy = moment.time().replace(tzinfo=None) < dt.time(9, 30)
    news = core.getkv('morning_news', {})
    news = news if isinstance(news, dict) else {}
    research = core.getkv('last_success', {})
    ready = _morning_ready(moment, target, research if isinstance(research, dict) else {}, news)
    research_status = 'success' if ready else 'unavailable'
    completed = core.getkv('last_completed', {})
    if not ready and isinstance(completed, dict) and completed.get('status') in ('success', 'partial'):
        total, ok = completed.get('total'), completed.get('ok')
        enough = _positive(total) and isinstance(ok, (int, float)) and not isinstance(ok, bool) and math.isfinite(ok) and 0 <= ok <= total and ok / total >= .99
        if enough and _morning_ready(moment, target, completed, news):
            ready, research_status = True, completed['status']
    dash = core.Service().dashboard(target)
    stocks = dash['stocks']
    coverage = dict(dash['meta']['coverage'])
    total, ok = coverage.get('total'), coverage.get('ok')
    complete = _positive(total) and type(ok) in (int, float) and math.isfinite(ok) and 0 <= ok <= total and ok / total >= .99
    # Invalid counts must not block yesterday's sell tracking through a JSON
    # serialization error, nor be accepted as evidence for a new buy.
    coverage = {key: None if type(value) is float and not math.isfinite(value) else value for key, value in coverage.items()}
    risk_exclusions = news.get('risk_exclusions', [])
    risk_exclusions = risk_exclusions if isinstance(risk_exclusions, list) else []
    session_risks = news.get('session_risks')
    if isinstance(session_risks, dict) and isinstance(session_risks.get('risk_exclusions'), list):
        risk_exclusions = risk_exclusions + session_risks['risk_exclusions']
    risk_monitoring = news.get('risk_monitoring')
    if isinstance(risk_monitoring, dict) and isinstance(risk_monitoring.get('risk_exclusions'), list):
        risk_exclusions = risk_exclusions + risk_monitoring['risk_exclusions']
    risks = {r.get('symbol') for r in risk_exclusions if isinstance(r, dict)}
    candidates = [s for s in stocks if s.get('date') == target and s.get('eligible') is True and
        not core.prediction_failures(s.get('prediction'), target) and not s.get('stale') and
        not core.chase_failures(s, target) and
        not s.get('excluded') and s.get('symbol') not in risks and
        all(_positive(s.get(key)) for key in ('entry_low', 'entry_high', 'stop_loss', 'take_profit', 'raw_close')) and
        s['stop_loss'] < s['entry_low'] <= s['entry_high'] < s['take_profit']]
    market_state = dash.get('meta', {}).get('main_wave_market')
    if isinstance(market_state, dict) and market_state.get('regime') != 'strong':
        candidates = []
    ai_receipt=core.getkv('ai_review',{})
    ai_ready,ai_vetoes,ai_warning=_ai_risk_review(moment,target,candidates,ai_receipt)
    candidates=[s for s in candidates if s.get('symbol') not in ai_vetoes]
    candidates.sort(key=lambda s:s['prediction'].get('rank_value',-1e9),reverse=True)
    fully_ready=ready and ai_ready
    pick = candidates[0] if allow_buy and fully_ready and complete and candidates else None
    sell_date = session_after(date) if allow_buy and fully_ready and complete else None
    buy = dict(pick,stock=pick,recommended_date=date,sell_date=sell_date) if pick else None
    if buy:
        ceiling = pick['chase_risk']['entry_ceiling']['price']
        buy['entry_rule'] = f'D1实际开盘价或下单前报价高于{ceiling:.2f}元时放弃，不追价；低于上限也须本人核对是否仍在原研究区间。09:00无法预先核验开盘价。'
    import forecast_audit
    qualified_symbols = {s.get('symbol') for s in candidates}
    research_forecasts = forecast_audit.snapshot(
        [dict(s, eligible=s.get('symbol') in qualified_symbols) for s in stocks
         if (s.get('prediction') or {}).get('model_version') == core.forecast.MODEL_VERSION],
        target, date, sell_date, moment.isoformat()) if sell_date else []
    prior = []
    with core.connect() as con:
        import json
        for row in con.execute("SELECT value FROM kv WHERE key LIKE 'daily_plan:%' ORDER BY key DESC LIMIT 10"):
            plan = json.loads(row[0]); old = plan.get('buy')
            if old and old.get('sell_date') == date:
                latest = next((s for s in stocks if s['symbol']==old['symbol']), None)
                # The original target/stop survive today's loss of model eligibility.
                prior.append(_sell_reference(old, latest, target))
    warnings = []
    if not complete:
        warnings.append('研究覆盖未达到有效99%门槛或数量记录无效，暂停新买入推荐。')
    if not allow_buy:
        warnings.append('本次生成已晚于09:30，不补造盘前买入建议；仅保留真实发布时间及前日建议跟踪。')
    if not ready:
        warnings.append('当日08:00行情任务或有来源的资讯核验未完成，暂停新买入推荐。')
    if not ai_ready:warnings.append(ai_warning)
    pending = allow_buy and not (fully_ready and complete)
    plan = {'date':date,'status':'pending' if pending else 'published',
            'attempted_at':moment.isoformat(),'published_at':None if pending else moment.isoformat(),'buy':buy,'sell':prior,
            'research_forecasts':research_forecasts,
            'news':news.get('items',[]),'news_status':news.get('status','unavailable'),'risk_exclusions':risk_exclusions,
            'news_verification': {key: news.get(key) for key in ('verification_protocol','verification_scope','window_start','window_end','items_scanned','items_displayed','sources','session_risks','risk_monitoring')},
            'ai_review':ai_receipt if isinstance(ai_receipt,dict) else {},'ai_review_ready':ai_ready,
            'timezone':'Asia/Shanghai','mode':'manual_research','data_date':target,'coverage':coverage,'morning_ready':fully_ready,'research_status':research_status,'late':not allow_buy,'warnings':warnings,
            'message':'手动决策；当日买入最早下一交易日卖出。' if buy else '已错过盘前发布窗口，今日不补造买入建议。' if not allow_buy else '盘前研究尚未就绪，等待补偿任务重试；暂不提供买入建议。' if pending else '没有同时通过数据、规则和预测验证的买入候选，今日不强行推荐。'}
    # Acquire the writer lock before measuring the actual record time. A slow
    # dashboard read or a busy SQLite writer must not retain an earlier clock.
    with core.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        saved = con.execute('SELECT value FROM kv WHERE key=?', ('daily_plan:'+date,)).fetchone()
        if saved:
            original = json.loads(saved[0])
            if isinstance(original, dict) and original.get('status') == 'published':
                return original
        recorded = core.now().astimezone(core.TZ)
        if recorded.date().isoformat() != date:
            raise ValueError('研究生成期间日期已变化，禁止补造前一日发布')
        if recorded.time().replace(tzinfo=None) >= dt.time(9, 30):
            plan.update(status='published', published_at=recorded.isoformat(), buy=None,
                        research_forecasts=[], late=True,
                        message='已错过盘前发布窗口，今日不补造买入建议。')
            if not any('晚于09:30' in warning for warning in plan['warnings']):
                plan['warnings'].append('实际记录生成已晚于09:30，不补造盘前买入建议；保留前日建议跟踪。')
        elif plan['status'] == 'published':
            plan['published_at'] = recorded.isoformat()
            for captured in plan['research_forecasts']:
                captured['published_at'] = recorded.isoformat()
        plan['recorded_at'] = recorded.isoformat()
        con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('daily_plan:'+date,core.encode(plan)))
    return plan
