"""One frozen prefix-only short-confirmation audit; read-only market database.

This is a price-component ablation. It never updates a model, production
watchlist, or financial snapshot. All selections freeze before outcome access.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

TZ = dt.timezone(dt.timedelta(hours=8))
COST = .30
EXPECTED_PROTOCOL = 'fixed-short-confirmation-v1-audit-20261022'


def finite(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (not positive or value > 0))


def risk_profile(bars, benchmark, target):
    """Frozen v2 risk formula, independent of newly edited watchlist helpers."""
    def window(rows):
        selected = [row for row in rows if isinstance(row, dict)
                    and isinstance(row.get('date'), str) and row['date'] <= target]
        dates = [row['date'] for row in selected]
        if dates != sorted(set(dates)) or len(selected) < 61 or dates[-1] != target:
            return None
        try:
            for day in dates:
                if dt.date.fromisoformat(day).isoformat() != day:
                    return None
        except ValueError:
            return None
        return selected[-61:]
    rows, index = window(bars), window(benchmark)
    if rows is None or index is None or [r['date'] for r in rows] != [r['date'] for r in index]:
        return None
    for row in [*rows, *index]:
        if (any(not finite(row.get(k), positive=True) for k in ('open', 'high', 'low', 'close'))
                or not row['low'] <= min(row['open'], row['close'])
                <= max(row['open'], row['close']) <= row['high']):
            return None
    if any(not finite(row.get('volume'), positive=True)
           or not finite(row.get('raw_close'), positive=True) for row in rows[-20:]):
        return None
    returns = [(b['close'] / a['close'] - 1) * 100 for a, b in zip(rows, rows[1:])]
    downside = math.sqrt(mean(min(0, value) ** 2 for value in returns))
    peak, drawdown = rows[0]['close'], 0
    for row in rows:
        peak = max(peak, row['close'])
        drawdown = max(drawdown, (1 - row['close'] / peak) * 100)
    ranges = [max(b['high'] - b['low'], abs(b['high'] - a['close']),
                  abs(b['low'] - a['close'])) for a, b in zip(rows, rows[1:])]
    atr = mean(ranges[-14:]) / rows[-1]['close'] * 100
    liquidity = median(row['raw_close'] * row['volume'] * 100 for row in rows[-20:])
    defensive = (index[-1]['close'] < mean(r['close'] for r in index[-60:])
                 and index[-1]['close'] < index[-21]['close'])
    atr, downside, drawdown, liquidity = [round(v, 3) for v in (atr, downside, drawdown, liquidity)]
    penalty = round(min(30, 2 * atr + 3 * downside * (1.5 if defensive else 1)
                        + .25 * drawdown + 10 * (1 - min(1, liquidity / 20_000_000))), 3)
    return {'atr_pct': atr, 'downside_pct': downside, 'drawdown_pct': drawdown,
            'liquidity_proxy_20d_cny': liquidity,
            'market_regime': 'defensive' if defensive else 'normal', 'risk_penalty': penalty}


def confirmation(prefix, benchmark, day, forecast):
    """Zero-bound price checks and the pre-existing economic cost threshold."""
    # The v2 risk window has already proved these six consecutive sessions align.
    close = prefix[-1]['close']
    ma5 = mean(row['close'] for row in prefix[-5:])
    return5 = (close / prefix[-6]['close'] - 1) * 100
    index5 = (benchmark[-1]['close'] / benchmark[-6]['close'] - 1) * 100
    evidence = {'close': close, 'ma5': ma5, 'return_5d_pct': return5,
                'index_return_5d_pct': index5, 'excess_5d_pct': return5 - index5}
    if close < ma5 or return5 <= 0 or return5 - index5 < 0:
        return False, 'short_price_confirmation_failed', evidence
    prediction = forecast.predict(prefix, benchmark)
    for key in ('available', 'model_version', 'as_of_date', 'horizon_days',
                'target_kind', 'expected_return_pct'):
        evidence[key] = prediction.get(key)
    if (prediction.get('available') is not True
            or prediction.get('model_version') != 'ridge-cycle-v3'
            or prediction.get('as_of_date') != day
            or prediction.get('horizon_days') != 2
            or prediction.get('target_kind') != 'next_open_to_following_close'
            or not finite(prediction.get('expected_return_pct'))):
        return False, 'missing_or_incoherent_model_evidence', evidence
    if prediction['expected_return_pct'] <= COST:
        return False, 'point_estimate_not_above_existing_cost', evidence
    return True, 'confirmed', evidence


def outcome(history, entry_day, exit_day):
    """Called only after all dates' selections are frozen."""
    rows = {r['date']: r for r in history if r.get('date') in (entry_day, exit_day)}
    if entry_day not in rows or exit_day not in rows:
        return None, 'missing_entry_or_exit_bar'
    entry, exit_row = rows[entry_day], rows[exit_day]
    opening, closing = entry.get('raw_open'), exit_row.get('raw_close')
    if not finite(opening, positive=True) or not finite(closing, positive=True):
        return None, 'missing_or_invalid_raw_entry_or_exit_price'
    if not finite(entry.get('volume'), positive=True) or not finite(exit_row.get('volume'), positive=True):
        return None, 'zero_or_invalid_entry_or_exit_volume'
    return (closing / opening - 1) * 100 - COST, None


def summarize(rows, top):
    unresolved = [r['date'] for r in rows if r['net_return_pct'] is None]
    index_unresolved = [r['date'] for r in rows if r['index_return_pct'] is None]
    result = {'dates': len(rows), 'selected_slots': sum(r['selected_count'] for r in rows),
              'total_slots': len(rows) * top,
              'slot_utilization_pct': round(sum(r['selected_count'] for r in rows) / (len(rows) * top) * 100, 4),
              'dates_with_at_least_one_candidate': sum(r['pool_size'] >= 1 for r in rows),
              'dates_with_at_least_five_candidates': sum(r['pool_size'] >= 5 for r in rows),
              'empty_dates': sum(r['selected_count'] == 0 for r in rows),
              'unresolved_dates': unresolved, 'index_unresolved_dates': index_unresolved,
              'complete_return_summary': not unresolved and not index_unresolved,
              'mean_net_return_pct': None, 'mean_excess_index_pct': None,
              'worst_date_pct': None, 'worst_decile_mean_pct': None,
              'synthetic_max_drawdown_pct': None}
    if unresolved or index_unresolved:
        return result
    returns = [r['net_return_pct'] for r in rows]
    index = [r['index_return_pct'] for r in rows]
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        drawdown = max(drawdown, (1 - equity / peak) * 100)
    result.update(mean_net_return_pct=round(mean(returns), 6),
                  mean_excess_index_pct=round(mean(a - b for a, b in zip(returns, index)), 6),
                  worst_date_pct=round(min(returns), 6),
                  worst_decile_mean_pct=round(mean(sorted(returns)[:max(1, math.ceil(len(rows) * .1))]), 6),
                  synthetic_max_drawdown_pct=round(drawdown, 6))
    return result


def run(engine, database):
    sys.path.insert(0, str(engine.resolve()))
    import forecast
    import strategy
    protocol_path = Path(__file__).with_name('short-confirmation-v1-protocol.json')
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    if protocol.get('protocol') != EXPECTED_PROTOCOL or not protocol.get('selection_boundary'):
        raise ValueError('The fixed protocol and selection boundary must exist before evaluation')
    if forecast.MODEL_VERSION != 'ridge-cycle-v3' or forecast.ROUND_TRIP_COST_STRESS_PCT != COST:
        raise ValueError('Model or fixed existing cost has changed')
    started = dt.datetime.now(TZ).isoformat()
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        securities = [(s, n) for s, n in con.execute('SELECT symbol,name FROM securities WHERE active=1')
                      if isinstance(s, str) and len(s) == 8 and s.startswith('sh') and s[2:].isdigit()]
        securities.sort(key=lambda row: hashlib.sha256(row[0].encode()).hexdigest())
        sample = securities[480:840]
        if len(sample) != 360:
            raise ValueError('The registered 360-symbol universe is unavailable')
        symbols = [s for s, _ in sample]
        histories = {s: json.loads(p)['bars'] for s, p in con.execute(
            'SELECT symbol,payload FROM histories WHERE symbol IN (' + ','.join('?' for _ in symbols) + ')', symbols)}
        benchmark = json.loads(con.execute("SELECT payload FROM histories WHERE symbol='sh000001'").fetchone()[0])['bars']
    benchmark_dates = [r['date'] for r in benchmark]
    if benchmark_dates != sorted(set(benchmark_dates)):
        raise ValueError('Benchmark chronology is invalid')
    eligible_positions = [i for i, day in enumerate(benchmark_dates)
                          if '2025-06-02' <= day <= '2026-09-30' and i + 2 < len(benchmark_dates)
                          and benchmark_dates[i + 2] <= '2026-09-30']
    positions = eligible_positions[::10]
    if not positions:
        raise ValueError('Registered fixed date grid is unavailable')
    dates = [benchmark_dates[i] for i in positions]
    source_files = {name: hashlib.sha256((engine / name).read_bytes()).hexdigest()
                    for name in ('forecast.py', 'strategy.py')}
    output = {'protocol': EXPECTED_PROTOCOL, 'protocol_sha256': hashlib.sha256(protocol_bytes).hexdigest(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'engine_file_sha256': source_files, 'evaluation_started_at': started,
              'history_asof': benchmark_dates[-1], 'sample_symbols': symbols, 'signal_dates': dates,
              'financial_performance_tested': False, 'fee_stress_pct': COST,
              'selection_boundary': protocol['selection_boundary'],
              'unresolved_rule': protocol['unresolved_rule'],
              'limitations': protocol['limitations'], 'per_date': []}
    # Phase one contains no access to entry/exit prices or returns.
    frozen = []
    for finished, position in enumerate(positions, 1):
        day = benchmark_dates[position]
        benchmark_prefix = benchmark[:position + 1]
        pool, candidate, exclusions = [], [], Counter()
        for symbol, name in sample:
            prefix = [r for r in histories.get(symbol, []) if r.get('date', '') <= day]
            if len(prefix) < 120 or prefix[-1]['date'] != day or strategy._restricted(symbol, name):
                exclusions['history_or_current_name_restriction'] += 1
                continue
            risk = risk_profile(prefix, benchmark_prefix, day)
            if risk is None:
                exclusions['risk_or_price_integrity'] += 1
                continue
            signal = strategy._frame(prefix, symbol, name, benchmark_prefix)
            if signal['chase_risk'].get('gate_passed') is not True:
                exclusions['existing_chase_veto'] += 1
                continue
            item = {'symbol': symbol, 'raw_technical_score': signal['score'],
                    'risk_adjusted_technical_score': round(max(0, signal['score'] - risk['risk_penalty']), 3),
                    **risk}
            pool.append(item)
            passed, reason, evidence = confirmation(prefix, benchmark_prefix, day, forecast)
            exclusions[reason] += 1
            if passed:
                candidate.append({**item, 'confirmation': evidence})
        order = lambda item: (-item['risk_adjusted_technical_score'], item['atr_pct'], item['symbol'])
        pool.sort(key=order)
        candidate.sort(key=order)
        frozen.append({'date': day, 'entry_date': benchmark_dates[position + 1],
                       'exit_date': benchmark_dates[position + 2], 'baseline_pool': len(pool),
                       'candidate_pool': len(candidate), 'exclusions': dict(exclusions),
                       'selections': {name + '_' + str(top): [dict(item) for item in members[:top]]
                                      for name, members in (('baseline', pool), ('candidate', candidate))
                                      for top in (1, 5)}})
        print(f'selection_dates_completed={finished}/{len(positions)}', flush=True)
    output['selections_frozen_at'] = dt.datetime.now(TZ).isoformat()
    output['frozen_selection_sha256'] = hashlib.sha256(
        json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    # Phase two reads outcomes only after the whole immutable selection grid exists.
    groups = {name + '_' + str(top): [] for name in ('baseline', 'candidate') for top in (1, 5)}
    for frozen_date in frozen:
        record = {key: value for key, value in frozen_date.items() if key != 'selections'}
        index_return, index_issue = outcome(benchmark, record['entry_date'], record['exit_date'])
        record.update(index_return_pct=index_return, index_outcome_issue=index_issue, selections={})
        for key, selected in frozen_date['selections'].items():
            top = int(key.rsplit('_', 1)[1])
            items, unresolved = [], []
            for item in selected:
                value, issue = outcome(histories[item['symbol']], record['entry_date'], record['exit_date'])
                items.append({**item, 'net_return_pct': value, 'outcome_issue': issue})
                if issue:
                    unresolved.append({'symbol': item['symbol'], 'issue': issue})
            net = None if unresolved else sum(item['net_return_pct'] for item in items) / top
            record['selections'][key] = {'selected_count': len(items), 'cash_slots': top - len(items),
                                         'unresolved_slots': unresolved, 'net_return_pct': net, 'items': items}
            groups[key].append({'date': record['date'], 'net_return_pct': net,
                                'index_return_pct': index_return, 'selected_count': len(items),
                                'pool_size': record['candidate_pool'] if key.startswith('candidate') else record['baseline_pool']})
        output['per_date'].append(record)
    midpoint = len(dates) // 2
    output['summaries'] = {key: summarize(rows, int(key.rsplit('_', 1)[1])) for key, rows in groups.items()}
    output['chronological_halves'] = {
        'split_rule': 'first floor(N/2) fixed dates and remaining fixed dates; no performance-dependent split',
        'first_dates': dates[:midpoint], 'second_dates': dates[midpoint:],
        'first': {key: summarize(rows[:midpoint], int(key.rsplit('_', 1)[1])) for key, rows in groups.items()},
        'second': {key: summarize(rows[midpoint:], int(key.rsplit('_', 1)[1])) for key, rows in groups.items()}}
    both_worse, incomplete = {}, False
    for top in (1, 5):
        old, new = (output['summaries'][name + '_' + str(top)] for name in ('baseline', 'candidate'))
        if not old['complete_return_summary'] or not new['complete_return_summary']:
            incomplete = True
            both_worse[str(top)] = None
        else:
            both_worse[str(top)] = (new['mean_net_return_pct'] < old['mean_net_return_pct']
                                    and new['worst_decile_mean_pct'] < old['worst_decile_mean_pct'])
    output['deployment_gate'] = {'both_mean_and_tail_worse': both_worse,
                                'all_selected_outcomes_resolved': not incomplete,
                                'consistency_constraint_permitted': not incomplete and not any(both_worse.values()),
                                'validated_buy_model': False}
    output['evaluated_at'] = dt.datetime.now(TZ).isoformat()
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing fixed evaluation; no repeated run or outcome tuning')
    result = run(args.engine, args.database)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('evaluated_at', 'summaries', 'chronological_halves', 'deployment_gate')},
                     ensure_ascii=False, indent=2, allow_nan=False))
