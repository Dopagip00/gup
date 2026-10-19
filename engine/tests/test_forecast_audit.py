import collections
import contextlib
import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import forecast_audit as audit


class FakeCalendar:
    def __init__(self):
        self.calls = collections.Counter()
        self.unavailable = set()

    def is_trading_day(self, day):
        day = dt.date.fromisoformat(day) if isinstance(day, str) else day
        self.calls[day] += 1
        if day in self.unavailable:
            raise ValueError('calendar unavailable')
        return day.weekday() < 5


class FakeCore:
    TZ = audit.TZ

    def __init__(self, directory):
        self.db = Path(directory) / 'audit.sqlite3'
        self.moment = dt.datetime(2026, 10, 22, 16, tzinfo=self.TZ)
        self.completed = '2026-10-22'
        self.md = FakeCalendar()
        self.histories = {}
        self.history_calls = collections.Counter()
        with self.connect() as con:
            con.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')

    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.db)
        try:
            with con:
                yield con
        finally:
            con.close()

    def now(self):
        return self.moment

    def expected_date(self):
        if isinstance(self.completed, Exception):
            raise self.completed
        return self.completed

    def load_history(self, symbol):
        self.history_calls[symbol] += 1
        return copy.deepcopy(self.histories.get(symbol, {'bars': []}))

    def save(self, plan, key=None):
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',
                        (key or 'daily_plan:' + plan['date'], json.dumps(plan, ensure_ascii=False)))


class ForecastAuditTests(unittest.TestCase):
    def setUp(self):
        self.test_root = (Path(__file__).resolve().parents[1] / 'backups').resolve()
        self.test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='forecast-audit-test-', dir=self.test_root)
        self.core = FakeCore(self.temp.name)
        self.symbol = 'sh600298'
        self.core.histories[self.symbol] = {'metadata': {'unused': 'large'}, 'bars': [
            {'date': '2026-10-16', 'open': 9, 'close': 10, 'raw_open': 18, 'raw_close': 20, 'volume': 100, 'unused': 'large'},
            {'date': '2026-10-19', 'open': 10, 'close': 10.5, 'raw_open': 20, 'raw_close': 21, 'volume': 100, 'unused': 'large'},
            {'date': '2026-10-20', 'open': 11, 'close': 11, 'raw_open': 22, 'raw_close': 21, 'volume': 100, 'unused': 'large'},
            {'date': '2026-10-21', 'open': 11, 'close': 12, 'raw_open': 22, 'raw_close': 24, 'volume': 100, 'unused': 'large'},
            {'date': '2026-10-22', 'open': 12, 'close': 14, 'raw_open': 24, 'raw_close': 28, 'volume': 100, 'unused': 'large'},
        ]}

    def tearDown(self):
        # Verify the resolved recursive-cleanup target stays inside this project.
        assert Path(self.temp.name).resolve().is_relative_to(self.test_root)
        self.temp.cleanup()

    def plan(self, date='2026-10-19', signal='2026-10-16', sell='2026-10-20', version='v2', expected=8):
        published = date + 'T09:00:00+08:00'
        return {'date': date, 'data_date': signal, 'status': 'published', 'morning_ready': True,
                'late': False, 'published_at': published,
                'research_forecasts': [{'symbol': self.symbol, 'name': 'Research only', 'signal_date': signal,
                    'buy_date': date, 'sell_date': sell, 'published_at': published, 'model_version': version,
                    'validation_protocol': 'fixed-test-protocol', 'expected_return_pct': expected,
                    'rank_value': -1.0, 'interval_low_pct': 0, 'interval_high_pct': 12,
                    'model_qualified': False, 'recommendation_qualified': False}],
                'buy': {'symbol': self.symbol, 'take_profit': 15, 'stop_loss': 8}}

    def stock(self, i=0):
        return {'symbol': f'sh{600000 + i}', 'name': str(i), 'date': '2026-10-16', 'eligible': False,
            'stale': False, 'excluded': False, 'prediction': {'available': True, 'eligible': False,
                'as_of_date': '2026-10-16', 'horizon_days': 2, 'target_kind': 'next_open_to_following_close',
                'model_version': 'v2', 'validation_protocol': 'fixed-test-protocol',
                'expected_return_pct': 2, 'raw_rank_value': i, 'interval_low_pct': -3, 'interval_high_pct': 4}}

    def test_double_price_basis_and_explicit_cost_stress_are_arithmetic_not_account_returns(self):
        self.core.save(self.plan())
        result = audit.evaluate(self.core)
        row = result['outcomes'][0]
        self.assertEqual(result['matured'], 1)
        self.assertEqual(row['adjusted_return_pct'], 10)
        self.assertEqual(row['nominal_price_return_pct'], 5)
        self.assertEqual(row['absolute_error_pct'], 2)
        self.assertEqual(row['adjusted_cost_stress_return_pct'], 9.7)
        self.assertEqual(row['nominal_cost_stress_return_pct'], 4.7)
        self.assertEqual(row['round_trip_cost_stress_pct'], 0.30)
        self.assertTrue(row['in_research_interval'])
        self.assertFalse(row['recommendation_qualified'])
        self.assertIn('不是实际税费', result['cost_stress_method'])

    def test_only_aware_same_day_real_0900_to_0929_publications_are_evidence(self):
        for changes in ({'published_at': '2026-10-19T09:00:00'},
                        {'published_at': '2026-10-19T08:59:59+08:00'},
                        {'published_at': '2026-10-19T09:30:00+08:00'},
                        {'published_at': '2026-10-20T09:00:00+08:00'},
                        {'published_at': '2026-10-19T09:00:00+00:00'},
                        {'morning_ready': False}, {'morning_ready': 1}, {'late': True}, {'status': 'pending'}):
            with self.subTest(changes=changes):
                plan = self.plan()
                plan.update(changes)
                plan['research_forecasts'][0]['published_at'] = plan['published_at']
                self.core.save(plan)
                self.assertEqual(audit.evaluate(self.core)['captured_forecasts'], 0)
        self.assertFalse(self.core.history_calls)

    def test_missing_or_mismatched_snapshot_is_never_backfilled_from_buy_or_current_model(self):
        for change in ('no_forecasts', 'bad_item_timestamp', 'missing_version', 'wrong_signal', 'wrong_key'):
            with self.subTest(change=change):
                with self.core.connect() as con:
                    con.execute('DELETE FROM kv')
                plan = self.plan()
                key = None
                if change == 'no_forecasts':
                    plan.pop('research_forecasts')
                elif change == 'bad_item_timestamp':
                    plan['research_forecasts'][0]['published_at'] = '2026-10-19T09:01:00+08:00'
                elif change == 'missing_version':
                    plan['research_forecasts'][0].pop('model_version')
                elif change == 'wrong_signal':
                    plan['research_forecasts'][0]['signal_date'] = '2026-10-15'
                else:
                    key = 'daily_plan:2026-10-20'
                self.core.save(plan, key)
                self.assertEqual(audit.evaluate(self.core)['captured_forecasts'], 0)
        self.assertFalse(self.core.history_calls)

    def test_future_d2_and_same_day_before_close_are_waiting_without_reading_history(self):
        self.core.save(self.plan())
        for moment, completed in (('2026-10-19T09:15:00+08:00', '2026-10-16'),
                                  ('2026-10-20T14:59:59+08:00', '2026-10-20'),
                                  ('2026-10-20T16:00:00+08:00', '2026-10-22')):
            self.core.moment = dt.datetime.fromisoformat(moment)
            self.core.completed = completed
            result = audit.evaluate(self.core)
            self.assertEqual(result['waiting'], 1)
            self.assertEqual(result['matured'], 0)
        self.assertFalse(self.core.history_calls)
        self.assertFalse(self.core.md.calls)

    def test_a_publication_after_current_time_cannot_be_treated_as_observed(self):
        self.core.save(self.plan())
        self.core.moment = dt.datetime(2026, 10, 19, 8, 59, tzinfo=audit.TZ)
        self.core.completed = '2026-10-16'
        self.assertEqual(audit.evaluate(self.core)['captured_forecasts'], 0)

    def test_exact_official_sessions_cross_weekend_without_calendar_day_shift(self):
        self.core.save(self.plan())
        self.assertEqual(audit.evaluate(self.core)['matured'], 1)
        self.assertIn(dt.date(2026, 10, 17), self.core.md.calls)
        self.assertIn(dt.date(2026, 10, 18), self.core.md.calls)
        self.assertTrue(all(count == 1 for count in self.core.md.calls.values()))

    def test_skipping_an_official_d1_or_d2_or_using_non_session_signal_is_rejected(self):
        for plan in (self.plan(sell='2026-10-21'),
                     self.plan(date='2026-10-20', sell='2026-10-21'),
                     self.plan(signal='2026-10-17')):
            with self.subTest(plan=plan):
                with self.core.connect() as con:
                    con.execute('DELETE FROM kv')
                self.core.save(plan)
                result = audit.evaluate(self.core)
                self.assertEqual(result['data_unavailable'], 1)
                self.assertEqual(result['matured'], 0)
        self.assertFalse(self.core.history_calls)

    def test_official_calendar_failure_is_preserved_and_never_falls_back_to_weekdays(self):
        self.core.md.unavailable.add(dt.date(2026, 10, 19))
        self.core.save(self.plan())
        result = audit.evaluate(self.core)
        self.assertEqual(result['data_unavailable'], 1)
        self.assertIn('日历', result['outcomes'][0]['reason'])
        self.assertFalse(self.core.history_calls)

    def test_missing_scheduled_day_does_not_use_later_available_stock_row(self):
        self.core.histories[self.symbol]['bars'] = [row for row in self.core.histories[self.symbol]['bars'] if row['date'] != '2026-10-20']
        self.core.save(self.plan())
        result = audit.evaluate(self.core)
        self.assertEqual(result['data_unavailable'], 1)
        self.assertEqual(result['matured'], 0)

    def test_zero_volume_missing_raw_and_invalid_numeric_legs_fail_closed(self):
        original = copy.deepcopy(self.core.histories[self.symbol])
        for index, field, value in ((1, 'volume', 0), (2, 'volume', 0), (1, 'raw_open', None),
                                    (2, 'raw_close', None), (1, 'open', float('nan')),
                                    (2, 'close', float('inf')), (1, 'open', True), (1, 'open', 1e-320)):
            with self.subTest(field=field, value=value):
                self.core.histories[self.symbol] = copy.deepcopy(original)
                self.core.histories[self.symbol]['bars'][index][field] = value
                self.core.save(self.plan())
                result = audit.evaluate(self.core)
                self.assertEqual(result['data_unavailable'], 1)
                self.assertEqual(result['matured'], 0)
                json.dumps(result, allow_nan=False)

    def test_duplicate_history_failure_is_cached_for_all_later_forecasts(self):
        for duplicated_index in (0, 1):
            with self.subTest(duplicated_index=duplicated_index):
                self.core.history_calls.clear()
                rows = self.core.histories[self.symbol]['bars'][:5]
                self.core.histories[self.symbol]['bars'] = rows + [copy.deepcopy(rows[duplicated_index])]
                self.core.save(self.plan())
                self.core.save(self.plan('2026-10-20', '2026-10-19', '2026-10-21'))
                result = audit.evaluate(self.core)
                self.assertEqual(result['data_unavailable'], 2)
                self.assertEqual(result['matured'], 0)
                self.assertTrue(all('重复' in row['reason'] for row in result['outcomes']))
                self.assertEqual(self.core.history_calls[self.symbol], 1)

    def test_each_symbol_is_loaded_once_and_cache_retains_only_needed_price_fields(self):
        self.core.save(self.plan())
        self.core.save(self.plan('2026-10-20', '2026-10-19', '2026-10-21'))
        result = audit.evaluate(self.core)
        self.assertEqual(result['matured'], 2)
        self.assertEqual(self.core.history_calls[self.symbol], 1)
        small = audit._load_needed_history(self.core, self.symbol, {'2026-10-19'})
        self.assertEqual(set(small), {'2026-10-19'})
        self.assertEqual(set(small['2026-10-19']), {'open', 'close', 'raw_open', 'raw_close', 'volume'})

    def test_only_same_model_versions_share_a_summary(self):
        self.core.save(self.plan(version='v1'))
        self.core.save(self.plan('2026-10-20', '2026-10-19', '2026-10-21', 'v2', 20))
        self.core.save(self.plan('2026-10-21', '2026-10-20', '2026-10-22', 'v2', 10))
        result = audit.evaluate(self.core)
        self.assertEqual(set(result['by_model_version']), {'v1', 'v2'})
        self.assertEqual(result['by_model_version']['v1']['samples'], 1)
        self.assertEqual(result['by_model_version']['v1']['mae_pct'], 2)
        self.assertEqual(result['by_model_version']['v2']['samples'], 2)
        self.assertNotIn('mae_pct', result)

    def test_binary_and_three_sign_direction_metrics_are_distinct_on_zero_return(self):
        self.core.histories[self.symbol]['bars'][2]['close'] = 10
        self.core.save(self.plan(expected=-1))
        result = audit.evaluate(self.core)
        row = result['outcomes'][0]
        self.assertTrue(row['direction_correct'])
        self.assertFalse(row['direction_three_sign_correct'])
        self.assertEqual(result['by_model_version']['v2']['direction_accuracy_pct'], 100)
        self.assertEqual(result['by_model_version']['v2']['three_sign_direction_accuracy_pct'], 0)

    def test_snapshot_ranks_top_twenty_unique_including_unqualified_without_mutation(self):
        stocks = [self.stock(i) for i in range(30)]
        stocks += [copy.deepcopy(stocks[-1])]
        excluded = self.stock(99)
        excluded['excluded'] = True
        stocks.append(excluded)
        before = copy.deepcopy(stocks)
        rows = audit.snapshot(stocks, '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-19T09:00:00+08:00')
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]['rank_value'], 29)
        self.assertEqual(rows[-1]['rank_value'], 10)
        self.assertEqual(len({row['symbol'] for row in rows}), 20)
        self.assertTrue(all(not row['model_qualified'] for row in rows))
        self.assertEqual(stocks, before)

    def test_snapshot_rejects_missing_evidence_bad_dates_and_invalid_prediction_numbers(self):
        for timestamp in ('2026-10-19T09:00:00', '2026-10-18T09:00:00+08:00',
                          '2026-10-19T08:59:00+08:00', '2026-10-19T09:30:00+08:00'):
            self.assertEqual(audit.snapshot([self.stock()], '2026-10-16', '2026-10-19', '2026-10-20', timestamp), [])
        for change in ({'model_version': None}, {'expected_return_pct': float('nan')}, {'raw_rank_value': True}, {'horizon_days': 1}):
            stock = self.stock()
            stock['prediction'].update(change)
            self.assertEqual(audit.snapshot([stock], '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-19T09:00:00+08:00'), [])

    def test_recent_120_published_plan_window_is_explicit_and_older_archives_remain(self):
        start = dt.date(2025, 1, 1)
        for i in range(121):
            date = (start + dt.timedelta(days=i)).isoformat()
            self.core.save({'date': date, 'status': 'published', 'morning_ready': False})
        for i in range(20):
            date = (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
            self.core.save({'date': date, 'status': 'pending'})
        with self.core.connect() as con:
            con.execute('INSERT INTO kv VALUES(?,?)', ('daily_plan:2026-09-01', 'not json'))
        result = audit.evaluate(self.core)
        window = result['plan_window']
        self.assertEqual(window['limit'], 120)
        self.assertEqual(window['published_plans_considered'], 120)
        self.assertEqual(window['oldest_date'], '2025-01-02')
        self.assertEqual(window['newest_date'], (start + dt.timedelta(days=120)).isoformat())
        with self.core.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM kv').fetchone()[0], 142)

    def test_per_plan_cap_and_duplicate_items_cannot_inflate_the_public_sample(self):
        plan = self.plan()
        original = plan['research_forecasts'][0]
        plan['research_forecasts'] = [dict(original, symbol=f'sh{600000 + i}') for i in range(30)]
        plan['research_forecasts'][1] = copy.deepcopy(plan['research_forecasts'][0])
        self.core.save(plan)
        self.core.completed = None
        result = audit.evaluate(self.core)
        self.assertEqual(result['captured_forecasts'], 19)
        self.assertFalse(self.core.history_calls)

    def test_anomalous_cycle_span_is_rejected_before_calendar_or_history_work(self):
        self.core.save(self.plan(sell='2027-10-20'))
        result = audit.evaluate(self.core)
        self.assertEqual(result['data_unavailable'], 1)
        self.assertIn('31', result['outcomes'][0]['reason'])
        self.assertFalse(self.core.history_calls)
        self.assertFalse(self.core.md.calls)

    def test_evaluation_never_rewrites_original_advice_or_research_snapshots(self):
        self.core.save(self.plan())
        before_history = copy.deepcopy(self.core.histories)
        with self.core.connect() as con:
            before = con.execute('SELECT key,value FROM kv ORDER BY key').fetchall()
        audit.evaluate(self.core)
        audit.evaluate(self.core)
        with self.core.connect() as con:
            self.assertEqual(con.execute('SELECT key,value FROM kv ORDER BY key').fetchall(), before)
        self.assertEqual(self.core.histories, before_history)


if __name__ == '__main__':
    unittest.main()
