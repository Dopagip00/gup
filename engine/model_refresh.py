"""Recalculate versioned predictions from cached bars, without market collection.

Only existing active-stock signals in the newest cached report can change.
Published daily cohorts, history payloads, securities, account data and morning
collection receipts are never written here. Computation happens before one
optimistic, atomic commit so readers keep seeing the previous complete set.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json


class RefreshConflict(RuntimeError):
    """The input snapshot changed while the new predictions were computed."""


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest() if value is not None else None


def _current(signal, date, version, protocol):
    prediction = signal.get('prediction')
    return bool(isinstance(prediction, dict) and signal.get('date') == date
                and prediction.get('as_of_date') == date
                and prediction.get('model_version') == version
                and prediction.get('validation_protocol') == protocol
                and signal.get('recompute_status') != 'error')


def _bars(history, date):
    if not isinstance(history, dict) or not isinstance(history.get('bars'), list):
        raise ValueError('缓存日线格式无效')
    result, seen = [], set()
    for bar in history['bars']:
        if not isinstance(bar, dict):
            raise ValueError('缓存日线记录格式无效')
        day = dt.date.fromisoformat(str(bar.get('date', ''))[:10]).isoformat()
        if day > date:
            continue
        if day in seen:
            raise ValueError('缓存日线日期重复')
        seen.add(day)
        result.append(dict(bar, date=day))
    result.sort(key=lambda bar: bar['date'])
    if not result or result[-1]['date'] != date:
        raise ValueError('缓存日线未覆盖原报告交易日')
    return result


def _disabled(old, symbol, name, date, stamp, version, protocol, reason):
    result = dict(old, symbol=symbol, name=name, eligible=False,
                  levels_status='research_only', max_position_pct=0,
                  recomputed_at=stamp, model_version=version, recompute_status='error')
    previous = old.get('prediction')
    # Do not relabel old point estimates, intervals or validation numbers as a
    # current-model result. A failed computation has no current numerical output,
    # including auxiliary next-day forecasts. Original report backups survive.
    prediction = {'available': False, 'eligible': False,
                  'model_version': version, 'validation_protocol': protocol,
                  'validation_protocol_complete': False, 'as_of_date': date,
                  'expected_return_pct': None, 'interval_low_pct': None,
                  'interval_high_pct': None, 'rank_value': None,
                  'raw_rank_value': None, 'reason': reason, 'warnings': [reason]}
    if isinstance(previous, dict) and isinstance(previous.get('model_version'), str):
        prediction['previous_model_version'] = previous['model_version']
    result['prediction'] = prediction
    result['warnings'] = list(dict.fromkeys(list(old.get('warnings', [])) + [reason]))
    return result


def refresh_cached_predictions(core, target=None):
    """Return a true recomputation receipt; never claim an 08:00 collection.

    ``target`` may assert the latest stored report date, but cannot request a
    rewrite of an earlier report. Per-stock failures are committed as disabled
    predictions in the same transaction as successful calculations. Snapshot
    conflicts or commit exceptions leave every original signal untouched.
    """
    started = core.now().isoformat()
    version = core.forecast.MODEL_VERSION
    protocol = core.forecast.VALIDATION_PROTOCOL
    receipt = {'status': 'noop', 'mode': 'cached_model_recompute',
               'model_version': version, 'validation_protocol': protocol,
               'started_at': started, 'report_date': None, 'target_date': None,
               'total': 0, 'active_total': 0, 'missing_signal_count': 0,
               'current': 0, 'attempted': 0, 'ok': 0, 'failed': 0,
               'updated': 0, 'recomputed': 0, 'committed': False, 'errors': [],
               'network_used': False, 'morning_collection': False,
               'note': '仅按原报告日期重算缓存模型；不是新行情采集、盘前研究完成或历史建议修订。'}

    def finish(status, reason=None):
        receipt['status'] = status
        receipt['finished_at'] = core.now().isoformat()
        if reason:
            receipt['reason'] = reason
        return receipt

    try:
        # A read transaction provides one stable source snapshot without taking
        # a write lock. WAL readers and market refreshes remain available.
        with core.connect() as con:
            con.execute('BEGIN')
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'").fetchone():
                return finish('noop', '没有缓存报告可重算')
            date = con.execute('SELECT MAX(report_date) FROM signals').fetchone()[0]
            if not date:
                return finish('noop', '没有缓存报告可重算')
            if dt.date.fromisoformat(str(date)).isoformat() != date:
                raise ValueError('缓存报告日期格式无效')
            receipt.update(report_date=date, target_date=date, original_data_date=date)
            if target is not None and str(target) != date:
                return finish('skipped', '指定日期不是最新缓存报告，保留全部原报告')
            securities = [tuple(row) for row in con.execute('SELECT symbol,name FROM securities WHERE active=1 ORDER BY symbol')]
            names = dict(securities)
            rows = {row[0]: row[1] for row in con.execute(
                "SELECT symbol,payload FROM signals WHERE report_date=? AND symbol<>'sh000001' "
                'AND symbol IN (SELECT symbol FROM securities WHERE active=1) ORDER BY symbol', (date,))}
            receipt.update(total=len(rows), active_total=len(securities),
                           missing_signal_count=sum(symbol != 'sh000001' and symbol not in rows for symbol in names))
            pending = []
            for symbol, raw in rows.items():
                old = json.loads(raw)
                if not isinstance(old, dict):
                    raise ValueError('缓存信号格式无效：' + symbol)
                if _current(old, date, version, protocol):
                    receipt['current'] += 1
                else:
                    pending.append((symbol, old))
            if not pending:
                return finish('noop', '最新缓存报告已使用当前模型和日期')
            receipt['attempted'] = len(pending)
            inputs, updates = {}, {}

            def history(symbol):
                row = con.execute('SELECT payload FROM histories WHERE symbol=?', (symbol,)).fetchone()
                raw = row[0] if row else None
                inputs[symbol] = _digest(raw)
                return json.loads(raw) if raw is not None else {'bars': []}

            benchmark_error = None
            try:
                benchmark = _bars(history('sh000001'), date)
            except Exception as exc:
                benchmark, benchmark_error = [], type(exc).__name__ + ': ' + str(exc)[:180]
            for symbol, old in pending:
                stamp = core.now().isoformat()
                try:
                    cached = history(symbol)
                    bars = _bars(cached, date)
                    if benchmark_error:
                        raise ValueError('基准缓存不可用：' + benchmark_error)
                    result = core.strategy.analyze(bars, symbol, names[symbol], benchmark_bars=benchmark)
                    result = core.enrich_signal(result, bars, benchmark)
                    # An existing issuer exclusion must not disappear simply
                    # because this offline operation has no fresh quote.
                    if old.get('excluded'):
                        result.update(excluded=True, eligible=False,
                                      exclusion_reason=old.get('exclusion_reason'))
                    result = core.gate_signal(result, date, True)
                    prediction = result.get('prediction')
                    if not isinstance(prediction, dict) or prediction.get('model_version') != version or prediction.get('validation_protocol') != protocol or prediction.get('as_of_date') != date or result.get('date') != date:
                        raise ValueError('重算模型版本、验证协议或日期不匹配')
                    for key in ('source', 'adjustment', 'fetched_at'):
                        result[key] = old[key] if key in old else cached.get(key)
                    result.update(recomputed_at=stamp, model_version=version, recompute_status='success')
                    updates[symbol] = core.encode(result)
                    receipt['ok'] += 1
                except Exception as exc:
                    reason = '缓存模型重算失败：' + type(exc).__name__ + ': ' + str(exc)[:200]
                    updates[symbol] = core.encode(_disabled(old, symbol, names[symbol], date, stamp, version, protocol, reason))
                    receipt['errors'].append({'symbol': symbol, 'error': reason})
                    receipt['failed'] += 1

        # A writer lock makes the final snapshot checks and all replacements
        # indivisible. Check unchanged rows too, as they defined the report set.
        with core.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            latest = con.execute('SELECT MAX(report_date) FROM signals').fetchone()[0]
            current_securities = [tuple(row) for row in con.execute('SELECT symbol,name FROM securities WHERE active=1 ORDER BY symbol')]
            current_rows = {row[0]: row[1] for row in con.execute(
                "SELECT symbol,payload FROM signals WHERE report_date=? AND symbol<>'sh000001' "
                'AND symbol IN (SELECT symbol FROM securities WHERE active=1) ORDER BY symbol', (date,))}
            if latest != date or current_securities != securities or current_rows != rows:
                raise RefreshConflict('最新报告、证券集合或原信号已被其他任务更新')
            for symbol, digest in inputs.items():
                row = con.execute('SELECT payload FROM histories WHERE symbol=?', (symbol,)).fetchone()
                if _digest(row[0] if row else None) != digest:
                    raise RefreshConflict('原日线缓存已被其他任务更新：' + symbol)
            con.executemany('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?',
                            [(payload, date, symbol) for symbol, payload in updates.items()])
        receipt.update(updated=len(updates), recomputed=len(updates), committed=True)
        return finish('success' if not receipt['failed'] and not receipt['missing_signal_count'] else 'partial' if receipt['ok'] else 'error')
    except RefreshConflict as exc:
        receipt['errors'].append({'symbol': None, 'error': str(exc)})
        return finish('conflict', '输入在计算期间发生变化；全部重算结果未写入')
    except Exception as exc:
        receipt['errors'].append({'symbol': None, 'error': type(exc).__name__ + ': ' + str(exc)[:240]})
        return finish('error', '缓存重算未完成；原信号未被替换')
