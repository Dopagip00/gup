"""Immutable daily recommendation cohorts. References are never actual holdings."""
from __future__ import annotations
import datetime as dt
import math
from urllib.parse import urlsplit
import core
import market_data as md


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


def _morning_ready(moment, target, research, news):
    date = moment.date().isoformat()
    start = dt.datetime.combine(moment.date(), dt.time(8), core.TZ)
    end = dt.datetime.combine(moment.date(), dt.time(9, 30), core.TZ)
    finished, fetched = _instant(research.get('finished_at')), _instant(news.get('as_of') or news.get('fetched_at'))
    if not (research.get('target_date') == target and finished and start <= finished < end and finished <= moment):
        return False
    if not (news.get('date') == date and news.get('status') == 'verified' and fetched and start <= fetched < end and fetched <= moment):
        return False
    items = news.get('items')
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict) or not item.get('title') or not item.get('published_at'):
            return False
        try:
            url = urlsplit(str(item.get('url', '')))
            published = dt.datetime.fromisoformat(str(item['published_at']).replace('Z', '+00:00'))
            published = published.replace(tzinfo=core.TZ) if not published.tzinfo else published.astimezone(core.TZ)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or published > moment:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


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
    coverage = dash['meta']['coverage']
    complete = coverage['total'] > 0 and coverage['ok']/coverage['total'] >= .99
    risk_exclusions = news.get('risk_exclusions', [])
    risk_exclusions = risk_exclusions if isinstance(risk_exclusions, list) else []
    risks = {r.get('symbol') for r in risk_exclusions if isinstance(r, dict)}
    candidates = [s for s in stocks if s.get('date') == target and s.get('eligible') and s.get('prediction',{}).get('eligible') and not s.get('stale') and not s.get('excluded') and s.get('symbol') not in risks]
    candidates.sort(key=lambda s:s['prediction'].get('rank_value',-1e9),reverse=True)
    pick = candidates[0] if allow_buy and ready and complete and candidates else None
    buy = dict(pick,stock=pick,recommended_date=date,sell_date=session_after(date)) if pick else None
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
    if not allow_buy:
        warnings.append('本次生成已晚于09:30，不补造盘前买入建议；仅保留真实发布时间及前日建议跟踪。')
    if not ready:
        warnings.append('当日08:00行情任务或有来源的资讯核验未完成，暂停新买入推荐。')
    plan = {'date':date,'status':'published','published_at':moment.isoformat(),'buy':buy,'sell':prior,
            'news':news.get('items',[]),'news_status':news.get('status','unavailable'),'risk_exclusions':risk_exclusions,
            'timezone':'Asia/Shanghai','mode':'manual_research','data_date':target,'coverage':coverage,'morning_ready':ready,'research_status':research_status,'late':not allow_buy,'warnings':warnings,
            'message':'手动决策；当日买入最早下一交易日卖出。' if buy else '已错过盘前发布窗口，今日不补造买入建议。' if not allow_buy else '资讯或行情收集未完成，今日暂无可用买入建议。' if not ready else '没有同时通过数据、规则和预测验证的买入候选，今日不强行推荐。'}
    core.putkv('daily_plan:'+date,plan)
    return plan
