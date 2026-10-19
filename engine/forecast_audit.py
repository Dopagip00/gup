"""Prospective checks of immutable published research, never account returns.

Only forecasts actually recorded before the D1 open are evaluated.  Historical
model fits are not backfilled into this series.  Daily bars cannot prove that
an order filled, and revised adjusted histories are not point-in-time archives.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from statistics import mean

MAX_RESEARCH_FORECASTS = 20
MAX_PUBLISHED_PLANS = 120
MAX_CYCLE_CALENDAR_DAYS = 31
ROUND_TRIP_COST_STRESS_PCT = 0.30
TZ = dt.timezone(dt.timedelta(hours=8))
SYMBOL = re.compile(r'^(?:sh|sz)\d{6}$')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def snapshot(stocks, signal_date, buy_date, sell_date, published_at):
    """A bounded, declared selection sample; it is not a market-wide win rate."""
    signal, entry, exit_day = map(_date, (signal_date, buy_date, sell_date))
    captured = _instant(published_at, TZ)
    if not (signal and entry and exit_day and signal < entry < exit_day and
            (exit_day - signal).days <= MAX_CYCLE_CALENDAR_DAYS and captured and
            captured.date() == entry and dt.time(9) <= captured.time().replace(tzinfo=None) < dt.time(9, 30)):
        return []
    selected, seen = [], set()
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        p = stock.get('prediction') or {}
        symbol = stock.get('symbol')
        if (not isinstance(p, dict) or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in seen or
                stock.get('date') != signal_date or stock.get('stale') or stock.get('excluded') or
                p.get('as_of_date') != signal_date or p.get('available') is not True or
                p.get('horizon_days') != 2 or p.get('target_kind') != 'next_open_to_following_close' or
                not _version(p.get('model_version')) or
                not finite(p.get('expected_return_pct')) or not finite(p.get('raw_rank_value'))):
            continue
        seen.add(symbol)
        low, high = p.get('interval_low_pct'), p.get('interval_high_pct')
        interval_valid = finite(low) and finite(high) and low <= high
        selected.append({'symbol': symbol, 'name': stock.get('name'),
            'signal_date': signal_date, 'buy_date': buy_date, 'sell_date': sell_date,
            'published_at': published_at, 'model_version': p.get('model_version'),
            'validation_protocol': p.get('validation_protocol'),
            'expected_return_pct': p['expected_return_pct'], 'rank_value': p['raw_rank_value'],
            'interval_low_pct': low if interval_valid else None, 'interval_high_pct': high if interval_valid else None,
            'model_qualified': p.get('eligible') is True,
            'recommendation_qualified': stock.get('eligible') is True and p.get('eligible') is True})
    return sorted(selected, key=lambda x: (-x['rank_value'], x['symbol']))[:MAX_RESEARCH_FORECASTS]


def _date(value):
    try:
        result = dt.date.fromisoformat(value)
        return result if result.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _instant(value, timezone):
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _version(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def _published_payload(value):
    """SQLite predicate keeps the result bounded without requiring JSON1."""
    try:
        payload = json.loads(value)
        return int(isinstance(payload, dict) and payload.get('status') == 'published')
    except (TypeError, ValueError, RecursionError):
        return 0


def _recent_plans(core):
    with core.connect() as con:
        con.create_function('forecast_audit_published', 1, _published_payload, deterministic=True)
        rows = con.execute("SELECT key,value FROM kv WHERE key GLOB 'daily_plan:????-??-??' "
                           "AND forecast_audit_published(value)=1 ORDER BY key DESC LIMIT ?", (MAX_PUBLISHED_PLANS,))
        return [(row[0], json.loads(row[1])) for row in rows]


def _load_needed_history(core, symbol, needed_dates):
    """Validate before caching; retain only required OHLC fields, never metadata.

    Duplicate dates anywhere in a history make its date mapping ambiguous. A
    caller caches this failure too, so later rows cannot reuse a partial map.
    """
    payload = core.load_history(symbol)
    rows = payload.get('bars') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('历史日线格式缺失')
    output, seen = {}, set()
    for row in rows:
        day = _date(row.get('date')) if isinstance(row, dict) else None
        if day is None:
            raise ValueError('历史包含无效交易日期')
        key = day.isoformat()
        if key in seen:
            raise ValueError('历史包含重复交易日期')
        seen.add(key)
        if key in needed_dates:
            output[key] = {field: row.get(field) for field in ('open', 'close', 'raw_open', 'raw_close', 'volume')}
    return output


def _direction(value):
    return 1 if value > 1e-10 else (-1 if value < -1e-10 else 0)


def evaluate(core):
    moment = core.now().astimezone(core.TZ)
    try:
        completed = _date(str(core.expected_date()))
        last_completed = completed.isoformat() if completed and completed <= moment.date() else None
    except Exception:
        last_completed = None
    plans = _recent_plans(core)
    outcomes, pending, needed_dates = [], [], {}
    calendar, cycles = {}, {}

    def trading(day):
        if day not in calendar:
            try:
                calendar[day] = (bool(core.md.is_trading_day(day)), None)
            except Exception:
                calendar[day] = (False, '官方交易日历不可核验')
        valid, error = calendar[day]
        if error:
            raise ValueError(error)
        return valid

    def exact_cycle(signal, entry, exit_day):
        key = (signal, entry, exit_day)
        if key not in cycles:
            try:
                if not trading(signal):
                    raise ValueError('信号日不是官方交易日')
                day, sessions = signal, []
                while day < exit_day:
                    day += dt.timedelta(days=1)
                    if trading(day):
                        sessions.append(day)
                if sessions != [entry, exit_day]:
                    raise ValueError('目标并非连续官方D1/D2交易日')
                cycles[key] = None
            except ValueError as exc:
                cycles[key] = str(exc)
        if cycles[key]:
            raise ValueError(cycles[key])

    for key, plan in reversed(plans):
        if plan.get('status') != 'published' or plan.get('morning_ready') is not True or plan.get('late'):
            continue
        captured = _instant(plan.get('published_at'), core.TZ)
        buy_day = _date(plan.get('date'))
        if not (captured and buy_day and key == 'daily_plan:' + buy_day.isoformat() and captured.date() == buy_day and
                dt.time(9) <= captured.time().replace(tzinfo=None) < dt.time(9, 30) and captured <= moment):
            continue
        items = plan.get('research_forecasts')
        if not isinstance(items, list):
            continue
        seen = set()
        for item in items[:MAX_RESEARCH_FORECASTS]:
            if not isinstance(item, dict):
                continue
            signal, entry, exit_day = (_date(item.get(k)) for k in ('signal_date', 'buy_date', 'sell_date'))
            symbol = item.get('symbol')
            if not (signal and entry and exit_day and signal < entry < exit_day and
                    entry == buy_day and item.get('published_at') == plan.get('published_at') and
                    (plan.get('data_date') is None or plan.get('data_date') == signal.isoformat()) and
                    isinstance(symbol, str) and SYMBOL.fullmatch(symbol) and symbol not in seen and
                    _version(item.get('model_version')) and finite(item.get('expected_return_pct'))):
                continue
            seen.add(symbol)
            record = {k: item.get(k) for k in ('symbol', 'name', 'signal_date', 'buy_date', 'sell_date',
                'published_at', 'model_version', 'validation_protocol', 'expected_return_pct', 'rank_value',
                'interval_low_pct', 'interval_high_pct', 'model_qualified', 'recommendation_qualified')}
            for field in ('rank_value', 'interval_low_pct', 'interval_high_pct'):
                if not finite(record[field]):
                    record[field] = None
            record['status'] = 'waiting'
            outcomes.append(record)
            if (exit_day - signal).days > MAX_CYCLE_CALENDAR_DAYS:
                record.update(status='data_unavailable', reason='目标跨度超过31个日历日，拒绝异常周期')
                continue
            # expected_date is necessary but not sufficient: no today's D2
            # outcome can mature before the actual exchange closing time.
            if (not last_completed or exit_day.isoformat() > last_completed or exit_day > moment.date() or
                    (exit_day == moment.date() and moment.time().replace(tzinfo=None) < dt.time(15))):
                continue
            try:
                exact_cycle(signal, entry, exit_day)
                needed_dates.setdefault(symbol, set()).update((entry.isoformat(), exit_day.isoformat()))
                pending.append(record)
            except ValueError as exc:
                record.update(status='data_unavailable', reason=str(exc)[:120])

    # Each required symbol is read and validated at most once per evaluation.
    # Cache failures explicitly instead of exposing a partially filled mapping.
    histories = {}
    for symbol, dates in needed_dates.items():
        try:
            histories[symbol] = (_load_needed_history(core, symbol, dates), None)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            histories[symbol] = (None, str(exc)[:120])
        except Exception:
            histories[symbol] = (None, '历史日线不可核验')
    for record in pending:
        try:
            bars, error = histories[record['symbol']]
            if error:
                raise ValueError(error)
            entry, exit_day = _date(record['buy_date']), _date(record['sell_date'])
            first, second = bars[entry.isoformat()], bars[exit_day.isoformat()]
            values = (first.get('open'), second.get('close'), first.get('raw_open'), second.get('raw_close'),
                      first.get('volume'), second.get('volume'))
            if not all(finite(value) and value > 0 for value in values):
                raise ValueError('目标开收盘或成交量缺失，不能证明周期可交易')
            adjusted = (second['close'] / first['open'] - 1) * 100
            nominal = (second['raw_close'] / first['raw_open'] - 1) * 100
            absolute_error = abs(adjusted - record['expected_return_pct'])
            if not all(finite(value) for value in (adjusted, nominal, absolute_error)):
                raise ValueError('价格回报或预测误差超出有效数值范围')
            record.update(status='matured', adjusted_return_pct=round(adjusted, 4),
                nominal_price_return_pct=round(nominal, 4), absolute_error_pct=round(absolute_error, 4),
                direction_correct=(record['expected_return_pct'] > 0) == (adjusted > 0),
                direction_three_sign_correct=_direction(record['expected_return_pct']) == _direction(adjusted),
                adjusted_cost_stress_return_pct=round(adjusted - ROUND_TRIP_COST_STRESS_PCT, 4),
                nominal_cost_stress_return_pct=round(nominal - ROUND_TRIP_COST_STRESS_PCT, 4),
                round_trip_cost_stress_pct=ROUND_TRIP_COST_STRESS_PCT)
            low, high = record.get('interval_low_pct'), record.get('interval_high_pct')
            record['in_research_interval'] = low <= adjusted <= high if finite(low) and finite(high) and low <= high else None
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            record.update(status='data_unavailable', reason=str(exc)[:120])
    complete = [row for row in outcomes if row['status'] == 'matured']
    versions = sorted({str(row.get('model_version')) for row in complete})
    def summarize(rows):
        return {'samples': len(rows),
            'mae_pct': round(mean(row['absolute_error_pct'] for row in rows), 3) if rows else None,
            'direction_accuracy_pct': round(mean(row['direction_correct'] for row in rows) * 100, 2) if rows else None,
            'three_sign_direction_accuracy_pct': round(mean(row['direction_three_sign_correct'] for row in rows) * 100, 2) if rows else None,
            'mean_nominal_cost_stress_return_pct': round(mean(row['nominal_cost_stress_return_pct'] for row in rows), 3) if rows else None}
    return {'as_of': moment.isoformat(), 'last_completed_session': last_completed,
        'plan_window': {'limit': MAX_PUBLISHED_PLANS, 'published_plans_considered': len(plans),
            'oldest_date': plans[-1][1].get('date') if plans else None,
            'newest_date': plans[0][1].get('date') if plans else None,
            'scope': '按归档日期读取最近120份status=published日报，早期档案保留；其中仅真实盘前核验记录参与评估'},
        'status': 'available' if complete else 'awaiting_prospective_outcomes',
        'captured_forecasts': len(outcomes), 'matured': len(complete),
        'waiting': sum(row['status'] == 'waiting' for row in outcomes),
        'data_unavailable': sum(row['status'] == 'data_unavailable' for row in outcomes),
        'by_model_version': {version: summarize([row for row in complete if str(row.get('model_version')) == version]) for version in versions},
        'selection': '仅实际09:00至09:29记录的前20个研究排名，包含未通过推荐者；不是全市场随机样本，也不是账户持仓。',
        'return_basis': '当前缓存前复权D1开盘至D2收盘；另列原价价格回报，未完整重建现金分红、配股或历史复权版本。',
        'cost_stress_method': '两种价格回报各减去0.30个百分点固定每回合研究压力；不是实际税费、成交或账户收益。',
        'warnings': ['没有事前记录就不回填历史成绩。', '同日股票和相邻时期相关，样本数不代表独立试验次数。',
            'direction_accuracy口径为上涨>0与未上涨<=0；另列正/零/负三方向诊断，两者均不是未来上涨概率。',
            '原价成本压力仅扣每回合0.30个百分点研究假设，不是实际账户收益；涨跌停、触价、滑点及最低佣金可能使成交更差。'],
        'outcomes': outcomes}
