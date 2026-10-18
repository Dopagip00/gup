"""Refresh dated valuations/fundamentals, with explicit partial coverage."""
from __future__ import annotations
import argparse
import logging
import core
import longterm
import market_data as md


def refresh_fundamentals():
    target = core.expected_date()
    with core.connect() as con:
        universe = [dict(r) for r in con.execute('SELECT symbol,name FROM securities WHERE active=1 ORDER BY symbol')]
    quotes, errors = {}, []
    for offset in range(0, len(universe), 40):
        batch = [s['symbol'] for s in universe[offset:offset+40]]
        try:
            quotes.update(md.fetch_quotes(batch))
        except Exception as exc:
            errors.append({'symbols': batch, 'error': str(exc)[:200]})
    core.putkv('valuation_quotes', {'as_of': target, 'quotes': quotes, 'errors': errors, 'fetched_at': core.now().isoformat()})
    with core.connect() as con:
        rows = con.execute('SELECT symbol,payload FROM signals WHERE report_date=? AND symbol<>\'sh000001\'', (target,)).fetchall()
        for row in rows:
            import json
            signal = json.loads(row['payload'])
            why = core.exclusion(row['symbol'], signal.get('name', ''), quotes.get(row['symbol']))
            signal.update(excluded=bool(why), exclusion_reason=why)
            if why:
                signal['eligible'] = False
                signal['signal'] = '权重股 · 仅供参考'
            con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?', (core.encode(signal), target, row['symbol']))
    result = longterm.refresh(universe, quotes, target)
    if errors:
        result['warnings'].insert(0, f'{sum(len(x["symbols"]) for x in errors)}只股票报价批次失败，未用旧估值代替。')
    result['quote_coverage'] = {'total': len(universe), 'received': len(quotes), 'failed_batches': len(errors)}
    result['fetched_at'] = core.now().isoformat()
    core.putkv('longterm', result)
    return result


if __name__ == '__main__':
    core.init_db()
    result = refresh_fundamentals()
    print(core.encode({'as_of': result['as_of'], 'coverage': result['coverage'], 'quote_coverage': result['quote_coverage']}))
