import contextlib
import copy
import datetime as dt
import json
import sqlite3
import types
import unittest
from unittest.mock import patch

import core as real_core
import daily_watchlist as watch


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.moment = dt.datetime(2026, 10, 20, 10, 10, tzinfo=real_core.TZ)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        self.calls = []
        self.stocks = [self.stock(i) for i in range(7)]
        self.finance = {'as_of': '2026-10-19', 'fetched_at': '2026-10-19T16:00:00+08:00',
                        'stocks': [self.financial(i) for i in range(6)]}
        quotes = {row['symbol']: self.quote(row['symbol']) for row in self.stocks}
        self.put('valuation_quotes', {'as_of': '2026-10-19', 'fetched_at': '2026-10-19T16:00:00+08:00', 'quotes': quotes})
        self.service = types.SimpleNamespace(dashboard=self.dashboard, longterm=lambda: copy.deepcopy(self.finance))
        fake_core = types.SimpleNamespace(TZ=real_core.TZ, strategy=real_core.strategy,
            now=lambda: self.moment, getkv=self.get, putkv=self.put, connect=self.connect,
            encode=real_core.encode, quote_gate=real_core.quote_gate, exclusion=real_core.exclusion,
            Service=lambda: self.service)
        self.patches = [patch.object(watch, 'core', fake_core),
                        patch.object(watch.md, 'is_trading_day', side_effect=lambda day: self.day(day).weekday() < 5),
                        patch.object(watch.md, 'last_completed_session', side_effect=self.last_completed)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.db.close()

    def day(self, value):
        return dt.date.fromisoformat(value) if isinstance(value, str) else value

    def last_completed(self, moment):
        day = moment.date() if moment.hour >= 16 else moment.date() - dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        return day.isoformat()

    def stock(self, i):
        return {'symbol': f'sh600{i:03d}', 'name': f'样本企业{i}', 'date': '2026-10-19',
                'fetched_at': '2026-10-19T16:00:00+08:00',
                'stale': False, 'excluded': False, 'eligible': False, 'score': 80 + i,
                'bar_count': 400, 'atr14': .2, 'close': 10, 'raw_close': 10,
                'entry_low': 999, 'entry_high': 1000, 'stop_loss': 998, 'take_profit': 1001,
                'prediction': {'eligible': False, 'expected_return_pct': 99999 - i},
                'chase_risk': {'version': 'anti-chase-v1', 'as_of_date': '2026-10-19',
                    'blocked': False, 'gate_passed': True, 'status': 'clear', 'reasons': [],
                    'metrics': {'raw_price_complete': True, 'benchmark_aligned': True,
                                'return_5d_pct': 2, 'ma20_deviation_pct': 3},
                    'entry_ceiling': {'reference_date': '2026-10-19', 'price_basis': 'latest_raw_close_scaled_ma20', 'price': 10.2}}}

    def quote(self, symbol):
        return {'symbol': symbol, 'name': '样本企业', 'date': '2026-10-19',
                'datetime': '2026-10-19T16:00:00+08:00', 'price': 10, 'market_cap_100m': 100,
                'suspended': False, 'is_st': False, 'is_delisting': False}

    def financial(self, i):
        return {'symbol': f'sh600{i:03d}', 'date': '2026-10-19', 'quote_date': '2026-10-19',
                'score': 80, 'eligible': True, 'pe_ttm': 15, 'pb': 2, 'roe': 10,
                'revenue_growth': 8, 'profit_growth': 12, 'operating_cash_to_profit': 1,
                'recurring_profit_ratio': .8, 'debt_ratio': 40, 'ttm_parent_netprofit_100m': 2,
                'report_date': '2026-06-30', 'publication_date': '2026-08-20', 'provider_update_date': '2026-08-20'}

    def dashboard(self, target):
        self.calls.append(target)
        return {'stocks': copy.deepcopy(self.stocks), 'index': {'date': self.stocks[0]['date']}}

    @contextlib.contextmanager
    def connect(self):
        with self.db:
            yield self.db

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else copy.deepcopy(default)

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, json.dumps(value)))

    def build(self, **kwargs):
        return watch.build(service=self.service, now=self.moment, **kwargs)

    def symbols(self, result):
        return [item['symbol'] for item in result['items']]

    def test_unvalidated_models_can_be_observed_without_any_action_prices(self):
        result = self.build()
        self.assertEqual(result['status'], 'watch_only')
        self.assertEqual(result['data_date'], '2026-10-19')
        self.assertEqual(result['date'], '2026-10-20')
        self.assertEqual(len(result['items']), 5)
        self.assertIs(result['top_pick'], result['items'][0])
        self.assertEqual(result['published_at'], self.moment.isoformat())
        self.assertEqual(result['publication_timing'], 'late')
        self.assertFalse(result['model_validated'])
        for item in result['items']:
            self.assertFalse(item['eligible']); self.assertFalse(item['actionable'])
            self.assertTrue(all(item[key] is None for key in watch.PRICE_FIELDS))
        self.assertTrue(any('尚未完整核验' in warning for warning in result['warnings']))

    def test_fixed_composite_uses_complete_financial_pool_and_not_failed_predictions(self):
        result = self.build()
        self.assertEqual(self.symbols(result)[0], 'sh600005')
        self.assertNotIn('sh600006', self.symbols(result))
        self.assertAlmostEqual(result['top_pick']['score'], .6 * 85 + .4 * 80)
        for stock in self.stocks:
            stock['prediction']['expected_return_pct'] *= -100
        self.assertEqual(self.symbols(result), self.symbols(self.build()))

    def test_missing_financial_never_gets_filled_neutral_score(self):
        self.finance['stocks'] = self.finance['stocks'][:1]
        result = self.build()
        incomplete = next(item for item in result['items'] if not item['verification']['financial_complete'])
        self.assertIsNone(incomplete['components']['financial'])
        self.assertAlmostEqual(incomplete['score'], .6 * incomplete['components']['technical'])
        self.assertTrue(any('较低' in note for note in incomplete['risk_notes']))

    def test_hard_veto_stale_index_st_excluded_suspension_and_chase(self):
        mutations = [lambda: self.stocks[5].update(stale=True),
                     lambda: self.stocks[5].update(symbol='sh000001'),
                     lambda: self.stocks[5].update(name='*ST样本'),
                     lambda: self.stocks[5].update(excluded=True),
                     lambda: self.stocks[5]['chase_risk'].update(blocked=True),
                     lambda: self.stocks[5]['chase_risk'].update(version='old'),
                     lambda: self.stocks[5]['chase_risk']['metrics'].update(benchmark_aligned=False)]
        original = copy.deepcopy(self.stocks)
        for mutate in mutations:
            self.stocks = copy.deepcopy(original)
            mutate()
            self.assertNotIn('sh600005', self.symbols(self.build()))
        for changes in ({'suspended': True}, {'is_st': True}, {'is_delisting': True},
                        {'market_cap_100m': 5000}, {'price': 12}, {'is_st': None}):
            self.stocks = copy.deepcopy(original)
            quotes = self.get('valuation_quotes')
            quotes['quotes']['sh600005'].update(changes)
            self.put('valuation_quotes', quotes)
            self.assertNotIn('sh600005', self.symbols(self.build()))
            quotes['quotes']['sh600005'] = self.quote('sh600005')
            self.put('valuation_quotes', quotes)

    def test_known_negative_official_session_monitor_and_partial_ai_evidence_veto(self):
        for section in ('risk_exclusions', 'risk_monitoring', 'session_risks', 'ai'):
            self.put('morning_news', {})
            self.put('ai_review', {})
            event = {'symbol': 'sh600005', 'reason': '已知负向风险'}
            if section == 'ai':
                self.put('ai_review', {'valid': True, 'status': 'partial', 'date': '2026-10-20',
                                      'review': {'risk_vetoes': [event]}})
            else:
                self.put('morning_news', {section: [event] if section == 'risk_exclusions' else {'risk_exclusions': [event]}})
            self.assertNotIn('sh600005', self.symbols(self.build()), section)
            self.assertEqual(self.build()['verification']['ai_status'], 'unverified')

    def test_only_complete_current_ai_review_marks_actually_covered_symbols(self):
        review = {'protocol': watch.daily_plan.AI_PROTOCOL, 'status': 'complete', 'date': '2026-10-20',
                  'signal_date': '2026-10-19', 'completed_at': '2026-10-20T08:30:00+08:00',
                  'reviewed_symbols': ['sh600005'], 'risk_vetoes': []}
        receipt = {'valid': True, 'morning_qualified': True, 'protocol': watch.daily_plan.AI_PROTOCOL,
                   'status': 'complete', 'date': '2026-10-20', 'signal_date': '2026-10-19',
                   'checked_at': '2026-10-20T08:31:00+08:00', 'review': review}
        self.put('ai_review', receipt)
        result = self.build()
        self.assertEqual(result['verification']['ai_status'], 'partial')
        self.assertTrue(result['items'][0]['verification']['ai_reviewed'])
        self.assertFalse(result['items'][1]['verification']['ai_reviewed'])
        receipt['status'] = 'unavailable'
        self.put('ai_review', receipt)
        result = self.build()
        self.assertEqual(result['verification']['ai_status'], 'unverified')
        self.assertFalse(any(item['verification']['ai_reviewed'] for item in result['items']))

    def test_future_source_prices_financial_and_risks_are_not_read_as_current_evidence(self):
        self.finance['stocks'][5]['publication_date'] = '2026-10-21'
        result = self.build()
        self.assertNotIn('sh600005', self.symbols(result))  # Five other complete stocks exist.
        self.put('morning_news', {'as_of': '2026-10-21T08:00:00+08:00',
                                 'risk_exclusions': [{'symbol': 'sh600004', 'reason': '未来记录'}]})
        self.assertIn('sh600004', self.symbols(self.build()))
        self.stocks[4]['fetched_at'] = '2026-10-21T08:00:00+08:00'
        self.assertNotIn('sh600004', self.symbols(self.build()))

    def test_nonfinite_missing_and_duplicate_price_records_are_excluded(self):
        for key, value in (('score', True), ('score', float('nan')), ('atr14', float('inf')), ('bar_count', 1)):
            original = copy.deepcopy(self.stocks[5])
            self.stocks[5][key] = value
            self.assertNotIn('sh600005', self.symbols(self.build()))
            self.stocks[5] = original
        self.stocks.append(copy.deepcopy(self.stocks[5]))
        self.assertNotIn('sh600005', self.symbols(self.build()))

    def test_missing_source_times_are_never_called_complete_and_bad_financial_rows_are_safe(self):
        quotes = self.get('valuation_quotes')
        quotes.pop('fetched_at')
        self.put('valuation_quotes', quotes)
        self.assertEqual(self.build()['status'], 'insufficient_data')
        quotes['fetched_at'] = '2026-10-19T16:00:00+08:00'
        quotes['quotes']['sh600005'].pop('datetime')
        self.put('valuation_quotes', quotes)
        self.assertNotIn('sh600005', self.symbols(self.build()))
        self.stocks[4].pop('fetched_at')
        self.assertNotIn('sh600004', self.symbols(self.build()))
        for key, value in (('stocks', None), ('fetched_at', None)):
            original = copy.deepcopy(self.finance)
            self.finance[key] = value
            self.assertTrue(all(not item['verification']['financial_complete'] for item in self.build()['items']))
            self.finance = original

    def test_ineligible_financial_risks_and_values_remain_visible(self):
        row = self.finance['stocks'][5]
        row.update(eligible=False, roe=6.99, debt_ratio=85.85,
                   risks=['ROE不足', '营收下滑', '负债达到75%'])
        result = self.build()
        notes = ' '.join(result['top_pick']['risk_notes'])
        self.assertIn('负债达到75%', notes)
        self.assertIn('6.99%', notes)
        self.assertIn('85.85%', notes)
        self.assertTrue(result['top_pick']['verification']['financial_complete'])
        self.assertFalse(result['top_pick']['eligible'])

    def test_duplicate_financial_rows_do_not_depend_on_input_order(self):
        self.finance['stocks'].append(dict(self.finance['stocks'][5], score=100))
        before = self.build()
        self.finance['stocks'].reverse()
        after = self.build()
        self.assertEqual(self.symbols(before), self.symbols(after))
        self.assertNotIn('sh600005', self.symbols(before))

    def test_sort_is_deterministic_under_input_permutation(self):
        before = self.build()
        self.stocks.reverse()
        self.finance['stocks'].reverse()
        after = self.build()
        self.assertEqual(self.symbols(before), self.symbols(after))
        self.assertEqual([x['score'] for x in before['items']], [x['score'] for x in after['items']])

    def test_history_is_only_real_saved_snapshot_without_future_data_rebuild(self):
        result = self.build(date='2026-10-19')
        self.assertEqual(result['status'], 'historical_unavailable')
        self.assertEqual(self.calls, [])
        saved = watch._base(dt.date(2026, 10, 19), self.moment - dt.timedelta(days=1))
        saved.update(status='watch_only', published_at='2026-10-19T09:15:00+08:00')
        self.put(watch.KEY_PREFIX + '2026-10-19', saved)
        self.assertEqual(watch.read('2026-10-19', service=self.service, now=self.moment)['published_at'], saved['published_at'])
        self.assertEqual(self.calls, [])

    def test_future_next_session_cannot_use_unfinished_current_prices(self):
        result = self.build(date='2026-10-21')
        self.assertEqual(result['status'], 'insufficient_data')
        self.assertEqual(result['items'], [])
        self.assertIsNone(result['published_at'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.build(date='2026-10-22')['items'], [])

    def test_after_close_can_prepare_official_next_session_across_weekend(self):
        close = dt.datetime(2026, 10, 23, 17, 0, tzinfo=real_core.TZ)
        for stock in self.stocks:
            stock.update(date='2026-10-23', fetched_at=close.isoformat())
            stock['chase_risk']['as_of_date'] = '2026-10-23'
            stock['chase_risk']['entry_ceiling']['reference_date'] = '2026-10-23'
        for row in self.finance['stocks']:
            row.update(date='2026-10-23', quote_date='2026-10-23')
        self.finance.update(as_of='2026-10-23', fetched_at=close.isoformat())
        quotes = self.get('valuation_quotes')
        quotes.update(as_of='2026-10-23', fetched_at=close.isoformat())
        for row in quotes['quotes'].values():
            row.update(date='2026-10-23', datetime=close.isoformat())
        self.put('valuation_quotes', quotes)
        result = watch.publish('2026-10-26', service=self.service, now=close)
        self.assertEqual(result['status'], 'watch_only')
        self.assertEqual(result['data_date'], '2026-10-23')
        self.assertEqual(result['date'], '2026-10-26')
        self.assertEqual(result['release_at'], '2026-10-26T09:00:00+08:00')
        self.assertIsNone(result['published_at'])
        self.assertEqual(result['generated_at'], close.isoformat())
        self.assertEqual(result['publication_timing'], 'prepared')
        self.assertEqual(self.symbols(result), self.symbols(watch.read('2026-10-26', service=self.service, now=close)))

    def test_saved_preparation_can_release_when_short_lived_research_seed_is_missing(self):
        early = self.moment.replace(hour=8)
        prepared = watch.publish(service=self.service, now=early)
        missing = types.SimpleNamespace(dashboard=lambda target: {'stocks': [], 'index': {'date': target}},
                                        longterm=lambda: {'stocks': None})
        released = watch.publish(service=missing, now=self.moment)
        self.assertEqual(released['release_basis'], 'prepared_cohort_frozen_at_release')
        self.assertEqual(released['generated_at'], prepared['generated_at'])
        self.assertEqual(released['prepared_at'], early.isoformat())
        self.assertEqual(released['published_at'], self.moment.isoformat())
        self.assertEqual(self.symbols(released), self.symbols(prepared))
        self.assertIs(released['top_pick'], released['items'][0])
        self.assertEqual(watch.read(service=missing, now=self.moment)['published_at'], self.moment.isoformat())

    def test_prepared_release_refuses_malformed_evidence_but_withdraws_known_new_risk(self):
        early = self.moment.replace(hour=8)
        prepared = watch.publish(service=self.service, now=early)
        missing = types.SimpleNamespace(dashboard=lambda target: {'stocks': [], 'index': {'date': target}},
                                        longterm=lambda: {'stocks': None})
        key = watch.KEY_PREFIX + '2026-10-20'
        for change in (lambda row: row.update(data_date='2026-10-16'),
                       lambda row: row.update(generated_at='2026-10-21T08:00:00+08:00'),
                       lambda row: row['items'][0].update(entry_high=100),
                       lambda row: row['items'][0]['verification'].update(chase_passed=False),
                       lambda row: row['items'][0].update(score=float('nan'))):
            bad = copy.deepcopy(prepared)
            change(bad)
            self.put(key, bad)
            result = watch.publish(service=missing, now=self.moment)
            self.assertEqual(result['status'], 'insufficient_data')
            self.assertIsNone(result['published_at'])
        self.put(key, prepared)
        self.put('morning_news', {'risk_exclusions': [{'symbol': prepared['top_pick']['symbol'], 'reason': '今晨新风险'}]})
        result = watch.publish(service=missing, now=self.moment)
        self.assertEqual(result['status'], 'watch_only')
        self.assertEqual(result['published_at'], self.moment.isoformat())
        self.assertEqual(self.symbols(result), self.symbols(prepared))
        self.assertIn(prepared['top_pick']['symbol'], result['withdrawn_symbols'])
        self.assertEqual(self.get(key)['published_at'], self.moment.isoformat())

    def test_before_nine_prepares_and_at_nine_original_queue_freezes_with_risk_withdrawals(self):
        early = self.moment.replace(hour=8)
        prepared = watch.publish(service=self.service, now=early)
        self.assertIsNone(prepared['published_at'])
        self.assertEqual(prepared['publication_timing'], 'prepared')
        self.stocks[5]['chase_risk']['blocked'] = True
        released = watch.publish(service=self.service, now=self.moment)
        self.assertEqual(released['published_at'], self.moment.isoformat())
        self.assertEqual(released['prepared_at'], early.isoformat())
        self.assertEqual(self.symbols(prepared), self.symbols(released))
        self.assertEqual(prepared['top_pick']['symbol'], released['top_pick']['symbol'])
        self.assertIn('sh600005', released['withdrawn_symbols'])
        self.stocks.clear()
        again = watch.publish(service=self.service, now=self.moment + dt.timedelta(minutes=1))
        self.assertEqual(self.symbols(released), self.symbols(again))
        self.assertEqual(released['published_at'], again['published_at'])

    def test_post_release_risk_is_saved_monotonically_without_survivor_reranking(self):
        released = watch.publish(service=self.service, now=self.moment)
        original = self.get(watch.KEY_PREFIX + '2026-10-20')
        self.put('morning_news', {'risk_exclusions': [{'symbol': released['top_pick']['symbol'], 'reason': '新增官方风险'}]})
        read = watch.read(service=self.service, now=self.moment + dt.timedelta(minutes=5))
        self.assertEqual(self.symbols(read), self.symbols(released))
        self.assertEqual(read['top_pick']['symbol'], released['top_pick']['symbol'])
        self.assertIn(released['top_pick']['symbol'], read['withdrawn_symbols'])
        stored = self.get(watch.KEY_PREFIX + '2026-10-20')
        self.assertEqual(self.symbols(stored), self.symbols(original))
        self.assertIn(released['top_pick']['symbol'], stored['withdrawn_symbols'])
        self.put('morning_news', {})
        again = watch.read(service=self.service, now=self.moment + dt.timedelta(minutes=6))
        self.assertIn(released['top_pick']['symbol'], again['withdrawn_symbols'])
        self.assertEqual(again['alerts'], read['alerts'])
        historical = watch.read('2026-10-20', service=self.service, now=self.moment + dt.timedelta(days=1))
        self.assertIn(released['top_pick']['symbol'], historical['withdrawn_symbols'])

    def test_at_nine_new_scores_and_quotes_cannot_silently_replace_original_top_pick(self):
        early = self.moment.replace(hour=8)
        prepared = watch.publish(service=self.service, now=early)
        for stock in self.stocks:
            stock['score'] = 100 - stock['score']
        quotes = self.get('valuation_quotes')
        quotes['quotes'][prepared['top_pick']['symbol']]['suspended'] = True
        self.put('valuation_quotes', quotes)
        moment = self.moment.replace(hour=9, minute=0)
        released = watch.publish(service=self.service, now=moment, limit=1)
        self.assertEqual(self.symbols(prepared), self.symbols(released))
        self.assertEqual(prepared['top_pick']['score'], released['top_pick']['score'])
        self.assertEqual(released['published_at'], moment.isoformat())
        self.assertEqual(released['review_at'], moment.isoformat())
        self.assertIn(prepared['top_pick']['symbol'], released['withdrawn_symbols'])

    def test_closed_day_and_invalid_options_do_not_build_or_fake_releases(self):
        closed = self.build(date='2026-10-24')
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(self.calls, [])
        for limit in (0, 11, True):
            with self.assertRaises(ValueError): self.build(limit=limit)
        with self.assertRaises(ValueError): watch.build(now=self.moment.replace(tzinfo=None))

    def test_formal_daily_plan_is_not_changed_by_observation_publish(self):
        plan = {'date': '2026-10-20', 'status': 'published', 'buy': None, 'published_at': '2026-10-20T09:10:00+08:00'}
        self.put('daily_plan:2026-10-20', plan)
        watch.publish(service=self.service, now=self.moment)
        self.assertEqual(self.get('daily_plan:2026-10-20'), plan)


if __name__ == '__main__':
    unittest.main()
