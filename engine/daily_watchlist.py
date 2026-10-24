"""Independent daily observation ranking; never a validated buy instruction.

Uses existing dated research only. Missing announcement/AI coverage is disclosed,
while known negative evidence always vetoes. No model flags, trades or original
daily recommendation records are changed by this module.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
from statistics import mean, median
from urllib.parse import urlsplit

import core
import daily_plan
import forecast
import market_data as md
import main_wave
import factor_screen

PROTOCOL = 'daily-observation-v1'
KEY_PREFIX = 'daily_watchlist:'
TECHNICAL_WEIGHT, FINANCIAL_WEIGHT = .60, .40
RANKING_POLICY = main_wave.POLICY
SHORT_CONFIRMATION_PROTOCOL = 'short-confirmation-v1'
SYMBOL = re.compile(r'sh(?:60[0135]|68[89])\d{3}')
PRICE_FIELDS = ('entry_low', 'entry_high', 'stop_loss', 'take_profit', 'max_position_pct')


def _number(value, *, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)
    except OverflowError:
        return False


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('榜单日期必须为YYYY-MM-DD')
    return dt.date.fromisoformat(value)


def _instant(value):
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(core.TZ) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def _moment(now):
    result = now if now is not None else core.now()
    if not isinstance(result, dt.datetime) or result.tzinfo is None:
        raise ValueError('观察榜必须使用真实带时区时间')
    return result.astimezone(core.TZ)


def _base(day, moment):
    release = dt.datetime.combine(day, dt.time(9), core.TZ)
    timing = 'prepared' if moment < release else 'on_time' if moment < release + dt.timedelta(minutes=30) else 'late'
    return {'protocol': PROTOCOL, 'date': day.isoformat(), 'data_date': None, 'signal_date': None,
            'generated_at': moment.isoformat(), 'release_at': release.isoformat(), 'published_at': None,
            'publication_timing': timing, 'late': timing == 'late', 'status': 'insufficient_data',
            'model_validated': False, 'actionable': False, 'eligible': False, 'items': [], 'top_pick': None,
            'alerts': [], 'withdrawn_symbols': [], 'coverage': {}, 'verification': {}, 'warnings': [],
            'ranking_policy': RANKING_POLICY,
            'method': '先核短线方向：当前两日点估计大于固定成本压力，收盘不低于MA5、五期涨幅为正且不弱于沪指；点估计仅作负向否决，不加分或提升模型资格。证据顺序仍为完整同日财务、历史已披露质量、财务缺失；固定60%风险调整技术分+40%财务分。',
            'scope': '仅供比较的优选观察榜，未通过短线模型验证，不提供入场、止损、止盈或仓位指令；排名不是上涨概率或收益保证。'}


def _known_risks(moment, target=None):
    news = core.getkv('morning_news', {})
    news = news if isinstance(news, dict) else {}
    ai = core.getkv('ai_review', {})
    ai = ai if isinstance(ai, dict) else {}
    vetoes = {}

    def add(events, label):
        if not isinstance(events, list):
            return
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get('symbol'), str) or not SYMBOL.fullmatch(event['symbol']):
                continue
            future = False
            for key in ('observed_at', 'published_at', 'completed_at'):
                value = event.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    try:
                        future = future or _day(value) > moment.date()
                    except ValueError:
                        future = True
                else:
                    stamp = _instant(value)
                    future = future or stamp is None or stamp > moment
            if future:
                continue
            # These are negative-only local collector records, even if the
            # rest of the collection was partial. No absence implies clearance.
            reason = event.get('reason') or event.get('title') or label
            reason = re.sub(r'[\x00-\x1f\x7f]', ' ', str(reason))[:300]
            vetoes.setdefault(event['symbol'], {'symbol': event['symbol'], 'reason': reason,
                                               'source': label, 'withdrawn': True})

    news_stamp = _instant(news.get('as_of') or news.get('fetched_at'))
    news_not_future = news_stamp is None or news_stamp <= moment
    if news_not_future:
        add(news.get('risk_exclusions'), '官方公告已知风险')
        for section, label in (('risk_monitoring', '官方31日风险观察'), ('session_risks', '官方停复牌风险')):
            part = news.get(section)
            if isinstance(part, dict):
                stamp = _instant(part.get('observed_at') or part.get('as_of'))
                if stamp is None or stamp <= moment:
                    add(part.get('risk_exclusions'), label)
    review = ai.get('review') if isinstance(ai.get('review'), dict) else {}
    checked, finished = _instant(ai.get('checked_at')), _instant(review.get('completed_at'))
    ai_known = (ai.get('valid') is True and (checked is None or checked <= moment)
                and (finished is None or finished <= moment))
    if ai_known:
        add(review.get('risk_vetoes'), '云端AI已知负向复核')
    requested = moment.date().isoformat()
    begun = _instant(news.get('collection_started_at'))
    monitoring = news.get('risk_monitoring')
    announcements = bool(news.get('date') == requested and news.get('status') == 'verified'
                         and news.get('verification_protocol') == daily_plan.NEWS_VERIFICATION_PROTOCOL
                         and begun and news_stamp and begun <= news_stamp <= moment
                         and daily_plan._risk_observation_ready(moment, begun, news_stamp, monitoring))
    session = news.get('session_risks') if isinstance(news.get('session_risks'), dict) else {}
    session_stamp = _instant(session.get('as_of'))
    session_ready = bool(session.get('session_date') == requested and session.get('status') == 'complete'
                         and session.get('source_url') == daily_plan.SESSION_RISK_SOURCE
                         and isinstance(session.get('scope'), dict) and session['scope'].get('source_scope_complete') is True
                         and session_stamp and session_stamp <= moment)
    ai_complete = bool(ai_known and ai.get('morning_qualified') is True
                       and ai.get('status') == review.get('status') == 'complete'
                       and ai.get('protocol') == review.get('protocol') == daily_plan.AI_PROTOCOL
                       and ai.get('date') == review.get('date') == requested
                       and target and ai.get('signal_date') == review.get('signal_date') == target
                       and isinstance(review.get('reviewed_symbols'), list)
                       and checked and finished and finished <= checked <= moment)
    verification = {'announcements_status': 'verified' if announcements else 'unverified',
                    'session_status': 'verified' if session_ready else 'unverified',
                    'ai_status': 'reviewed' if ai_complete else 'unverified',
                    'known_risk_veto_count': len(vetoes), 'evidence_cutoff': moment.isoformat()}
    return vetoes, verification


def _financial(record, snapshot, target, moment):
    if not factor_screen.financial_dates_valid(record, snapshot, target, moment):
        return False
    if not isinstance(record, dict) or snapshot.get('as_of') != target or record.get('date') != target or record.get('quote_date') != target:
        return False
    fetched = _instant(snapshot.get('fetched_at'))
    if not fetched or fetched > moment or fetched.date() < _day(target):
        return False
    fields = ('score', 'pe_ttm', 'pb', 'roe', 'revenue_growth', 'profit_growth',
              'operating_cash_to_profit', 'recurring_profit_ratio', 'debt_ratio', 'ttm_parent_netprofit_100m')
    if any(not _number(record.get(key)) for key in fields):
        return False
    if not (0 <= record['score'] <= 100 and record['pe_ttm'] > 0 and record['pb'] > 0
            and record['ttm_parent_netprofit_100m'] > 0 and 0 <= record['debt_ratio'] <= 100):
        return False
    try:
        report, published, updated = [_day(record.get(key)) for key in ('report_date', 'publication_date', 'provider_update_date')]
        return report <= published <= _day(target) and report <= updated <= _day(target)
    except (TypeError, ValueError):
        return False


def _disclosed_quality(record, snapshot, target, moment):
    """Reuse disclosed operating facts, never stale valuation or total score."""
    if not factor_screen.financial_dates_valid(record, snapshot, target, moment):
        return None
    if not isinstance(record, dict) or not isinstance(snapshot, dict):
        return None
    try:
        day, original = _day(target), _day(snapshot.get('as_of'))
        fetched = _instant(snapshot.get('fetched_at'))
        if (record.get('date') != original.isoformat() or record.get('quote_date') != original.isoformat()
                or original > day or not fetched or fetched > moment or fetched.date() < original):
            return None
        report, published, updated = [_day(record.get(key)) for key in ('report_date','publication_date','provider_update_date')]
        annual, annual_published = [_day(record.get(key)) for key in ('roe_report_date','roe_publication_date')]
        if (not report <= published <= original <= day or not report <= updated <= original
                or not annual <= annual_published <= original or not annual.isoformat().endswith('12-31')
                or (day-report).days>200):
            return None
    except (TypeError, ValueError):
        return None
    fields=('roe','revenue_growth','profit_growth','operating_cash_to_profit','recurring_profit_ratio','debt_ratio','ttm_parent_netprofit_100m')
    if any(not _number(record.get(key)) for key in fields) or record['ttm_parent_netprofit_100m']<=0 or not 0<=record['debt_ratio']<=100:
        return None
    quality=max(0,min(15,record['roe']))+max(0,min(10,record['operating_cash_to_profit']*10))
    quality+=10 if record['recurring_profit_ratio']>=.8 else 6 if record['recurring_profit_ratio']>=.6 else 0
    growth=max(0,min(15,record['revenue_growth']*.75))+max(0,min(15,record['profit_growth']*.5))
    # Retain a provider-derived low-base warning instead of extrapolating its growth.
    risks=record.get('risks') if isinstance(record.get('risks'),list) else []
    if any('低基数' in str(value) or '同期利润基数为零或亏损' in str(value) for value in risks):
        growth=min(growth,10)
    penalty=5 if record['debt_ratio']>=65 else 0
    return round(max(0,min(100,(quality+growth-penalty)/65*100)),3)


def _risk_penalty(atr, downside, drawdown, liquidity, regime):
    multiplier=1.5 if regime=='defensive' else 1.0
    return round(min(30,2*atr+3*downside*multiplier+.25*drawdown+10*(1-min(1,liquidity/20_000_000))),3)


def _risk_profile(bars, benchmark, target):
    """Fixed backward-only risk budget; no fitted return parameters."""
    def window(rows):
        if not isinstance(rows,list):return None
        selected=[row for row in rows if isinstance(row,dict) and isinstance(row.get('date'),str) and row['date']<=target]
        dates=[row['date'] for row in selected]
        if dates!=sorted(set(dates)) or len(selected)<61 or dates[-1]!=target:return None
        try:
            for day in dates:_day(day)
        except (TypeError,ValueError):return None
        return selected[-61:]
    rows, index=window(bars),window(benchmark)
    if rows is None or index is None or [row['date'] for row in rows]!=[row['date'] for row in index]:return None
    for row in [*rows,*index]:
        if any(not _number(row.get(key),positive=True) for key in ('open','high','low','close')) or not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
            return None
    if any(not _number(row.get('volume'),positive=True) or not _number(row.get('raw_close'),positive=True) for row in rows[-20:]):return None
    returns=[(right['close']/left['close']-1)*100 for left,right in zip(rows,rows[1:])]
    downside=math.sqrt(mean(min(0,value)**2 for value in returns))
    peak=rows[0]['close'];drawdown=0
    for row in rows:
        peak=max(peak,row['close']);drawdown=max(drawdown,(1-row['close']/peak)*100)
    ranges=[max(row['high']-row['low'],abs(row['high']-previous['close']),abs(row['low']-previous['close'])) for previous,row in zip(rows,rows[1:])]
    atr=mean(ranges[-14:])/rows[-1]['close']*100
    liquidity=median(row['raw_close']*row['volume']*100 for row in rows[-20:])
    regime='defensive' if index[-1]['close']<mean(row['close'] for row in index[-60:]) and index[-1]['close']<index[-21]['close'] else 'normal'
    atr,downside,drawdown,liquidity=[round(value,3) for value in (atr,downside,drawdown,liquidity)]
    return {'atr_pct':atr,'downside_deviation_60d_pct':downside,'max_drawdown_60d_pct':drawdown,
            'liquidity_proxy_20d_cny':liquidity,'market_regime':regime,
            'risk_penalty':_risk_penalty(atr,downside,drawdown,liquidity,regime),
            'window_start':rows[0]['date'],'window_end':target,'return_samples':60,
            'reference_close':rows[-1]['close'],'reference_raw_close':rows[-1]['raw_close']}


def _short_window(bars, target):
    if not isinstance(bars, list):
        return None
    rows = [row for row in bars if isinstance(row, dict) and isinstance(row.get('date'), str) and row['date'] <= target]
    dates = [row['date'] for row in rows]
    if len(rows) < 6 or dates != sorted(set(dates)) or dates[-1] != target:
        return None
    rows = rows[-6:]
    try:
        for row in rows:
            _day(row['date'])
    except (TypeError, ValueError):
        return None
    if any(not _number(row.get('close'), positive=True) for row in rows):
        return None
    return rows


def _short_values(dates, stock_closes, benchmark_closes, target):
    """Recompute the frozen six-session confirmation, including calendar gaps."""
    if (not isinstance(dates, list) or not isinstance(stock_closes, list) or not isinstance(benchmark_closes, list)
            or len(dates) != 6 or len(stock_closes) != 6 or len(benchmark_closes) != 6
            or any(not isinstance(day, str) for day in dates) or dates != sorted(set(dates))
            or dates[-1] != target
            or any(not _number(value, positive=True) for value in stock_closes + benchmark_closes)):
        return None
    try:
        for day in dates:
            if _day(day) > _day(target) or not md.is_trading_day(day):
                return None
        for previous, following in zip(dates, dates[1:]):
            prior = md.last_completed_session(dt.datetime.combine(_day(following), dt.time(9), core.TZ))
            prior = prior.isoformat() if isinstance(prior, dt.date) else str(prior)
            if previous != prior:
                return None
    except (md.MarketDataError, TypeError, ValueError):
        return None
    ma5 = mean(stock_closes[-5:])
    stock_return = (stock_closes[-1] / stock_closes[0] - 1) * 100
    benchmark_return = (benchmark_closes[-1] / benchmark_closes[0] - 1) * 100
    values = {'ma5': ma5, 'stock_return_5d_pct': stock_return,
              'benchmark_return_5d_pct': benchmark_return,
              'relative_return_5d_pct': stock_return - benchmark_return}
    return values if all(_number(value) for value in values.values()) else None


def _short_confirmation(stock, bars, benchmark, target):
    """A negative-only consistency veto; its estimate never enters the score."""
    prediction = stock.get('prediction') if isinstance(stock, dict) else None
    if (not isinstance(prediction, dict) or prediction.get('available') is not True
            or prediction.get('model_version') != forecast.MODEL_VERSION
            or prediction.get('validation_protocol') != forecast.VALIDATION_PROTOCOL
            or prediction.get('target_kind') != 'next_open_to_following_close'
            or prediction.get('as_of_date') != target
            or not _number(prediction.get('expected_return_pct'))):
        return None, 'short_model_evidence_missing'
    if prediction['expected_return_pct'] <= forecast.ROUND_TRIP_COST_STRESS_PCT:
        return None, 'short_model_not_above_cost'
    stock_rows, index_rows = _short_window(bars, target), _short_window(benchmark, target)
    if stock_rows is None or index_rows is None:
        return None, 'short_price_evidence_missing'
    dates = [row['date'] for row in stock_rows]
    if dates != [row['date'] for row in index_rows]:
        return None, 'short_price_evidence_missing'
    closes, index_closes = [row['close'] for row in stock_rows], [row['close'] for row in index_rows]
    values = _short_values(dates, closes, index_closes, target)
    if values is None:
        return None, 'short_price_evidence_missing'
    if closes[-1] < values['ma5']:
        return None, 'short_below_ma5'
    if values['stock_return_5d_pct'] <= 0:
        return None, 'short_nonpositive_return_5d'
    if values['relative_return_5d_pct'] < 0:
        return None, 'short_relative_weakness'
    return {'protocol': SHORT_CONFIRMATION_PROTOCOL, 'model_version': forecast.MODEL_VERSION,
            'validation_protocol': forecast.VALIDATION_PROTOCOL, 'target_kind': 'next_open_to_following_close',
            'as_of_date': target, 'expected_return_pct': prediction['expected_return_pct'],
            'round_trip_cost_stress_pct': forecast.ROUND_TRIP_COST_STRESS_PCT,
            'session_dates': dates, 'stock_closes': closes, 'benchmark_closes': index_closes, **values}, None


def _valid_short_confirmation(evidence, target):
    keys = {'protocol', 'model_version', 'validation_protocol', 'target_kind', 'as_of_date',
            'expected_return_pct', 'round_trip_cost_stress_pct', 'session_dates', 'stock_closes',
            'benchmark_closes', 'ma5', 'stock_return_5d_pct', 'benchmark_return_5d_pct', 'relative_return_5d_pct'}
    if (not isinstance(evidence, dict) or set(evidence) != keys
            or evidence.get('protocol') != SHORT_CONFIRMATION_PROTOCOL
            or evidence.get('model_version') != forecast.MODEL_VERSION
            or evidence.get('validation_protocol') != forecast.VALIDATION_PROTOCOL
            or evidence.get('target_kind') != 'next_open_to_following_close'
            or evidence.get('as_of_date') != target
            or not _number(evidence.get('expected_return_pct'))
            or not _number(evidence.get('round_trip_cost_stress_pct'))
            or evidence['round_trip_cost_stress_pct'] != forecast.ROUND_TRIP_COST_STRESS_PCT
            or evidence['expected_return_pct'] <= forecast.ROUND_TRIP_COST_STRESS_PCT):
        return False
    values = _short_values(evidence.get('session_dates'), evidence.get('stock_closes'), evidence.get('benchmark_closes'), target)
    return bool(values is not None and all(_number(evidence.get(key)) and abs(evidence[key] - value) <= 1e-9 for key, value in values.items())
                and evidence['stock_closes'][-1] >= values['ma5']
                and values['stock_return_5d_pct'] > 0 and values['relative_return_5d_pct'] >= 0)


def observation_quote_evidence(stock, quote, snapshot, target, moment, *, observation_only=False, defer_auction=False):
    """Price identity evidence for a non-actionable observation, never a buy gate.

    A next-session quote can corroborate the previous raw close after 09:30;
    its live price and valuation must never be relabelled as the prior close.
    """
    if not isinstance(stock, dict) or not isinstance(quote, dict) or not isinstance(snapshot, dict):
        return None
    fetched, stamp = _instant(snapshot.get('fetched_at')), _instant(quote.get('datetime'))
    source_stamp = _instant(stock.get('fetched_at'))
    try:
        target_day = _day(target)
    except (TypeError, ValueError):
        return None
    if (not fetched or not stamp or not source_stamp or fetched > moment or stamp > moment or source_stamp > moment
            or source_stamp.date() < target_day or fetched.date() < target_day
            or stock.get('date') != target or quote.get('symbol') != stock.get('symbol')
            or quote.get('is_st') is not False or quote.get('is_delisting') is not False or quote.get('suspended') is not False
            or 'ST' in str(quote.get('name', '')).upper() or '退' in str(quote.get('name', ''))
            or core.exclusion(stock.get('symbol', ''), stock.get('name', ''), quote)
            or any(not _number(value, positive=True) for value in (quote.get('market_cap_100m'), quote.get('price'), stock.get('raw_close')))):
        return None
    basis = 'same_session_raw_close'
    if quote.get('date') == target and stamp.date() == target_day and snapshot.get('as_of') == target:
        if core.quote_gate(stock, quote, target).get('quote_valid') is not True:
            return None
    else:
        today = moment.date().isoformat()
        row_fetched = _instant(quote.get('fetched_at'))
        auction = moment.time().replace(tzinfo=None) < dt.time(9, 30)
        if (observation_only is not True or auction and defer_auction is not True
                or quote.get('date') != today or stamp.date() != moment.date()
                or not row_fetched or not stamp <= row_fetched <= moment or row_fetched.date() != moment.date()
                or fetched.date() != moment.date() or snapshot.get('as_of') not in (target, today)
                or not _number(quote.get('previous_close'), positive=True)):
            return None
        try:
            source = urlsplit(str(quote.get('source', '')))
            if (source.scheme != 'https' or source.hostname != 'qt.gtimg.cn' or source.username or source.password
                    or source.port not in (None, 443) or source.fragment):
                return None
            previous = md.last_completed_session(dt.datetime.combine(moment.date(), dt.time(9), core.TZ))
            previous = previous.isoformat() if isinstance(previous, dt.date) else str(previous)
            if not md.is_trading_day(moment.date()) or previous != target or target_day >= moment.date():
                return None
        except (md.MarketDataError, TypeError, ValueError):
            return None
        if abs(quote['previous_close'] - stock['raw_close']) > max(.02, stock['raw_close'] * .001):
            return None
        basis = 'auction_previous_close_unverified' if auction else 'next_session_previous_close'
    return {'quote_basis': basis, 'quote_date': quote['date'], 'price_reference_date': target,
            'quote_consistent': basis != 'auction_previous_close_unverified', 'quote_source': quote.get('source'), 'quote_datetime': stamp.isoformat(),
            'quote_fetched_at': (row_fetched if basis != 'same_session_raw_close' else fetched).isoformat(),
            'previous_close': quote.get('previous_close') if basis != 'same_session_raw_close' else None}


def build(date=None, service=None, now=None, limit=5):
    """Build today's rank or the next official session's genuine preparation.

    Past dates are never reconstructed with current valuations/news. They can
    only be read from this module's independently saved dated observations.
    """
    moment = _moment(now)
    day = _day(date) if date is not None else moment.date()
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('观察榜数量须为1至10，默认5')
    result = _base(day, moment)
    if day < moment.date():
        saved = core.getkv(KEY_PREFIX + day.isoformat())
        if isinstance(saved, dict) and saved.get('protocol') == PROTOCOL and saved.get('date') == day.isoformat():
            return _with_alerts(saved, moment)
        result.update(status='historical_unavailable', warnings=['没有当时真实保存的观察榜；不使用今天的数据回填历史。'])
        return result
    try:
        if not md.is_trading_day(day):
            result.update(status='closed', warnings=['上交所官方日历休市，无当日观察榜。'])
            return result
        if day > moment.date() and day.isoformat() != daily_plan.session_after(moment.date().isoformat()):
            result['warnings'] = ['只允许为官方下一交易日准备，不能构造任意未来榜单。']
            return result
        release = dt.datetime.combine(day, dt.time(9), core.TZ)
        target = md.last_completed_session(release)
        target = target.isoformat() if isinstance(target, dt.date) else str(target)
        latest = md.last_completed_session(moment)
        latest = latest.isoformat() if isinstance(latest, dt.date) else str(latest)
        result.update(data_date=target, signal_date=target)
        if target > latest:
            result['warnings'] = ['下一交易日对应的收盘行情尚未完成，不提前生成未来数据。']
            return result
    except (md.MarketDataError, ValueError):
        result['warnings'] = ['官方交易日历不能核验，观察榜暂停。']
        return result
    vetoes, verification = _known_risks(moment, target)
    # A next-session preparation has not yet had that session's morning review.
    if day != moment.date():
        verification.update(announcements_status='unverified', session_status='unverified', ai_status='unverified')
    result['verification'] = verification
    if verification['announcements_status'] != 'verified' or verification['session_status'] != 'verified':
        result['warnings'].append('当日公告/停复牌覆盖尚未完整核验；本榜仅观察，未发现风险不代表不存在风险。')
    result['warnings'].append('公告核验范围为官方目录与已记录事件；未逐份阅读全部公告PDF，观察榜不代表公司风险已经排清。')
    if verification['ai_status'] != 'reviewed':
        result['warnings'].append('当日云端AI复核未完整提供；已知负向证据仍会排除，AI不能把观察榜变为合格买入。')
    try:
        service = service or core.Service()
        dashboard = service.dashboard(target)
        financial = service.longterm()
        quotes = core.getkv('valuation_quotes', {})
    except Exception as exc:
        result['warnings'].append('已有研究读取失败：' + type(exc).__name__)
        return result
    if not isinstance(dashboard, dict) or not isinstance(financial, dict) or not isinstance(quotes, dict):
        result['warnings'].append('已有研究格式不完整，不能计算观察榜。')
        return result
    stocks = dashboard.get('stocks')
    index = dashboard.get('index')
    quote_rows = quotes.get('quotes')
    quote_stamp = _instant(quotes.get('fetched_at'))
    if (not isinstance(stocks, list) or not isinstance(index, dict) or index.get('date') != target
            or quotes.get('as_of') not in (target, moment.date().isoformat()) or not isinstance(quote_rows, dict)
            or not quote_stamp or quote_stamp > moment or quote_stamp.date() < _day(target)):
        result['warnings'].append('行情基准或同日估值未达到本榜信号日，暂停观察榜；不将旧数据改标今日。')
        return result
    financial_stocks = financial.get('stocks')
    financial_stocks = financial_stocks if isinstance(financial_stocks, list) else []
    finance_rows, repeated_financial = {}, set()
    for item in financial_stocks:
        if not isinstance(item, dict) or not isinstance(item.get('symbol'), str):
            continue
        symbol = item['symbol']
        if symbol in finance_rows:
            repeated_financial.add(symbol)
        finance_rows[symbol] = item
    for symbol in repeated_financial:
        finance_rows.pop(symbol, None)
    try:
        benchmark = core.load_history('sh000001').get('bars')
    except Exception:
        benchmark = None
    wave_market = main_wave.market(benchmark, target)
    sector_context = main_wave.sectors(stocks, core.getkv('universe_info', {}), target, moment)
    if RANKING_POLICY == main_wave.POLICY:
        result['main_wave_market'] = wave_market
        result['method'] = '主升浪筛选：强市、均线多头、放量突破或缩量回踩、官方行业共振与相对领涨；保留原成本、追高和财务核验。'
        if wave_market['regime'] in ('weak', 'range'):
            result.update(status='cash', message='空仓等待：' + wave_market['reason'],
                          coverage={'universe': len(stocks), 'selected': 0})
            if result['publication_timing'] != 'prepared':
                result['published_at'] = moment.isoformat()
            return result
    ai = core.getkv('ai_review', {})
    review = ai.get('review', {}) if isinstance(ai, dict) else {}
    reviewed = review.get('reviewed_symbols', []) if isinstance(review, dict) else []
    reviewed = {value for value in reviewed if isinstance(value, str)} if isinstance(reviewed, list) else set()
    counts, pool, seen = {}, [], set()
    symbol_counts = {}
    for stock in stocks:
        if isinstance(stock, dict) and isinstance(stock.get('symbol'), str):
            symbol_counts[stock['symbol']] = symbol_counts.get(stock['symbol'], 0) + 1

    def exclude(reason):
        counts[reason] = counts.get(reason, 0) + 1

    for stock in stocks:
        if not isinstance(stock, dict):
            exclude('invalid_record'); continue
        symbol, name = stock.get('symbol'), stock.get('name')
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in seen or symbol_counts.get(symbol) != 1:
            exclude('invalid_or_duplicate_symbol'); continue
        seen.add(symbol)
        quote = quote_rows.get(symbol)
        if (stock.get('date') != target or stock.get('stale') is not False or not isinstance(name, str) or not name.strip()
                or not isinstance(quote, dict)):
            exclude('stale_or_missing'); continue
        if ('ST' in name.upper() or '退' in name or 'ST' in str(quote.get('name', '')).upper() or '退' in str(quote.get('name', ''))
                or quote.get('is_st') is not False or quote.get('is_delisting') is not False):
            exclude('st_or_delisting'); continue
        if stock.get('excluded') is True or core.exclusion(symbol, name, quote):
            exclude('preference_exclusion'); continue
        if symbol in vetoes:
            exclude('known_risk_veto'); continue
        quote_evidence = observation_quote_evidence(stock, quote, quotes, target, moment, observation_only=True)
        if quote_evidence is None:
            exclude('unverified_quote'); continue
        risk = stock.get('chase_risk') if isinstance(stock.get('chase_risk'), dict) else {}
        metrics = risk.get('metrics') if isinstance(risk.get('metrics'), dict) else {}
        ceiling = risk.get('entry_ceiling') if isinstance(risk.get('entry_ceiling'), dict) else {}
        if (risk.get('version') != core.strategy.CHASE_POLICY_VERSION or risk.get('as_of_date') != target
                or risk.get('blocked') is not False or risk.get('gate_passed') is not True
                or metrics.get('raw_price_complete') is not True or metrics.get('benchmark_aligned') is not True
                or ceiling.get('reference_date') != target or ceiling.get('price_basis') != 'latest_raw_close_scaled_ma20'
                or not _number(ceiling.get('price'), positive=True)):
            exclude('chase_or_integrity'); continue
        if (type(stock.get('bar_count')) is not int or stock['bar_count'] < 120
                or not _number(stock.get('score')) or not 0 <= stock['score'] <= 100
                or not _number(stock.get('atr14'), positive=True) or not _number(stock.get('close'), positive=True)
                or any(not _number(metrics.get(key)) for key in ('return_5d_pct', 'ma20_deviation_pct'))):
            exclude('technical_evidence_missing'); continue
        record = finance_rows.get(symbol, {})
        complete = quote_evidence['quote_basis'] == 'same_session_raw_close' and _financial(record, financial, target, moment)
        quality = _disclosed_quality(record, financial, target, moment) if not complete else None
        financial_score = record['score'] if complete else quality
        financial_basis = ('same_session_complete' if complete else
                           'cached_disclosed_quality_without_valuation' if quality is not None else 'missing')
        try:
            bars = core.load_history(symbol).get('bars')
            profile = _risk_profile(bars, benchmark, target)
        except Exception:
            profile = None
        if (profile is None or abs(profile['reference_close']-stock['close'])>.0001
                or abs(profile['reference_raw_close']-stock['raw_close'])>.0001):
            exclude('risk_history_missing'); continue
        confirmation, short_failure = _short_confirmation(stock, bars, benchmark, target)
        if confirmation is None:
            exclude(short_failure); continue
        wave = None
        if RANKING_POLICY == main_wave.POLICY:
            wave = main_wave.evidence(bars, benchmark, target, sector_context.get(symbol))
            if not wave['passed']:
                exclude('main_wave_' + wave['failure']); continue
        technical = round(max(0, stock['score'] - profile['risk_penalty']), 3)
        score = TECHNICAL_WEIGHT * technical + (FINANCIAL_WEIGHT * financial_score if financial_score is not None else 0)
        risk_reasons = risk.get('reasons') if isinstance(risk.get('reasons'), list) else []
        risk_notes = [str(item.get('detail'))[:300] for item in risk_reasons if isinstance(item, dict) and item.get('detail')]
        risk_notes.append('短线模型验证未作为本观察榜的合格买入依据；相对分数不是预期收益或上涨概率。')
        risk_notes.append('固定风险预算未经盈利验证；20日流动性为原价收盘×成交手数×100的代理，不等于真实可成交金额或盘口深度。')
        if quote_evidence['quote_basis'] == 'next_session_previous_close':
            risk_notes.append('盘中迟到观察恢复：今日报价的昨收仅佐证上一交易日原价收盘，不是该日同日估值；旧PE/PB不会改标此前日期。')
        if quality is not None:
            risk_notes.append(f"历史已披露经营质量截至{financial['as_of']}，重新计算质量/增长/现金流/杠杆分，不使用旧PE/PB或旧总分；当前估值待核验。")
        elif not complete:
            risk_notes.append('同日完整财务证据不足，仅计技术部分，不填补财务分；置信度较低。')
        elif record.get('eligible') is not True:
            financial_risks = record.get('risks') if isinstance(record.get('risks'), list) else []
            risk_notes.append('财务证据完整与长线筛选合格不同；该股未通过原长线筛选。')
            risk_notes.extend(str(value)[:300] for value in financial_risks[:12])
            risk_notes.append(f"年度ROE{record['roe']:.2f}%，资产负债率{record['debt_ratio']:.2f}%；营收/归母净利同比{record['revenue_growth']:+.2f}%/{record['profit_growth']:+.2f}%。")
        reasons = [f"原技术分{stock['score']:.1f}，固定风险扣分{profile['risk_penalty']:.2f}后{technical:.2f}；近5日{metrics['return_5d_pct']:+.2f}%，偏离MA20{metrics['ma20_deviation_pct']:+.2f}%。",
                   f"60日下行波动{profile['downside_deviation_60d_pct']:.2f}%，最大回撤{profile['max_drawdown_60d_pct']:.2f}%；市场状态{'防御' if profile['market_regime']=='defensive' else '正常'}。"]
        reasons.append(f"收盘不低于5日均线，近5期{confirmation['stock_return_5d_pct']:+.2f}%，较沪指{confirmation['relative_return_5d_pct']:+.2f}个百分点。")
        if complete:
            reasons.append(f"已披露财务分{record['score']:.1f}；PE(TTM){record['pe_ttm']:.2f}，ROE{record['roe']:.2f}%。")
            reasons.append(f"营收/归母净利同比{record['revenue_growth']:+.2f}%/{record['profit_growth']:+.2f}%，经营现金流/净利{record['operating_cash_to_profit']:.2f}。")
        elif quality is not None:
            reasons.append(f"已披露质量分{quality:.1f}；年度ROE{record['roe']:.2f}%，经营现金流/净利{record['operating_cash_to_profit']:.2f}，资产负债率{record['debt_ratio']:.2f}%；估值待核验。")
            financial_risks = record.get('risks') if isinstance(record.get('risks'), list) else []
            risk_notes.extend(str(value)[:300] for value in financial_risks[:12])
        per_verification = dict(verification, **quote_evidence, price_history_complete=True,
                                chase_passed=True, financial_complete=complete,
                                financial_quality_available=financial_score is not None,
                                financial_evidence_basis=financial_basis,
                                financial_data_date=financial.get('as_of') if financial_score is not None else None,
                                financial_valuation_date=target if complete else None,
                                financial_report_date=record.get('report_date') if financial_score is not None else None,
                                financial_publication_date=record.get('publication_date') if financial_score is not None else None,
                                financial_provider_update_date=record.get('provider_update_date') if financial_score is not None else None,
                                financial_roe_report_date=record.get('roe_report_date') if financial_score is not None else None,
                                financial_roe_publication_date=record.get('roe_publication_date') if financial_score is not None else None,
                                risk_window_start=profile['window_start'], risk_window_end=target, risk_return_samples=60,
                                model_validation_status='not_validated')
        per_verification['ai_reviewed'] = verification['ai_status'] == 'reviewed' and symbol in reviewed
        per_verification['ai_status'] = 'reviewed' if per_verification['ai_reviewed'] else 'unverified'
        item = {'symbol': symbol, 'name': name, 'asof': target, 'score': round(score, 3),
                'components': {'technical': technical, 'technical_raw': stock['score'],
                               'financial': financial_score, 'risk_penalty': profile['risk_penalty'],
                               'weights': {'technical': TECHNICAL_WEIGHT, 'financial': FINANCIAL_WEIGHT},
                               **{key: profile[key] for key in ('atr_pct','downside_deviation_60d_pct','max_drawdown_60d_pct','liquidity_proxy_20d_cny','market_regime')},
                               'return_5d_pct': metrics['return_5d_pct'],
                               'ma20_deviation_pct': metrics['ma20_deviation_pct']},
                'reasons': reasons, 'risk_notes': list(dict.fromkeys(risk_notes)), 'verification': per_verification,
                'short_confirmation': confirmation,
                'model_validated': False, 'actionable': False, 'eligible': False,
                **dict.fromkeys(PRICE_FIELDS)}
        pool.append(item)
        if wave is not None:
            item['main_wave'] = wave
    full = [item for item in pool if item['verification']['financial_complete']]
    quality_pool = [item for item in pool if item['verification']['financial_quality_available'] and not item['verification']['financial_complete']]
    selected_pool = pool
    selected_pool.sort(key=lambda item: (0 if item['verification']['financial_complete'] else 1 if item['verification']['financial_quality_available'] else 2, -item['score'],
                                         item['components']['atr_pct'], item['symbol']))
    result['coverage'] = {'universe': len(stocks), 'price_and_known_risk_pool': len(pool),
                          'complete_financial': len(full), 'disclosed_quality_financial': len(quality_pool),
                          'missing_financial': len(pool)-len(full)-len(quality_pool),
                          'selected': min(limit, len(selected_pool)), 'excluded_counts': counts,
                          'financial_pool_used': 'complete' if len(full) >= limit else 'disclosed_quality' if len(full)+len(quality_pool)>=limit else 'partial'}
    result['verification']['financial_status'] = 'complete' if len(full) >= limit else 'partial'
    result['items'] = selected_pool[:limit]
    result['top_pick'] = result['items'][0] if result['items'] else None
    if any(item['verification']['quote_basis'] == 'next_session_previous_close' for item in result['items']):
        result['warnings'].append('盘中迟到恢复观察榜；真实当日报价以昨收佐证此前完整日线，保留实际报价日期与时间，未冒充此前同日估值或09:00发布。历史已披露质量与当前估值分别标记。')
    ai_count = sum(item['verification']['ai_reviewed'] for item in result['items'])
    result['verification']['ai_status'] = ('reviewed' if ai_count and ai_count == len(result['items'])
                                           else 'partial' if ai_count else 'unverified')
    if result['items'] and ai_count != len(result['items']):
        result['warnings'].append('当日AI完整复核未覆盖本榜全部标的；未覆盖项按未复核展示。')
    if result['items']:
        result['status'] = 'watch_only'
        if result['publication_timing'] != 'prepared':
            result['published_at'] = moment.isoformat()
    else:
        result['warnings'].append('既有研究中没有通过必要价格、名称和风险证据检查的观察标的，不拼造候选。')
        if RANKING_POLICY == main_wave.POLICY:
            result['message'] = ('空仓等待：' + wave_market['reason'] if wave_market['regime'] in ('weak', 'range')
                                 else '空仓等待：主升浪或行业共振证据不足')
    return result


def _merge_alerts(result, *records):
    symbols = {item.get('symbol') for item in result.get('items', []) if isinstance(item, dict)}
    alerts, seen = [], set()
    for record in (result, *records):
        for alert in record.get('alerts', []):
            if not isinstance(alert, dict) or alert.get('symbol') not in symbols:
                continue
            key = (alert['symbol'], str(alert.get('source', '')), str(alert.get('reason', '')))
            if key not in seen:
                alerts.append(dict(alert, withdrawn=True)); seen.add(key)
        covered = {alert['symbol'] for alert in alerts}
        for symbol in record.get('withdrawn_symbols', []):
            if symbol in symbols and symbol not in covered:
                alerts.append({'symbol': symbol, 'source': '已保存撤销记录', 'reason': '此前已撤销，本日不自动恢复', 'withdrawn': True})
                covered.add(symbol)
    result['alerts'] = alerts
    withdrawn = {alert['symbol'] for alert in alerts}
    result['withdrawn_symbols'] = [item['symbol'] for item in result.get('items', []) if item.get('symbol') in withdrawn]
    if alerts:
        warning = '揭榜后出现负向证据；命中标的观察资格本日持续撤销，原始排名保留，不事后递补首位。'
        result['warnings'] = list(dict.fromkeys(list(result.get('warnings', [])) + [warning]))
    return result


def _cohort_risks(saved, moment, service=None):
    """Review the original symbols only; a changed score never changes ranks."""
    target = saved.get('data_date')
    vetoes, verification = _known_risks(moment, target)
    warnings, verified_historical_stale = [], set()
    try:
        dashboard = (service or core.Service()).dashboard(target)
        stocks = dashboard.get('stocks') if isinstance(dashboard, dict) else None
        quotes = core.getkv('valuation_quotes', {})
        quote_rows = quotes.get('quotes') if isinstance(quotes, dict) else None
        if not isinstance(stocks, list) or not stocks or not isinstance(quote_rows, dict):
            raise ValueError('research seed missing')
        # Service.dashboard gates historical rows against the latest completed
        # session for executable advice. That presentation-level stale marker
        # is not new negative evidence about the original cohort's dated bars.
        # Accept it only when the complete dashboard identity confirms this
        # exact historical report and its newer, real completed-session clock.
        meta = dashboard.get('meta') if isinstance(dashboard.get('meta'), dict) else {}
        index = dashboard.get('index') if isinstance(dashboard.get('index'), dict) else {}
        latest = md.last_completed_session(moment)
        latest = latest.isoformat() if isinstance(latest, dt.date) else str(latest)
        historical_view = (isinstance(target, str) and target < latest
            and meta.get('report_date') == target and meta.get('data_date') == target
            and meta.get('expected_date') == latest and index.get('date') == target)
        rows = {item.get('symbol'): item for item in stocks if isinstance(item, dict)}
        verification['quotes'] = {}
        for original in saved.get('items', []):
            symbol = original.get('symbol')
            stock, quote = rows.get(symbol), quote_rows.get(symbol)
            if stock is None:
                warnings.append('部分原始标的研究缓存不足，未声称重新完整核验；原排名保留。')
                vetoes.setdefault(symbol, {'symbol': symbol, 'source': '原队列发布后复核',
                    'reason': '当前原始标的研究缓存缺失，无法核验',
                    'observed_at': moment.isoformat(), 'withdrawn': True})
                continue
            risk = stock.get('chase_risk') if isinstance(stock.get('chase_risk'), dict) else {}
            metrics = risk.get('metrics') if isinstance(risk.get('metrics'), dict) else {}
            quote_evidence = observation_quote_evidence(stock, quote, quotes, target, moment, observation_only=True, defer_auction=True)
            if quote_evidence:
                verification['quotes'][symbol] = quote_evidence
                if quote_evidence['quote_basis'] == 'next_session_previous_close':
                    warnings.append('当前复核使用真实当日报价的昨收佐证原始行情日；原榜报价证据与排名保持不变，不将其改写成同日估值。')
                elif quote_evidence['quote_basis'] == 'auction_previous_close_unverified':
                    warnings.append('当日竞价报价不用于重排，尚未完成新价格核验；保留此前已核验收盘证据与原队列，不因报价日期切换自动撤销。')
            why = None
            if (stock.get('date') != target or not (stock.get('stale') is False
                    or stock.get('stale') is True and historical_view)):
                why = '当前原始标的行情已过期或日期不符'
            elif (stock.get('excluded') is True or core.exclusion(symbol, stock.get('name', ''), quote)
                  or 'ST' in str(stock.get('name', '')).upper() or '退' in str(stock.get('name', ''))):
                why = '当前证券状态或偏好排除条件已触发'
            elif (risk.get('version') != core.strategy.CHASE_POLICY_VERSION or risk.get('as_of_date') != target
                  or risk.get('blocked') is not False or risk.get('gate_passed') is not True
                  or metrics.get('raw_price_complete') is not True or metrics.get('benchmark_aligned') is not True):
                why = '当前追高、急跌或价格完整性风险未通过'
            elif quote_evidence is None:
                why = '当前报价、停牌/ST状态或未复权价格一致性未通过'
            if why:
                vetoes.setdefault(symbol, {'symbol': symbol, 'source': '原队列发布后复核', 'reason': why,
                                           'observed_at': moment.isoformat(), 'withdrawn': True})
            elif historical_view and stock.get('stale') is True and quote_evidence.get('quote_consistent') is True:
                verified_historical_stale.add(symbol)
    except Exception:
        warnings.append('原队列研究缓存不足，保留原始生成时间和排名；本次未声称重新计算或完整行情复核。')
    return list(vetoes.values()), verification, warnings, verified_historical_stale


def _correct_20261021_historical_display_error(saved, moment, current_alerts, verified_historical_stale):
    """Correct only the independently identified 21 October presentation bug.

    The original erroneous archive remains in Git history. This is deliberately
    not a general risk-restoration rule: all five original records must match,
    and all five must now pass the historical-data and current-risk recheck.
    """
    expected = (('sh601956', 56.34), ('sh603418', 55.02), ('sh688575', 53.88),
                ('sh603466', 53.88), ('sh603711', 53.58))
    symbols = {symbol for symbol, _ in expected}
    first_error = _instant('2026-10-21T19:14:16.523794+08:00')
    reviewed = _instant(saved.get('review_at'))
    items, alerts, withdrawn = saved.get('items'), saved.get('alerts'), saved.get('withdrawn_symbols')
    if (moment.date().isoformat() != '2026-10-21' or saved.get('date') != '2026-10-21'
            or saved.get('data_date') != '2026-10-20' or saved.get('signal_date') != '2026-10-20'
            or saved.get('protocol') != PROTOCOL or saved.get('status') != 'watch_only'
            or saved.get('published_at') != '2026-10-21T14:39:02.640852+08:00'
            or not reviewed or not first_error <= reviewed <= moment
            or not isinstance(items, list) or len(items) != 5
            or any(not isinstance(item, dict) for item in items)
            or tuple((item.get('symbol'), item.get('score')) for item in items) != expected
            or not isinstance(alerts, list) or len(alerts) != 5
            or any(not isinstance(alert, dict) or alert.get('source') != '原队列发布后复核'
                   or not isinstance(alert.get('symbol'), str)
                   or alert.get('reason') != '当前原始标的行情已过期或日期不符'
                   or alert.get('withdrawn') is not True for alert in alerts)
            or {alert.get('symbol') for alert in alerts} != symbols
            or not isinstance(withdrawn, list) or len(withdrawn) != 5
            or any(not isinstance(symbol, str) for symbol in withdrawn) or set(withdrawn) != symbols
            or set(verified_historical_stale) != symbols
            or any(alert.get('symbol') in symbols for alert in current_alerts if isinstance(alert, dict))):
        return saved
    result = copy.deepcopy(saved)
    result['alerts'], result['withdrawn_symbols'] = [], []
    obsolete = '揭榜后出现负向证据；命中标的观察资格本日持续撤销，原始排名保留，不事后递补首位。'
    correction = (f'2026-10-21日期校验错误更正：{reviewed.isoformat()}复核曾将20日历史报告的展示过期标记误判为五只原标的风险；'
                  '本次逐项核验历史行情、报价与已知风险后，仅更正该错误撤销，原发布排序、分数和时间不变。'
                  '原误判档案可追溯：https://github.com/Dopagip00/gup/commit/c618bb18e7ffeb10325f6ec331b4c670ced83f1c')
    result['warnings'] = list(dict.fromkeys([correction] + [warning for warning in result.get('warnings', []) if warning != obsolete]))
    return result


def _with_alerts(saved, moment, service=None):
    result = copy.deepcopy(saved)
    if result.get('items'):
        result['top_pick'] = result['items'][0]
    if result.get('date') != moment.date().isoformat():
        return result
    alerts, verification, warnings, verified_historical_stale = _cohort_risks(result, moment, service)
    previous_warnings = list(result.get('warnings', []))
    result = _correct_20261021_historical_display_error(result, moment, alerts, verified_historical_stale)
    correction_warnings = [warning for warning in result.get('warnings', []) if warning not in previous_warnings]
    _merge_alerts(result, {'alerts': alerts})
    result['current_risk_verification'] = verification
    result['review_at'] = moment.isoformat()
    result['warnings'] = list(dict.fromkeys(list(result.get('warnings', [])) + warnings))
    result['rankings_immutable'] = True
    if result.get('published_at'):
        key = KEY_PREFIX + result['date']
        with core.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            current = json.loads(row['value']) if row else None
            if (isinstance(current, dict) and current.get('protocol') == PROTOCOL
                    and current.get('date') == result['date'] and current.get('published_at')):
                # Read the latest stored cohort under the same transaction;
                # concurrent risk observers cannot erase each other's vetoes.
                latest = current
                current = _correct_20261021_historical_display_error(current, moment, alerts, verified_historical_stale)
                if current is latest and correction_warnings:
                    # A concurrent risk can invalidate the tentative correction.
                    # Keep that risk and omit this uncommitted correction notice;
                    # any earlier genuine correction notice stays on `current`.
                    result['warnings'] = [warning for warning in result['warnings'] if warning not in correction_warnings]
                result = _merge_alerts(copy.deepcopy(current), result)
                result.update(review_at=moment.isoformat(), current_risk_verification=verification, rankings_immutable=True)
                result['warnings'] = list(dict.fromkeys(list(result.get('warnings', [])) + warnings))
                con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, core.encode(result)))
    if result.get('items'):
        result['top_pick'] = result['items'][0]
    return result


def _archived_release(saved, moment, limit, service=None):
    """At 09:00 a genuine preparation freezes, including when seed expired."""
    if (not isinstance(saved, dict) or saved.get('protocol') != PROTOCOL or saved.get('status') not in ('watch_only', 'cash')
            or saved.get('ranking_policy') != RANKING_POLICY):
        return None
    day = moment.date()
    release = dt.datetime.combine(day, dt.time(9), core.TZ)
    generated, prepared = _instant(saved.get('generated_at')), _instant(saved.get('prepared_at') or saved.get('generated_at'))
    if (saved.get('date') != day.isoformat() or moment < release or saved.get('published_at') is not None
            or saved.get('publication_timing') != 'prepared' or _instant(saved.get('release_at')) != release
            or not generated or not prepared or not prepared <= generated < release <= moment):
        return None
    try:
        if not md.is_trading_day(day):
            return None
        target = md.last_completed_session(release)
        target = target.isoformat() if isinstance(target, dt.date) else str(target)
        latest = md.last_completed_session(moment)
        latest = latest.isoformat() if isinstance(latest, dt.date) else str(latest)
        if (saved.get('data_date') != target or saved.get('signal_date') != target or target > latest
                or generated.date() < _day(target)
                or saved.get('prepared_data_date', target) != target):
            return None
    except (md.MarketDataError, ValueError):
        return None
    if saved.get('status') == 'cash':
        if (saved.get('items') != [] or saved.get('top_pick') is not None
                or not main_wave.valid_market(saved.get('main_wave_market'), target)
                or saved['main_wave_market']['regime'] not in ('weak', 'range')):
            return None
        result = copy.deepcopy(saved)
        result.update(published_at=moment.isoformat(), generated_at=moment.isoformat(),
                      publication_timing='late' if moment >= release + dt.timedelta(minutes=30) else 'on_time',
                      late=moment >= release + dt.timedelta(minutes=30))
        return result
    items, seen = saved.get('items'), set()
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        return None
    vetoes, current_verification = _known_risks(moment, target)
    for item in items:
        if not isinstance(item, dict):
            return None
        symbol, verification, components = item.get('symbol'), item.get('verification'), item.get('components')
        if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in seen
                or not isinstance(item.get('name'), str) or not item['name'].strip()
                or 'ST' in item['name'].upper() or '退' in item['name'] or core.exclusion(symbol, item['name'], {})
                or item.get('asof') != target or not isinstance(verification, dict) or not isinstance(components, dict)
                or any(item.get(key) is not False for key in ('eligible', 'actionable', 'model_validated'))
                or any(key not in item or item[key] is not None for key in PRICE_FIELDS)
                or not _number(item.get('score')) or not 0 <= item['score'] <= 100
                or any(verification.get(key) is not True for key in ('quote_consistent', 'price_history_complete', 'chase_passed'))
                or verification.get('quote_date') != target
                or verification.get('model_validation_status') != 'not_validated'
                or any(not _number(components.get(key)) for key in ('technical', 'atr_pct', 'return_5d_pct', 'ma20_deviation_pct'))
                or not 0 <= components['technical'] <= 100 or components['atr_pct'] <= 0
                or components.get('weights') != {'technical': TECHNICAL_WEIGHT, 'financial': FINANCIAL_WEIGHT}):
            return None
        seen.add(symbol)
        if not _valid_short_confirmation(item.get('short_confirmation'), target):
            return None
        if saved.get('ranking_policy') == main_wave.POLICY and not main_wave.valid(item.get('main_wave'), target):
            return None
        if (saved.get('ranking_policy') == main_wave.POLICY
                and _instant(item['main_wave']['sector']['membership_at']) > generated):
            return None
        risk_keys=('technical_raw','risk_penalty','downside_deviation_60d_pct','max_drawdown_60d_pct','liquidity_proxy_20d_cny')
        if (any(not _number(components.get(key)) for key in risk_keys)
                or not 0<=components['technical_raw']<=100 or not 0<=components['risk_penalty']<=30
                or any(components[key]<0 for key in risk_keys[2:]) or components['liquidity_proxy_20d_cny']<=0
                or components.get('market_regime') not in ('normal','defensive')
                or verification.get('risk_window_end')!=target or verification.get('risk_return_samples')!=60
                or abs(components['technical']-round(max(0,components['technical_raw']-components['risk_penalty']),3))>.000001
                or abs(components['risk_penalty']-_risk_penalty(components['atr_pct'],components['downside_deviation_60d_pct'],components['max_drawdown_60d_pct'],components['liquidity_proxy_20d_cny'],components['market_regime']))>.000001):
            return None
        try:
            if not _day(verification.get('risk_window_start'))<_day(target):return None
        except (TypeError,ValueError):
            return None
        complete = verification.get('financial_complete')
        available=verification.get('financial_quality_available')
        if complete is True:
            if (verification.get('financial_data_date') != target or not _number(components.get('financial'))
                    or not 0 <= components['financial'] <= 100 or available is not True
                    or verification.get('financial_valuation_date')!=target
                    or verification.get('financial_evidence_basis')!='same_session_complete'):
                return None
            try:
                report=_day(verification.get('financial_report_date'))
                if (not report <= _day(verification.get('financial_publication_date')) <= _day(target)
                        or not report <= _day(verification.get('financial_provider_update_date')) <= _day(target)):
                    return None
            except (TypeError, ValueError):
                return None
        elif complete is False and available is True:
            if (verification.get('financial_evidence_basis')!='cached_disclosed_quality_without_valuation'
                    or verification.get('financial_valuation_date') is not None
                    or not _number(components.get('financial')) or not 0<=components['financial']<=100):
                return None
            try:
                original=_day(verification.get('financial_data_date'))
                report=_day(verification.get('financial_report_date'))
                published=_day(verification.get('financial_publication_date'))
                updated=_day(verification.get('financial_provider_update_date'))
                annual=_day(verification.get('financial_roe_report_date'))
                annual_published=_day(verification.get('financial_roe_publication_date'))
                if (not report<=published<=original<=_day(target) or not report<=updated<=original
                        or not annual<=annual_published<=original or not annual.isoformat().endswith('12-31')
                        or (_day(target)-report).days>200):
                    return None
            except (TypeError,ValueError):return None
        elif (complete is not False or available is not False or components.get('financial') is not None
              or verification.get('financial_evidence_basis')!='missing'):
            return None
        expected = TECHNICAL_WEIGHT * components['technical'] + (FINANCIAL_WEIGHT * components['financial'] if available else 0)
        if abs(item['score'] - round(expected, 3)) > .000001:
            return None
    result = copy.deepcopy(saved)
    result.update(published_at=moment.isoformat(), publication_timing='on_time' if moment < release + dt.timedelta(minutes=30) else 'late',
                  late=moment >= release + dt.timedelta(minutes=30),
                  release_basis='prepared_cohort_frozen_at_release', review_at=moment.isoformat(),
                  prepared_at=prepared.isoformat(), prepared_data_date=target)
    result['current_risk_verification'] = current_verification
    result['warnings'] = list(result.get('warnings', [])) + ['09:00原预备队列已揭榜；原生成时间、行情日及排序冻结，后续风险只撤销、不递补，不声称今晨重新排序。']
    result['top_pick'] = result['items'][0]
    return _with_alerts(result, moment, service)


def publish(date=None, service=None, now=None, limit=5):
    """Save only this independent cohort; prepared can become a real release."""
    moment = _moment(now)
    day = _day(date) if date is not None else moment.date()
    key = KEY_PREFIX + day.isoformat()
    existing = core.getkv(key)
    if (isinstance(existing, dict) and existing.get('date') == day.isoformat()
            and existing.get('published_at') and existing.get('protocol') == PROTOCOL):
        return _with_alerts(existing, moment, service)
    frozen = _archived_release(existing, moment, limit, service) if day == moment.date() else None
    result = frozen or build(day.isoformat(), service=service, now=moment, limit=limit)
    if result.get('status') not in ('watch_only', 'cash'):
        archived = _archived_release(existing, moment, limit, service) if result.get('status') == 'insufficient_data' else None
        if archived is None:
            if isinstance(existing, dict) and day == moment.date():
                checked = _with_alerts(existing, moment, service)
                result['alerts'], result['withdrawn_symbols'] = checked.get('alerts', []), checked.get('withdrawn_symbols', [])
            return result
        result = archived
    if isinstance(existing, dict) and existing.get('protocol') == PROTOCOL:
        result['prepared_at'] = existing.get('prepared_at') or existing.get('generated_at')
        result['prepared_data_date'] = existing.get('prepared_data_date') or existing.get('data_date')
    elif result['publication_timing'] == 'prepared':
        result['prepared_at'] = result['generated_at']
        result['prepared_data_date'] = result['data_date']
    with core.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        current = json.loads(row['value']) if row else None
        if isinstance(current, dict) and current.get('protocol') == PROTOCOL and current.get('published_at'):
            result = current
        else:
            result = _merge_alerts(result, current or {})
            con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, core.encode(result)))
    return _with_alerts(result, moment, service) if result.get('published_at') and day == moment.date() else result


def read(date=None, service=None, now=None):
    moment = _moment(now)
    day = _day(date) if date is not None else moment.date()
    saved = core.getkv(KEY_PREFIX + day.isoformat())
    if isinstance(saved, dict) and saved.get('protocol') == PROTOCOL and saved.get('date') == day.isoformat():
        if saved.get('published_at') or day < moment.date():
            return _with_alerts(saved, moment, service)
    return publish(day.isoformat(), service=service, now=moment)
