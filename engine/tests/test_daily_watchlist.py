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
        self.histories = {}
        self.stocks = [self.stock(i) for i in range(7)]
        self.finance = {'as_of': '2026-10-19', 'fetched_at': '2026-10-19T16:00:00+08:00',
                        'stocks': [self.financial(i) for i in range(6)]}
        quotes = {row['symbol']: self.quote(row['symbol']) for row in self.stocks}
        self.put('valuation_quotes', {'as_of': '2026-10-19', 'fetched_at': '2026-10-19T16:00:00+08:00', 'quotes': quotes})
        self.service = types.SimpleNamespace(dashboard=self.dashboard, longterm=lambda: copy.deepcopy(self.finance))
        fake_core = types.SimpleNamespace(TZ=real_core.TZ, strategy=real_core.strategy,
            now=lambda: self.moment, getkv=self.get, putkv=self.put, connect=self.connect,
            encode=real_core.encode, quote_gate=real_core.quote_gate, exclusion=real_core.exclusion,
            Service=lambda: self.service, load_history=self.history)
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

    def history(self, symbol):
        if symbol in self.histories:
            return copy.deepcopy(self.histories[symbol])
        end = self.day(self.stocks[0]['date'])
        dates=[]
        while len(dates)<120:
            if end.weekday()<5:dates.append(end.isoformat())
            end-=dt.timedelta(days=1)
        return {'bars':[{'date':day,'open':10,'high':10.1,'low':9.9,'close':10,
                         'raw_close':10,'volume':100000} for day in reversed(dates)]}

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
        self.assertEqual(result['top_pick']['components']['technical_raw'],85)
        self.assertAlmostEqual(result['top_pick']['score'], .6 * result['top_pick']['components']['technical'] + .4 * 80)
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

    def intraday_quotes(self):
        quotes = self.get('valuation_quotes')
        quotes.update(as_of='2026-10-20', fetched_at=self.moment.isoformat())
        for row in quotes['quotes'].values():
            row.update(date='2026-10-20', datetime=self.moment.replace(minute=9).isoformat(),
                       fetched_at=self.moment.isoformat(), previous_close=10, price=8.5,
                       source='https://qt.gtimg.cn/')
        self.put('valuation_quotes', quotes)
        return quotes

    def test_intraday_previous_close_corrobation_is_only_an_unqualified_observation(self):
        self.intraday_quotes()
        result = self.build()
        self.assertEqual(result['status'], 'watch_only')
        self.assertEqual(result['published_at'], self.moment.isoformat())
        self.assertEqual(result['publication_timing'], 'late')
        self.assertEqual(result['data_date'], '2026-10-19')
        for item in result['items']:
            evidence = item['verification']
            self.assertEqual(evidence['quote_basis'], 'next_session_previous_close')
            self.assertEqual(evidence['quote_date'], '2026-10-20')
            self.assertEqual(evidence['price_reference_date'], '2026-10-19')
            self.assertEqual(evidence['quote_source'], 'https://qt.gtimg.cn/')
            self.assertFalse(evidence['financial_complete'])
            self.assertIsNone(item['components']['financial'])
            self.assertFalse(item['actionable']); self.assertFalse(item['model_validated'])
            self.assertTrue(all(item[key] is None for key in watch.PRICE_FIELDS))
        self.assertTrue(any('盘中迟到' in message for message in result['warnings']))
        self.assertFalse(real_core.quote_gate(self.stocks[0], self.get('valuation_quotes')['quotes']['sh600000'], '2026-10-19')['quote_valid'])

    def test_intraday_previous_close_requires_explicit_watch_only_and_after_nine_thirty(self):
        quotes = self.intraday_quotes()
        quote = quotes['quotes']['sh600005']
        self.assertIsNone(watch.observation_quote_evidence(self.stocks[5], quote, quotes, '2026-10-19', self.moment))
        early = self.moment.replace(hour=9, minute=29)
        quotes['fetched_at'] = early.isoformat()
        for row in quotes['quotes'].values():
            row.update(datetime=early.isoformat(), fetched_at=early.isoformat())
        self.put('valuation_quotes', quotes)
        self.assertEqual(watch.build(service=self.service, now=early)['items'], [])
        now = early.replace(minute=30)
        self.assertIsNotNone(watch.observation_quote_evidence(self.stocks[5], quote, quotes, '2026-10-19', now, observation_only=True))

    def test_intraday_previous_close_rejects_missing_future_nonadjacent_and_risky_quotes(self):
        original = self.intraday_quotes()
        mutations = ({'previous_close': None}, {'previous_close': True}, {'previous_close': float('nan')},
                     {'previous_close': 10.03}, {'is_st': True}, {'is_delisting': True}, {'suspended': True},
                     {'market_cap_100m': 5000}, {'date': '2026-10-21'},
                     {'datetime': '2026-10-20T10:11:00+08:00'}, {'fetched_at': '2026-10-20T10:11:00+08:00'},
                     {'datetime': '2026-10-20T10:09:00'}, {'fetched_at': None}, {'source': 'https://example.com/'})
        for changes in mutations:
            quotes = copy.deepcopy(original)
            quotes['quotes']['sh600005'].update(changes)
            self.put('valuation_quotes', quotes)
            self.assertNotIn('sh600005', self.symbols(self.build()), changes)
        self.put('valuation_quotes', original)
        with patch.object(watch.md, 'last_completed_session', return_value='2026-10-16'):
            self.assertIsNone(watch.observation_quote_evidence(self.stocks[5], original['quotes']['sh600005'], original,
                                                             '2026-10-19', self.moment, observation_only=True))
        with patch.object(watch.md, 'is_trading_day', return_value=False):
            self.assertIsNone(watch.observation_quote_evidence(self.stocks[5], original['quotes']['sh600005'], original,
                                                             '2026-10-19', self.moment, observation_only=True))

    def test_intraday_current_quote_rechecks_do_not_withdraw_or_rewrite_frozen_same_day_evidence(self):
        early = self.moment.replace(hour=8)
        prepared = watch.publish(service=self.service, now=early)
        original = copy.deepcopy(prepared['items'])
        self.intraday_quotes()
        released = watch.publish(service=self.service, now=self.moment)
        self.assertEqual(released['items'], original)
        self.assertEqual(released['withdrawn_symbols'], [])
        for item in released['items']:
            self.assertEqual(item['verification']['quote_date'], '2026-10-19')
            current = released['current_risk_verification']['quotes'][item['symbol']]
            self.assertEqual(current['quote_date'], '2026-10-20')
            self.assertEqual(current['quote_basis'], 'next_session_previous_close')
        quotes = self.get('valuation_quotes')
        quotes['quotes'][released['top_pick']['symbol']]['suspended'] = True
        self.put('valuation_quotes', quotes)
        withdrawn = watch.read(service=self.service, now=self.moment + dt.timedelta(minutes=1))
        self.assertIn(released['top_pick']['symbol'], withdrawn['withdrawn_symbols'])
        self.assertEqual(withdrawn['items'], original)
        self.put('valuation_quotes', {})
        historical = watch.read('2026-10-20', service=self.service, now=self.moment + dt.timedelta(days=1))
        self.assertEqual(historical['items'], original)

    def test_auction_date_switch_does_not_withdraw_original_cohort_but_st_still_does(self):
        prepared = watch.publish(service=self.service, now=self.moment.replace(hour=8))
        original = copy.deepcopy(prepared['items'])
        auction = self.moment.replace(hour=9, minute=15)
        quotes = self.intraday_quotes()
        quotes['fetched_at'] = auction.isoformat()
        for quote in quotes['quotes'].values():
            quote.update(datetime=auction.isoformat(), fetched_at=auction.isoformat())
        self.put('valuation_quotes', quotes)
        released = watch.publish(service=self.service, now=auction)
        self.assertEqual(released['items'], original)
        self.assertEqual(released['withdrawn_symbols'], [])
        self.assertEqual(released['top_pick']['symbol'], prepared['top_pick']['symbol'])
        evidence = released['current_risk_verification']['quotes'][prepared['top_pick']['symbol']]
        self.assertEqual(evidence['quote_basis'], 'auction_previous_close_unverified')
        self.assertFalse(evidence['quote_consistent'])
        self.assertTrue(any('竞价报价不用于重排' in text for text in released['warnings']))
        self.assertEqual(watch.build(service=self.service, now=auction)['items'], [])
        quotes['quotes'][prepared['top_pick']['symbol']]['is_st'] = True
        self.put('valuation_quotes', quotes)
        withdrawn = watch.read(service=self.service, now=auction + dt.timedelta(minutes=1))
        self.assertIn(prepared['top_pick']['symbol'], withdrawn['withdrawn_symbols'])
        self.assertEqual(withdrawn['items'], original)

    def historical_finance(self):
        self.finance.update(as_of='2026-10-16',fetched_at='2026-10-16T17:00:00+08:00')
        for row in self.finance['stocks']:
            row.update(date='2026-10-16',quote_date='2026-10-16',
                       roe_report_date='2025-12-31',roe_publication_date='2026-04-20')

    def test_disclosed_quality_recomputed_without_stale_valuation_or_total_score(self):
        self.historical_finance()
        result=self.build()
        self.assertEqual(result['coverage']['disclosed_quality_financial'],6)
        self.assertEqual(result['coverage']['complete_financial'],0)
        self.assertNotIn('sh600006',self.symbols(result))
        item=result['top_pick']; verification=item['verification']
        self.assertFalse(verification['financial_complete'])
        self.assertTrue(verification['financial_quality_available'])
        self.assertEqual(verification['financial_data_date'],'2026-10-16')
        self.assertIsNone(verification['financial_valuation_date'])
        self.assertEqual(verification['financial_evidence_basis'],'cached_disclosed_quality_without_valuation')
        self.assertAlmostEqual(item['components']['financial'],round(42/65*100,3))
        for row in self.finance['stocks']:row.update(pe_ttm=999999,pb=-100,score=0)
        after=self.build()
        self.assertEqual(self.symbols(result),self.symbols(after))
        self.assertEqual(item['score'],after['top_pick']['score'])
        self.assertTrue(any('估值待核验' in note for note in item['risk_notes']))

    def test_quality_dates_nonfinite_profit_and_missing_roe_provenance_are_not_filled(self):
        self.historical_finance()
        original=copy.deepcopy(self.finance['stocks'][5])
        mutations=({'publication_date':'2026-10-20'},{'provider_update_date':'2026-10-20'},
                   {'roe_publication_date':'2026-10-20'},{'roe_report_date':'2026-06-30'},
                   {'roe_publication_date':None},{'report_date':'2025-12-31'},
                   {'roe':float('nan')},{'ttm_parent_netprofit_100m':-1},{'debt_ratio':101})
        for change in mutations:
            self.finance['stocks'][5]=dict(original,**change)
            result=self.build()
            self.assertNotIn('sh600005',self.symbols(result),change)
        self.finance['stocks'][5]=original
        self.finance['fetched_at']='2026-10-21T08:00:00+08:00'
        self.assertFalse(any(item['verification']['financial_quality_available'] for item in self.build()['items']))

    def test_low_base_growth_warning_caps_quality_growth_without_reusing_old_total(self):
        self.historical_finance()
        row=self.finance['stocks'][5]
        row.update(revenue_growth=1000,profit_growth=1000,risks=['上年同期利润基数为零或亏损；增长可能失真'])
        score=watch._disclosed_quality(row,self.finance,'2026-10-19',self.moment)
        self.assertAlmostEqual(score,round(40/65*100,3))

    def test_quality_preparation_releases_with_original_finance_date_and_frozen_ranking(self):
        self.historical_finance()
        early=self.moment.replace(hour=8)
        prepared=watch.publish(service=self.service,now=early)
        self.finance['stocks'][5]['roe']=0
        released=watch.publish(service=self.service,now=self.moment)
        self.assertEqual(prepared['items'],released['items'])
        self.assertEqual(released['release_basis'],'prepared_cohort_frozen_at_release')
        self.assertEqual(released['top_pick']['verification']['financial_data_date'],'2026-10-16')

    def test_new_policy_rebuilds_only_unpublished_old_preparation(self):
        early=self.moment.replace(hour=8)
        prepared=watch.publish(service=self.service,now=early)
        key=watch.KEY_PREFIX+'2026-10-20'
        prepared['ranking_policy']='old-policy'
        self.put(key,prepared)
        self.stocks[5]['score']=70
        rebuilt=watch.publish(service=self.service,now=self.moment)
        self.assertEqual(rebuilt['ranking_policy'],watch.RANKING_POLICY)
        self.assertNotEqual(prepared['items'],rebuilt['items'])
        frozen=copy.deepcopy(rebuilt);frozen['ranking_policy']='old-published-policy'
        self.put(key,frozen)
        for row in self.stocks:row['score']=0
        again=watch.publish(service=self.service,now=self.moment+dt.timedelta(minutes=1))
        self.assertEqual(again['items'],frozen['items'])
        self.assertEqual(again['ranking_policy'],'old-published-policy')
        self.assertEqual(again['published_at'],frozen['published_at'])

    def test_risk_profile_uses_only_aligned_prefix_and_liquidity_lots(self):
        bars=self.history('sh600000')['bars'];index=copy.deepcopy(bars)
        profile=watch._risk_profile(bars,index,'2026-10-19')
        self.assertEqual(profile['risk_penalty'],4)
        self.assertEqual(profile['liquidity_proxy_20d_cny'],100000000)
        self.assertEqual(profile['return_samples'],60)
        future=dict(bars[-1],date='2026-10-20',close=100,high=100,volume=1)
        self.assertEqual(profile,watch._risk_profile(bars+[future],index+[future],'2026-10-19'))
        self.assertIsNone(watch._risk_profile(bars[:-1],index,'2026-10-19'))
        self.assertIsNone(watch._risk_profile(bars,index[:-2]+index[-1:],'2026-10-19'))
        self.assertIsNone(watch._risk_profile(None,index,'2026-10-19'))
        broken=copy.deepcopy(bars);broken[-1]['volume']=0
        self.assertIsNone(watch._risk_profile(broken,index,'2026-10-19'))

    def test_fixed_risk_penalty_deprioritizes_downside_volatility_and_weak_liquidity(self):
        low=watch._risk_penalty(2,.5,5,50000000,'normal')
        self.assertGreater(watch._risk_penalty(4,.5,5,50000000,'normal'),low)
        self.assertGreater(watch._risk_penalty(2,2,5,50000000,'normal'),low)
        self.assertGreater(watch._risk_penalty(2,.5,20,50000000,'normal'),low)
        self.assertGreater(watch._risk_penalty(2,.5,5,1000000,'normal'),low)
        self.assertGreater(watch._risk_penalty(2,.5,5,50000000,'defensive'),low)
        self.assertEqual(watch._risk_penalty(50,50,50,1,'defensive'),30)

    def test_risk_history_failure_excludes_candidate_instead_of_filling_neutral_risk(self):
        self.histories['sh600005']={'bars':[]}
        result=self.build()
        self.assertNotIn('sh600005',self.symbols(result))
        self.assertEqual(result['coverage']['excluded_counts']['risk_history_missing'],1)

    def test_risk_history_invalid_dates_and_cached_close_mismatch_fail_safely(self):
        bars=self.history('sh600005')['bars'];index=copy.deepcopy(bars)
        bad=copy.deepcopy(bars);bad[-2]['date']='2026-10-18-invalid'
        self.assertIsNone(watch._risk_profile(bad,index,'2026-10-19'))
        for field,value in (('volume',float('inf')),('raw_close',float('nan')),('high',9)):
            bad=copy.deepcopy(bars);bad[-1][field]=value
            self.assertIsNone(watch._risk_profile(bad,index,'2026-10-19'))
        self.stocks[5]['close']=9.99
        self.assertNotIn('sh600005',self.symbols(self.build()))

    def test_complete_then_historical_quality_then_missing_evidence_tiers(self):
        self.historical_finance()
        old=copy.deepcopy(self.finance)
        self.finance.update(as_of='2026-10-19',fetched_at='2026-10-19T17:00:00+08:00')
        # A snapshot has one valuation day. Per-record old quotes cannot be
        # upgraded by mixing them into that snapshot; incomplete remains missing.
        self.finance['stocks'][0]=self.financial(0)
        self.assertTrue(self.build()['items'][0]['verification']['financial_complete'])
        self.finance=old
        self.finance['stocks']=self.finance['stocks'][:1]
        self.stocks[0]['score']=50
        result=self.build()
        self.assertEqual(result['top_pick']['symbol'],'sh600000')
        self.assertTrue(result['top_pick']['verification']['financial_quality_available'])
        self.assertFalse(result['items'][1]['verification']['financial_quality_available'])

    def test_tampered_risk_arithmetic_or_future_quality_cannot_release_without_source(self):
        self.historical_finance()
        prepared=watch.publish(service=self.service,now=self.moment.replace(hour=8))
        key=watch.KEY_PREFIX+'2026-10-20'
        for mutate in (lambda value:value['items'][0]['components'].update(risk_penalty=0),
                       lambda value:value['items'][0]['verification'].update(risk_window_end='2026-10-20'),
                       lambda value:value['items'][0]['verification'].update(financial_provider_update_date='2026-10-20'),
                       lambda value:value['items'][0]['verification'].update(financial_roe_publication_date='2026-10-20')):
            changed=copy.deepcopy(prepared);mutate(changed);self.put(key,changed)
            missing=types.SimpleNamespace(dashboard=lambda target:{'stocks':[],'index':{'date':target}},longterm=lambda:{})
            result=watch.publish(service=missing,now=self.moment)
            self.assertEqual(result['status'],'insufficient_data')
            self.assertIsNone(result['published_at'])

    def test_complete_preparation_cannot_release_future_provider_update(self):
        prepared=watch.publish(service=self.service,now=self.moment.replace(hour=8))
        prepared['items'][0]['verification']['financial_provider_update_date']='2026-10-20'
        self.put(watch.KEY_PREFIX+'2026-10-20',prepared)
        missing=types.SimpleNamespace(dashboard=lambda target:{'stocks':[],'index':{'date':target}},longterm=lambda:{})
        result=watch.publish(service=missing,now=self.moment)
        self.assertEqual(result['status'],'insufficient_data')
        self.assertIsNone(result['published_at'])


if __name__ == '__main__':
    unittest.main()
