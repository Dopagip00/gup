"""Run the preregistered fixed price-only audit; SQLite is strictly read-only.

This is an offline research artifact, never a published watchlist or buy model.
Usage: python research/evaluate_observation_risk.py --engine PATH --database PATH
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from statistics import mean


def summarize(rows):
    returns = [row['net_return_pct'] for row in rows]
    excess = [row['net_return_pct'] - row['index_return_pct'] for row in rows]
    count = max(1, math.ceil(len(returns) * .1))
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        drawdown = max(drawdown, (1 - equity / peak) * 100)
    return {'dates': len(rows), 'mean_net_return_pct': round(mean(returns), 4),
            'mean_excess_index_pct': round(mean(excess), 4),
            'worst_date_pct': round(min(returns), 4),
            'worst_decile_mean_pct': round(mean(sorted(returns)[:count]), 4),
            'synthetic_max_drawdown_pct': round(drawdown, 4),
            'mean_atr_pct': round(mean(row['atr_pct'] for row in rows), 4),
            'mean_downside_pct': round(mean(row['downside_pct'] for row in rows), 4)}


def run(engine, database):
    sys.path.insert(0, str(engine))
    import daily_watchlist as watch
    import strategy

    protocol_path = Path(__file__).with_name('observation-risk-v2-protocol.json')
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as con:
        securities = [(symbol, name) for symbol, name in con.execute('SELECT symbol,name FROM securities WHERE active=1')
                      if watch.SYMBOL.fullmatch(symbol)]
        securities.sort(key=lambda row: hashlib.sha256(row[0].encode()).hexdigest())
        sample = securities[:120]
        symbols = [symbol for symbol, _ in sample]
        histories = {symbol: json.loads(payload)['bars'] for symbol, payload in con.execute(
            'SELECT symbol,payload FROM histories WHERE symbol IN (' + ','.join('?' for _ in symbols) + ')', symbols)}
        benchmark = json.loads(con.execute("SELECT payload FROM histories WHERE symbol='sh000001'").fetchone()[0])['bars']
    dates = [row['date'] for row in benchmark][-242:-2:10]
    bench_map = {row['date']: row for row in benchmark}
    bench_dates = [row['date'] for row in benchmark]
    output = {'protocol': protocol['protocol'], 'protocol_sha256': hashlib.sha256(protocol_bytes).hexdigest(),
              'evaluated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
              'history_asof': benchmark[-1]['date'], 'sample_symbols': symbols, 'signal_dates': dates,
              'financial_performance_tested': False, 'fee_stress_pct': .30,
              'limitations': protocol['limitations'], 'per_date': [], 'summaries': {}}
    groups = {name + '_' + str(top): [] for name in ('baseline', 'candidate') for top in (1, 5)}
    for day in dates:
        index_position = bench_dates.index(day)
        entry_day, exit_day = bench_dates[index_position + 1:index_position + 3]
        benchmark_prefix = [row for row in benchmark if row['date'] <= day]
        index_return = (bench_map[exit_day]['raw_close'] / bench_map[entry_day]['raw_open'] - 1) * 100
        pool = []
        for symbol, name in sample:
            bars = histories.get(symbol, [])
            prefix = [row for row in bars if row['date'] <= day]
            future = {row['date']: row for row in bars if row['date'] in (entry_day, exit_day)}
            if (len(prefix) < 120 or prefix[-1]['date'] != day or strategy._restricted(symbol, name)
                    or any(date not in future for date in (entry_day, exit_day))):
                continue
            signal = strategy._frame(prefix, symbol, name, benchmark_prefix)
            if not signal['chase_risk']['gate_passed']:
                continue
            risk = watch._risk_profile(prefix, benchmark_prefix, day)
            if risk is None:
                continue
            opening, closing = future[entry_day].get('raw_open'), future[exit_day].get('raw_close')
            if not watch._number(opening, positive=True) or not watch._number(closing, positive=True):
                continue
            pool.append({'symbol': symbol, 'raw_score': signal['score'],
                         'adjusted_score': max(0, signal['score'] - risk['risk_penalty']),
                         'atr_pct': risk['atr_pct'], 'downside_pct': risk['downside_deviation_60d_pct'],
                         'net_return_pct': (closing / opening - 1) * 100 - .30})
        record = {'date': day, 'entry_date': entry_day, 'exit_date': exit_day, 'pool': len(pool),
                  'index_return_pct': round(index_return, 4), 'selections': {}}
        if len(pool) < 5:
            raise ValueError('Fixed signal date has fewer than five valid candidates: ' + day)
        for name, key in (('baseline', 'raw_score'), ('candidate', 'adjusted_score')):
            ordered = sorted(pool, key=lambda item: (-item[key], item['atr_pct'], item['symbol']))
            for top in (1, 5):
                chosen = ordered[:top]
                summary = {'date': day, 'net_return_pct': mean(item['net_return_pct'] for item in chosen),
                           'index_return_pct': index_return, 'atr_pct': mean(item['atr_pct'] for item in chosen),
                           'downside_pct': mean(item['downside_pct'] for item in chosen)}
                groups[name + '_' + str(top)].append(summary)
                record['selections'][name + '_' + str(top)] = {
                    'symbols': [item['symbol'] for item in chosen], 'net_return_pct': round(summary['net_return_pct'], 4)}
        output['per_date'].append(record)
    output['summaries'] = {key: summarize(rows) for key, rows in groups.items()}
    both_worse = {}
    for top in (1, 5):
        old, new = [output['summaries'][name + '_' + str(top)] for name in ('baseline', 'candidate')]
        both_worse[str(top)] = (new['mean_excess_index_pct'] < old['mean_excess_index_pct']
                               and new['worst_decile_mean_pct'] < old['worst_decile_mean_pct'])
    output['deployment_gate'] = {'both_mean_and_tail_worse': both_worse,
                                'risk_adjustment_permitted': not any(both_worse.values()),
                                'validated_buy_model': False}
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run(args.engine, args.database)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('history_asof', 'evaluated_at', 'summaries', 'deployment_gate')}, indent=2))
