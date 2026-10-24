"""Daily, prefix-only main-wave research. No intraday odds or order execution."""
from __future__ import annotations

import datetime as dt
import math
from statistics import mean

PROTOCOL = 'main-wave-daily-v1'
POLICY = 'main-wave-observation-v4'
RULES_SOURCE = 'https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def window(bars, target):
    if not isinstance(bars, list) or not isinstance(target, str):
        return None
    rows = [r for r in bars if isinstance(r, dict) and isinstance(r.get('date'), str) and r['date'] <= target]
    dates = [r['date'] for r in rows]
    if len(rows) < 65 or dates != sorted(set(dates)) or dates[-1] != target:
        return None
    rows = rows[-65:]
    try:
        if any(dt.date.fromisoformat(r['date']).isoformat() != r['date'] for r in rows):
            return None
    except ValueError:
        return None
    if any(any(not finite(r.get(k)) or r[k] <= 0 for k in ('open', 'high', 'low', 'close', 'volume'))
           or not r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high'] for r in rows):
        return None
    return rows


def market(benchmark, target):
    rows = window(benchmark, target)
    if rows is None:
        return {'protocol': PROTOCOL, 'as_of': target, 'regime': 'unknown', 'reason': '指数日线不足'}
    closes = [r['close'] for r in rows]
    ma20, ma60, previous = mean(closes[-20:]), mean(closes[-60:]), mean(closes[-25:-5])
    strong = closes[-1] > ma20 > ma60 and ma20 > previous
    weak = closes[-1] < ma20 and ma20 <= previous
    return {'protocol': PROTOCOL, 'as_of': target, 'regime': 'strong' if strong else 'weak' if weak else 'range',
            'close': closes[-1], 'ma20': ma20, 'ma60': ma60, 'previous_ma20': previous,
            'session_dates': [r['date'] for r in rows], 'closes': closes,
            'reason': '指数趋势向上' if strong else '弱势市场' if weak else '震荡市场'}


def valid_market(value, target):
    if not isinstance(value, dict):
        return False
    dates, closes = value.get('session_dates'), value.get('closes')
    if not isinstance(dates, list) or not isinstance(closes, list) or len(dates) != 65 or len(closes) != 65:
        return False
    rows = [dict(date=d, open=p, high=p, low=p, close=p, volume=1) for d, p in zip(dates, closes)]
    return market(rows, target) == value and value.get('regime') in ('strong', 'weak', 'range')


def sectors(stocks, membership, target, moment):
    """Official industry peers are a proxy, never a verified speculative theme."""
    result = {}
    if not isinstance(membership, dict) or not isinstance(membership.get('industries'), dict):
        return result
    try:
        fetched = dt.datetime.fromisoformat(membership['fetched_at'])
        if fetched.tzinfo is None or fetched > moment or fetched.date().isoformat() < target:
            return result
    except (KeyError, ValueError, TypeError):
        return result
    if membership.get('source') != 'https://www.sse.com.cn/assortment/stock/list/share/':
        return result
    groups = {}
    seen = set()
    for stock in stocks:
        symbol = stock.get('symbol') if isinstance(stock, dict) else None
        if symbol in seen:
            return {}
        seen.add(symbol)
        industry = membership['industries'].get(symbol)
        if not isinstance(industry, str) or not industry.strip() or len(industry) > 80:
            continue
        if (stock.get('date') != target or stock.get('stale') is not False
                or stock.get('excluded') is True or any(not finite(stock.get(k)) for k in ('momentum20', 'close', 'ma20'))):
            continue
        groups.setdefault(industry, []).append(stock)
    for industry, peers in groups.items():
        peers.sort(key=lambda s: (-s['momentum20'], s['symbol']))
        size = len(peers)
        avg = mean(s['momentum20'] for s in peers)
        breadth = sum(s['close'] > s['ma20'] for s in peers) / size
        for rank, stock in enumerate(peers, 1):
            result[stock['symbol']] = {'industry': industry, 'peer_count': size, 'rank20': rank,
                'return20_mean_pct': avg, 'above_ma20_ratio': breadth, 'membership_at': fetched.isoformat(),
                'passed': size >= 5 and avg > 0 and breadth >= .6 and rank <= max(1, math.ceil(size * .2)),
                'basis': 'official_industry_proxy', 'theme_verified': False}
    return result


def assess(bars, benchmark, target, sector=None):
    market_state = market(benchmark, target)
    result = {'protocol': PROTOCOL, 'as_of': target, 'market': market_state, 'sector': sector,
              'passed': False, 'phase': 'unverified', 'failure': 'price_evidence_missing',
              'launch_probability_pct': None, 'limit_up_probability_pct': None,
              'intraday_confirmed': False, 'suggested_position_pct': 0.0,
              'action': '空仓等待', 'price_basis': 'daily_qfq_research', 'metrics': {}}
    rows, index = window(bars, target), window(benchmark, target)
    if rows is None or index is None or [r['date'] for r in rows] != [r['date'] for r in index]:
        return result
    closes = [r['close'] for r in rows]
    ma5, ma10, ma20, ma60 = [mean(closes[-n:]) for n in (5, 10, 20, 60)]
    last = rows[-1]
    volume_ratio = last['volume'] / mean(r['volume'] for r in rows[-21:-1])
    pressure = max(r['high'] for r in rows[-21:-1])
    upper_wick = (last['high'] - last['close']) / max(last['high'] - last['low'], 1e-12)
    trend = closes[-1] >= ma5 > ma10 > ma20 > ma60 and ma20 > mean(closes[-25:-5])
    breakout = trend and closes[-1] > pressure and 1.5 <= volume_ratio <= 3.0 and upper_wick <= .35
    pullback = (trend and volume_ratio <= .8 and ma5 * .99 <= last['low'] <= ma5 * 1.01
                and closes[-1] >= ma5 and closes[-1] >= last['open'])
    distribution = volume_ratio >= 2 and (upper_wick > .35 or closes[-1] < last['open'])
    result['phase'] = ('high_volume_distribution' if distribution else 'volume_breakout' if breakout
                       else 'dry_pullback' if pullback else 'weak_decline' if closes[-1] < ma20 else 'no_setup')
    result['metrics'] = {'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'volume_ratio20': volume_ratio, 'pressure20': pressure, 'support10': min(r['low'] for r in rows[-10:]),
        'upper_wick_ratio': upper_wick, 'bias20_pct': (closes[-1] / ma20 - 1) * 100,
        'trend_aligned': trend}
    result['failure'] = ('market_' + market_state['regime'] if market_state['regime'] != 'strong'
                         else 'high_volume_distribution' if distribution else 'no_trend_setup' if not (breakout or pullback)
                         else 'sector_unverified' if not isinstance(sector, dict)
                         else 'not_industry_leader' if sector.get('passed') is not True else None)
    result['passed'] = result['failure'] is None
    if result['passed']:
        result['action'] = '突破确认后观察' if breakout else '回踩确认后观察'
    return result


def evidence(bars, benchmark, target, sector):
    report = assess(bars, benchmark, target, sector)
    if not report['passed']:
        return report
    fields = ('date', 'open', 'high', 'low', 'close', 'volume')
    report['evidence'] = {name: [{k: r[k] for k in fields} for r in window(source, target)]
                          for name, source in (('stock', bars), ('index', benchmark))}
    return report


def valid(report, target):
    if not isinstance(report, dict) or report.get('as_of') != target or not isinstance(report.get('evidence'), dict):
        return False
    sector = report.get('sector')
    if (not isinstance(sector, dict) or set(sector) != {'industry','peer_count','rank20','return20_mean_pct',
            'above_ma20_ratio','membership_at','passed','basis','theme_verified'}
            or not isinstance(sector.get('industry'), str) or not 0 < len(sector['industry']) <= 80
            or sector.get('basis') != 'official_industry_proxy'
            or type(sector.get('peer_count')) is not int or sector['peer_count'] < 5
            or type(sector.get('rank20')) is not int or not 1 <= sector['rank20'] <= math.ceil(sector['peer_count'] * .2)
            or not finite(sector.get('return20_mean_pct')) or sector['return20_mean_pct'] <= 0
            or not finite(sector.get('above_ma20_ratio')) or not .6 <= sector['above_ma20_ratio'] <= 1
            or sector.get('theme_verified') is not False):
        return False
    try:
        stamp = dt.datetime.fromisoformat(sector['membership_at'])
        if stamp.tzinfo is None or stamp.date().isoformat() < target:
            return False
    except (TypeError, ValueError):
        return False
    raw = report['evidence']
    rebuilt = evidence(raw.get('stock'), raw.get('index'), target, sector)
    return rebuilt.get('passed') is True and rebuilt == report


def apply_stock_gates(report, stock):
    risk = stock.get('chase_risk') if isinstance(stock.get('chase_risk'), dict) else {}
    if stock.get('stale') is not False or stock.get('excluded') is True or risk.get('gate_passed') is not True:
        report.update(passed=False, failure='existing_risk_gate', action='空仓等待')
    return report


def decision(report):
    """Four concise modules. Daily snapshots cannot authorize intraday execution."""
    m = report.get('metrics', {})
    f = lambda key: f"{m[key]:.2f}" if finite(m.get(key)) else '—'
    phase = {'volume_breakout': '平台放量突破', 'dry_pullback': '缩量回调确认',
             'high_volume_distribution': '高位放量分歧', 'weak_decline': '弱势下跌',
             'no_setup': '形态未确认', 'unverified': '数据不足'}.get(report.get('phase'), '数据不足')
    sector = report.get('sector') or {}
    sector_rank = (f"{sector['industry']} · {sector['rank20']}/{sector['peer_count']}"
                   if all(k in sector for k in ('industry','rank20','peer_count')) else '行业数据不足')
    breadth = f"{sector['above_ma20_ratio']*100:.2f}%" if finite(sector.get('above_ma20_ratio')) else '—'
    facts = [{'label':'均线 MA5 / MA10', 'value':f"{f('ma5')} / {f('ma10')}"},
             {'label':'均线 MA20 / MA60', 'value':f"{f('ma20')} / {f('ma60')}"},
             {'label':'均线多头排列', 'value':'已满足' if m.get('trend_aligned') else '未满足' if m else '数据不足'},
             {'label':'成交量 / 前20日均量', 'value':f('volume_ratio20')+' 倍'},
             {'label':'20日乖离率', 'value':f('bias20_pct')+'%'},
             {'label':'上影线占当日振幅', 'value':f"{m['upper_wick_ratio']*100:.2f}%" if finite(m.get('upper_wick_ratio')) else '—'},
             {'label':'行业20日涨幅排名', 'value':sector_rank},
             {'label':'同行站上MA20比例', 'value':breadth}]
    return [
        {'title': '1. 市场情绪与地位判别', 'text': f"{report['market']['reason']} · {phase}；行业共振{'通过' if sector.get('passed') else '未确认'}。行业排名为官方行业代理。", 'facts':facts},
        {'title': '2. 盘中执行计划', 'text': f"{report['action']}；新增仓位0.00%。缺分时/竞价确认，不提供实时买价或打板指令。"},
        {'title': '3. T+1动态仓位管理', 'text': '当日新买股票次一交易日才可卖；未读取持仓，不生成做T、加减仓比例。'},
        {'title': '4. 硬性风控与退出机制', 'text': f"复权研究参考：MA10 {f('ma10')}，10日支撑 {f('support10')}，前20日压力 {f('pressure20')}。突破形态要求日成交量为前20日均量1.50–3.00倍、上影线占振幅不超过35.00%；缩量回踩要求不超过0.80倍。实时原价未核验，止损/目标价暂空；跌停可能无法成交。"},
    ]
