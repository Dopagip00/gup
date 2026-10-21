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
from urllib.parse import urlsplit

import core
import daily_plan
import market_data as md

PROTOCOL = 'daily-observation-v1'
KEY_PREFIX = 'daily_watchlist:'
TECHNICAL_WEIGHT, FINANCIAL_WEIGHT = .60, .40
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
            'method': '完整同日财务证据池优先；固定60%技术规则分+40%已披露财务分，缺财务仅计60%技术分。并列按较低ATR占比、证券代码排序；不使用未验证模型的预期涨幅。',
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
        score = TECHNICAL_WEIGHT * stock['score'] + (FINANCIAL_WEIGHT * record['score'] if complete else 0)
        atr = stock['atr14'] / stock['close'] * 100
        risk_reasons = risk.get('reasons') if isinstance(risk.get('reasons'), list) else []
        risk_notes = [str(item.get('detail'))[:300] for item in risk_reasons if isinstance(item, dict) and item.get('detail')]
        risk_notes.append('短线模型验证未作为本观察榜的合格买入依据；相对分数不是预期收益或上涨概率。')
        if quote_evidence['quote_basis'] == 'next_session_previous_close':
            risk_notes.append('盘中迟到观察恢复：今日报价的昨收仅佐证上一交易日原价收盘，不是该日同日估值；财务部分按缺失处理。')
        if not complete:
            risk_notes.append('同日完整财务证据不足，仅计技术部分，不填补财务分；置信度较低。')
        elif record.get('eligible') is not True:
            financial_risks = record.get('risks') if isinstance(record.get('risks'), list) else []
            risk_notes.append('财务证据完整与长线筛选合格不同；该股未通过原长线筛选。')
            risk_notes.extend(str(value)[:300] for value in financial_risks[:12])
            risk_notes.append(f"年度ROE{record['roe']:.2f}%，资产负债率{record['debt_ratio']:.2f}%；营收/归母净利同比{record['revenue_growth']:+.2f}%/{record['profit_growth']:+.2f}%。")
        reasons = [f"技术规则分{stock['score']:.1f}；近5日{metrics['return_5d_pct']:+.2f}%，偏离MA20{metrics['ma20_deviation_pct']:+.2f}%。"]
        if complete:
            reasons.append(f"已披露财务分{record['score']:.1f}；PE(TTM){record['pe_ttm']:.2f}，ROE{record['roe']:.2f}%。")
            reasons.append(f"营收/归母净利同比{record['revenue_growth']:+.2f}%/{record['profit_growth']:+.2f}%，经营现金流/净利{record['operating_cash_to_profit']:.2f}。")
        per_verification = dict(verification, **quote_evidence, price_history_complete=True,
                                chase_passed=True, financial_complete=complete,
                                financial_data_date=target if complete else None,
                                financial_report_date=record.get('report_date') if complete else None,
                                financial_publication_date=record.get('publication_date') if complete else None,
                                model_validation_status='not_validated')
        per_verification['ai_reviewed'] = verification['ai_status'] == 'reviewed' and symbol in reviewed
        per_verification['ai_status'] = 'reviewed' if per_verification['ai_reviewed'] else 'unverified'
        item = {'symbol': symbol, 'name': name, 'asof': target, 'score': round(score, 3),
                'components': {'technical': stock['score'], 'financial': record['score'] if complete else None,
                               'weights': {'technical': TECHNICAL_WEIGHT, 'financial': FINANCIAL_WEIGHT},
                               'atr_pct': round(atr, 3), 'return_5d_pct': metrics['return_5d_pct'],
                               'ma20_deviation_pct': metrics['ma20_deviation_pct']},
                'reasons': reasons, 'risk_notes': list(dict.fromkeys(risk_notes)), 'verification': per_verification,
                'model_validated': False, 'actionable': False, 'eligible': False,
                **dict.fromkeys(PRICE_FIELDS)}
        pool.append(item)
    full = [item for item in pool if item['verification']['financial_complete']]
    selected_pool = full if len(full) >= limit else pool
    selected_pool.sort(key=lambda item: (not item['verification']['financial_complete'], -item['score'],
                                         item['components']['atr_pct'], item['symbol']))
    result['coverage'] = {'universe': len(stocks), 'price_and_known_risk_pool': len(pool),
                          'complete_financial': len(full), 'selected': min(limit, len(selected_pool)),
                          'excluded_counts': counts, 'financial_pool_used': 'complete' if len(full) >= limit else 'partial'}
    result['verification']['financial_status'] = 'complete' if len(full) >= limit else 'partial'
    result['items'] = selected_pool[:limit]
    result['top_pick'] = result['items'][0] if result['items'] else None
    if any(item['verification']['quote_basis'] == 'next_session_previous_close' for item in result['items']):
        result['warnings'].append('盘中迟到恢复观察榜；真实当日报价以昨收佐证此前完整日线，保留实际报价日期与时间，未冒充此前同日估值或09:00发布。财务仍按缺失处理。')
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
    warnings = []
    try:
        dashboard = (service or core.Service()).dashboard(target)
        stocks = dashboard.get('stocks') if isinstance(dashboard, dict) else None
        quotes = core.getkv('valuation_quotes', {})
        quote_rows = quotes.get('quotes') if isinstance(quotes, dict) else None
        if not isinstance(stocks, list) or not stocks or not isinstance(quote_rows, dict):
            raise ValueError('research seed missing')
        rows = {item.get('symbol'): item for item in stocks if isinstance(item, dict)}
        verification['quotes'] = {}
        for original in saved.get('items', []):
            symbol = original.get('symbol')
            stock, quote = rows.get(symbol), quote_rows.get(symbol)
            if stock is None:
                warnings.append('部分原始标的研究缓存不足，未声称重新完整核验；原排名保留。')
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
            if stock.get('stale') is not False or stock.get('date') != target:
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
    except Exception:
        warnings.append('原队列研究缓存不足，保留原始生成时间和排名；本次未声称重新计算或完整行情复核。')
    return list(vetoes.values()), verification, warnings


def _with_alerts(saved, moment, service=None):
    result = copy.deepcopy(saved)
    if result.get('items'):
        result['top_pick'] = result['items'][0]
    if result.get('date') != moment.date().isoformat():
        return result
    alerts, verification, warnings = _cohort_risks(result, moment, service)
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
                result = _merge_alerts(copy.deepcopy(current), result)
                result.update(review_at=moment.isoformat(), current_risk_verification=verification, rankings_immutable=True)
                result['warnings'] = list(dict.fromkeys(list(result.get('warnings', [])) + warnings))
                con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, core.encode(result)))
    if result.get('items'):
        result['top_pick'] = result['items'][0]
    return result


def _archived_release(saved, moment, limit, service=None):
    """At 09:00 a genuine preparation freezes, including when seed expired."""
    if not isinstance(saved, dict) or saved.get('protocol') != PROTOCOL or saved.get('status') != 'watch_only':
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
        complete = verification.get('financial_complete')
        if complete is True:
            if (verification.get('financial_data_date') != target or not _number(components.get('financial'))
                    or not 0 <= components['financial'] <= 100):
                return None
            try:
                if not _day(verification.get('financial_report_date')) <= _day(verification.get('financial_publication_date')) <= _day(target):
                    return None
            except (TypeError, ValueError):
                return None
        elif complete is not False or components.get('financial') is not None:
            return None
        expected = TECHNICAL_WEIGHT * components['technical'] + (FINANCIAL_WEIGHT * components['financial'] if complete else 0)
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
    if result.get('status') != 'watch_only':
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
