import copy
import datetime as dt
import json
import random
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import core
import app


class SameDayUniverseCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = patch.object(core, 'DATA', Path(self.temp.name))
        self.override.start()
        self.service = core.Service()
        self.moment = dt.datetime(2026, 10, 19, 21, 35, tzinfo=core.TZ)
        self.target = '2026-10-19'
        self.original_time = '2026-10-19T10:04:25.964452+08:00'
        self.receipt = {'mode': 'shanghai', 'count': 2318, 'fetched_at': self.original_time}
        core.putkv('universe_info', self.receipt)
        with core.connect() as con:
            con.executemany('INSERT INTO securities VALUES(?,?,1)',
                            [(f'sh{600000 + i if i < 2000 else 603000 + i - 2000:06d}',
                              f'测试证券{i}') for i in range(2318)])
        self.network = core.md.MarketNetworkError('上交所官方证券列表接口均失败；query.sse.com.cn: request failed (HTTP 403)')

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def cached(self, receipt=None, *, moment=None, target=None, error=None):
        if receipt is not None:
            core.putkv('universe_info', receipt)
        return core._same_day_sse_universe_cache(target or self.target, moment or self.moment, error or self.network)

    def test_verified_legacy_same_day_full_cache_preserves_original_verification(self):
        rows, receipt = self.cached()
        self.assertEqual(len(rows), 2318)
        self.assertEqual(len({row['symbol'] for row in rows}), 2318)
        self.assertEqual(receipt['fetched_at'], self.original_time)
        self.assertEqual(receipt['last_live_attempt_at'], self.moment.isoformat())
        self.assertTrue(receipt['cache_reused'])
        self.assertEqual(receipt['source'], core.md.SSE_LIST_SOURCE)
        self.assertEqual(receipt['source_provenance'], 'legacy_sse_engine_path')
        self.assertNotIn('source_api', receipt)  # Never invent which old route succeeded.
        self.assertIn('HTTP 403', receipt['source_error'])
        self.assertEqual(core.getkv('universe_info'), self.receipt)  # Helper reads only.
        # The prior UTC date can already be today's date in Shanghai.
        _, utc_receipt = self.cached(dict(self.receipt, fetched_at='2026-10-18T16:00:00Z'))
        self.assertEqual(utc_receipt['fetched_at'], '2026-10-18T16:00:00Z')

    def test_repeated_reuse_keeps_legacy_provenance_and_original_time(self):
        _, first = self.cached()
        _, second = self.cached(first, moment=self.moment + dt.timedelta(minutes=10))
        self.assertEqual(second['fetched_at'], self.original_time)
        self.assertEqual(second['source_provenance'], 'legacy_sse_engine_path')
        self.assertNotEqual(first['last_live_attempt_at'], second['last_live_attempt_at'])

    def test_old_naive_future_missing_or_cross_day_receipts_are_rejected(self):
        for value in ('2026-10-16T10:04:25+08:00', '2026-10-19T10:04:25',
                      '2026-10-19T21:36:00+08:00', None, 'invalid', '2026-10-18T15:59:00Z'):
            with self.subTest(value=value), self.assertRaises(core.md.MarketDataError):
                self.cached(dict(self.receipt, fetched_at=value))

    def test_cache_is_only_for_same_completed_trading_day_after_sixteen(self):
        for moment, target in ((self.moment.replace(hour=15), self.target),
                               (self.moment, '2026-10-16'),
                               (self.moment.replace(tzinfo=None), self.target),
                               (dt.datetime(2026, 10, 1, 21, tzinfo=core.TZ), '2026-10-01')):
            with self.subTest(moment=moment, target=target), self.assertRaises(core.md.MarketDataError):
                self.cached(moment=moment, target=target)

    def test_count_mode_small_list_and_activity_mismatch_are_rejected(self):
        for changes in ({'count': True}, {'count': 2318.0}, {'count': 0}, {'count': 2317},
                        {'mode': 'watchlist'}, {'mode': 'all'}):
            with self.subTest(changes=changes), self.assertRaises(core.md.MarketDataError):
                self.cached(dict(self.receipt, **changes))
        with core.connect() as con:
            con.execute('UPDATE securities SET active=0 WHERE symbol<>?', ('sh600000',))
        with self.assertRaises(core.md.MarketDataError):
            self.cached(dict(self.receipt, count=1))

    def test_only_official_source_and_known_route_may_be_reused(self):
        for changes in ({'source': 'https://example.com/list'}, {'source': 123},
                        {'source_api': 'https://example.com/query'},
                        {'source_api': 'https://query.sse.com.cn.evil.test/query'}):
            with self.subTest(changes=changes), self.assertRaises(core.md.MarketDataError):
                self.cached(dict(self.receipt, **changes))
        _, receipt = self.cached(dict(self.receipt, source=core.md.SSE_LIST_SOURCE,
                                      source_api=core.md.SSE_LIST_APIS[1]))
        self.assertEqual(receipt['source_api'], core.md.SSE_LIST_APIS[1])
        self.assertEqual(receipt['source_provenance'], 'sse_live_verified')

    def test_invalid_names_and_non_shanghai_codes_do_not_enter_cache_fallback(self):
        for name in ('', '2318', 'sh600000', ' 测试证券 ', '测试\n证券'):
            with self.subTest(name=name):
                with core.connect() as con:
                    con.execute('UPDATE securities SET name=? WHERE symbol=?', (name, 'sh600000'))
                with self.assertRaises(core.md.MarketDataError):
                    self.cached()
        with core.connect() as con:
            con.execute('UPDATE securities SET name=?,symbol=? WHERE symbol=?', ('测试证券', 'sz000001', 'sh600000'))
        with self.assertRaises(core.md.MarketDataError):
            self.cached()

    def test_duplicate_rows_even_with_matching_declared_count_fail(self):
        with core.connect() as con:
            con.execute('ALTER TABLE securities RENAME TO original_test_securities')
            con.execute('CREATE TABLE securities(symbol TEXT,name TEXT,active INTEGER)')
            con.execute('INSERT INTO securities SELECT * FROM original_test_securities')
            con.execute('UPDATE securities SET symbol=? WHERE symbol=?', ('sh600000', 'sh600001'))
        with self.assertRaises(core.md.MarketDataError):
            self.cached()

    def test_structural_errors_never_qualify_and_public_error_does_not_leak_provider_text(self):
        with self.assertRaises(core.md.MarketDataError):
            self.cached(error=core.md.MarketDataError('query.sse.com.cn: request failed (HTTP 403)'))
        sensitive = core.md.MarketNetworkError('HTTP 403 ' + 'PRIVATE token=https://user:secret@example.com/' * 100)
        _, receipt = self.cached(error=sensitive)
        self.assertLessEqual(len(receipt['source_error']), 300)
        self.assertNotIn('PRIVATE', receipt['source_error'])
        self.assertNotIn('secret', receipt['source_error'])
        self.assertNotIn('http', receipt['source_error'])

    def stop_after_universe(self, error, *, allow=False, mode='shanghai', live_rows=None):
        class StopAfterUniverse(RuntimeError):
            pass
        cfg = dict(core.DEFAULTS, universe=mode)
        core.putkv('config', cfg)
        with patch.object(core, 'now', return_value=self.moment), \
                patch.object(core, 'expected_date', return_value=self.target), \
                patch.object(core.socket, 'socket'), \
                patch.object(core.md, 'get_universe', side_effect=error, return_value=live_rows) as fetch, \
                patch.object(core.md, 'fetch_history', return_value={'bars': []}), \
                patch.object(core.concurrent.futures, 'ThreadPoolExecutor', side_effect=StopAfterUniverse):
            with self.assertRaises(StopAfterUniverse if error is None or allow and mode == 'shanghai'
                                   and isinstance(error, core.md.MarketNetworkError) else type(error)):
                self.service.refresh(force=True, allow_same_day_universe_cache=allow)
        fetch.assert_called_once_with(mode)

    def test_refresh_explicit_cache_fallback_does_not_retimestamp_receipt(self):
        self.stop_after_universe(self.network, allow=True)
        receipt = core.getkv('universe_info')
        self.assertTrue(receipt['cache_reused'])
        self.assertEqual(receipt['fetched_at'], self.original_time)
        self.assertEqual(receipt['count'], 2318)
        with core.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM securities WHERE active=1').fetchone()[0], 2318)

    def test_refresh_does_not_reuse_cache_by_default_outside_shanghai_or_for_bad_live_data(self):
        for allow, mode, error in ((False, 'shanghai', self.network),
                                   (True, 'all', self.network),
                                   (True, 'shanghai', core.md.MarketDataError('上交所证券列表不完整'))):
            with self.subTest(allow=allow, mode=mode, error=error):
                self.stop_after_universe(error, allow=allow, mode=mode)
                self.assertEqual(core.getkv('universe_info'), self.receipt)

    def test_new_live_receipt_records_actual_official_route_and_resets_reuse_flag(self):
        live = [{'symbol': 'sh600000', 'name': '测试证券', 'source': core.md.SSE_LIST_SOURCE,
                 'source_api': core.md.SSE_LIST_APIS[1]}]
        self.stop_after_universe(None, allow=True, live_rows=live)
        receipt = core.getkv('universe_info')
        self.assertFalse(receipt['cache_reused'])
        self.assertEqual(receipt['fetched_at'], self.moment.isoformat())
        self.assertEqual(receipt['source'], core.md.SSE_LIST_SOURCE)
        self.assertEqual(receipt['source_api'], core.md.SSE_LIST_APIS[1])
        self.assertEqual(receipt['source_provenance'], 'sse_live_verified')
        self.assertNotIn('source_error', receipt)

    def test_dashboard_discloses_original_cache_verification_and_full_count(self):
        _, receipt = self.cached()
        core.putkv('universe_info', receipt)
        with patch.object(core, 'now', return_value=self.moment), \
                patch.object(core, 'expected_date', return_value=self.target):
            warnings = self.service.dashboard()['meta']['warnings']
        transparent = next(text for text in warnings if '本次复用当日' in text)
        self.assertIn(self.original_time, transparent)
        self.assertIn('完整2318只名单', transparent)
        self.assertIn('保留原核验时间', transparent)


class DecisionEvidenceTests(unittest.TestCase):
    """Synthetic price process tests the real v3 fit, never a tuned real stock."""

    @classmethod
    def setUpClass(cls):
        randomizer, price, momentum = random.Random(3), 20.0, 0.0
        history = []
        for i in range(700):
            opening = price
            momentum = max(-.04, min(.04, .9 * momentum + randomizer.gauss(0, .006)))
            price *= 1 + momentum
            history.append({'date': (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(),
                            'open': opening, 'close': price,
                            'volume': 100000 + randomizer.randrange(50000)})
        cls.legal_prediction = core.forecast.predict(history, history)
        cls.target = history[-1]['date']

    def prediction(self):
        return copy.deepcopy(self.legal_prediction)

    def rebuild_summaries(self, prediction):
        decision = prediction['validation_decision']
        opportunities = decision['opportunities']
        decision['aggregate'] = core.forecast._decision_metrics(opportunities)
        decision['folds'] = [{'fold': fold, **core.forecast._decision_metrics(
            [item for item in opportunities if item['fold'] == fold])} for fold in (1, 2, 3)]
        decision['latest_fold'] = copy.deepcopy(decision['folds'][-1])

    def assert_rejected(self, prediction):
        self.assertTrue(core.prediction_failures(prediction, self.target))

    def test_real_v3_fit_with_legal_decision_evidence_passes_independent_gate(self):
        prediction = self.prediction()
        self.assertTrue(prediction['eligible'], prediction['reason'])
        self.assertTrue(prediction['validation_decision']['eligible'])
        self.assertEqual(core.decision_validation_failures(prediction, self.target), [])
        self.assertEqual(core.prediction_failures(prediction, self.target), [])
        stock = {'symbol': 'sh600298', 'eligible': True, 'prediction': prediction}
        checked = core.prediction_gate(stock, self.target)
        self.assertTrue(checked['eligible'])
        self.assertTrue(checked['prediction_current'])

    def test_decision_protocol_flags_cost_and_fixed_sample_thresholds_are_required(self):
        for changes in ({'protocol': 'old'}, {'eligible': False}, {'eligible': 1},
                        {'cost_stress_pct': .1}, {'cost_stress_pct': True},
                        {'minimum_trades': 20}, {'minimum_trades': 30.0},
                        {'minimum_latest_trades': 1}, {'failures': ['still failed']},
                        {'selection_rule': 'choose_after_realized_profit'}):
            with self.subTest(changes=changes):
                prediction = self.prediction()
                prediction['validation_decision'].update(changes)
                self.assert_rejected(prediction)
        prediction = self.prediction()
        prediction.pop('validation_decision')
        self.assert_rejected(prediction)

    def test_current_feature_z_requires_finite_nonboolean_evidence_within_four(self):
        for value in (None, True, float('nan'), float('inf'), -1, 5, 10 ** 400):
            with self.subTest(value=value):
                prediction = self.prediction()
                prediction['current_feature_max_abs_z'] = value
                self.assert_rejected(prediction)
        prediction = self.prediction()
        prediction.pop('current_feature_max_abs_z')
        self.assert_rejected(prediction)
        for value in (0, 4):
            prediction = self.prediction()
            prediction['current_feature_max_abs_z'] = value
            self.assertEqual(core.prediction_failures(prediction, self.target), [])

    def test_summary_tampering_is_rejected_for_every_numeric_boolean_and_null_metric(self):
        for section in ('aggregate', 'latest_fold', 'fold0'):
            prediction = self.prediction()
            metrics = prediction['validation_decision']['folds'][0] if section == 'fold0' else prediction['validation_decision'][section]
            for key, original in metrics.items():
                with self.subTest(section=section, key=key):
                    changed = self.prediction()
                    saved = changed['validation_decision']['folds'][0] if section == 'fold0' else changed['validation_decision'][section]
                    saved[key] = (not original if type(original) is bool else
                                  0 if original is None else original + 1)
                    self.assert_rejected(changed)

    def test_summary_cannot_replace_counters_with_booleans_or_nonfinite_values(self):
        for key, value in (('samples', True), ('trades', 30.0),
                           ('net_mean_per_opportunity_pct', float('nan')),
                           ('net_compound_return_pct', float('inf')),
                           ('max_drawdown_pct', False)):
            with self.subTest(key=key, value=value):
                prediction = self.prediction()
                prediction['validation_decision']['aggregate'][key] = value
                self.assert_rejected(prediction)

    def test_decision_dates_future_fold_mismatch_overlap_and_reordering_fail(self):
        mutations = [
            lambda items: items[-1].update(exit_date=(dt.date.fromisoformat(self.target) + dt.timedelta(days=1)).isoformat()),
            lambda items: items[0].update(fold=2),
            lambda items: items[0].update(feature_date='20251130'),
            lambda items: items[0].update(entry_date=items[0]['feature_date']),
            lambda items: items[1].update(feature_date=items[0]['entry_date'], entry_date=items[0]['exit_date']),
            lambda items: items.__setitem__(slice(0, 2), list(reversed(items[:2]))),
            lambda items: items.__setitem__(1, copy.deepcopy(items[0])),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                prediction = self.prediction()
                mutate(prediction['validation_decision']['opportunities'])
                self.assert_rejected(prediction)

    def test_opportunity_count_matches_nonoverlap_and_each_fold_has_evidence(self):
        for change in ('missing', 'extra', 'wrong_count', 'missing_fold'):
            with self.subTest(change=change):
                prediction = self.prediction()
                decision = prediction['validation_decision']
                if change == 'missing':
                    decision['opportunities'].pop()
                elif change == 'extra':
                    decision['opportunities'].append(copy.deepcopy(decision['opportunities'][-1]))
                elif change == 'wrong_count':
                    prediction['validation_nonoverlapping']['samples'] = True
                else:
                    decision['opportunities'] = [item for item in decision['opportunities'] if item['fold'] != 2]
                    prediction['validation_nonoverlapping']['samples'] = len(decision['opportunities'])
                self.assert_rejected(prediction)

    def test_opportunity_fields_must_be_finite_numbers_and_selector_boolean(self):
        for field in ('estimate_pct', 'gross_return_pct', 'net_return_pct', 'passive_net_return_pct'):
            for value in (True, None, float('nan'), float('inf'), 10 ** 400):
                with self.subTest(field=field, value=value):
                    prediction = self.prediction()
                    prediction['validation_decision']['opportunities'][0][field] = value
                    self.assert_rejected(prediction)
        for field, value in (('selected', 1), ('fold', True), ('fold', 1.0)):
            prediction = self.prediction()
            prediction['validation_decision']['opportunities'][0][field] = value
            self.assert_rejected(prediction)

    def test_selection_and_net_cost_arithmetic_are_checked_before_saved_metrics(self):
        for mutate in (lambda item: item.update(selected=not item['selected']),
                       lambda item: item.update(net_return_pct=item['net_return_pct'] + .3),
                       lambda item: item.update(passive_net_return_pct=item['passive_net_return_pct'] + .3)):
            prediction = self.prediction()
            mutate(prediction['validation_decision']['opportunities'][0])
            self.rebuild_summaries(prediction)
            self.assert_rejected(prediction)

    def test_exact_cost_boundary_means_cash_even_if_saved_eligible_says_buy(self):
        prediction = self.prediction()
        item = next(item for item in prediction['validation_decision']['opportunities']
                    if item['selected'] and item['net_return_pct'] < 0 and item['fold'] == 1)
        item['estimate_pct'] = .30
        self.assert_rejected(prediction)
        item.update(selected=False, net_return_pct=0.0)
        self.rebuild_summaries(prediction)
        self.assertEqual(core.prediction_failures(prediction, self.target), [])
        item.update(estimate_pct=.30 + 1e-12, selected=True,
                    net_return_pct=item['gross_return_pct'] - .30)
        self.rebuild_summaries(prediction)
        self.assertEqual(core.prediction_failures(prediction, self.target), [])

    def test_forged_positive_eligibility_and_rebuilt_summaries_cannot_hide_real_losses(self):
        prediction = self.prediction()
        for item in prediction['validation_decision']['opportunities']:
            if item['selected']:
                item.update(gross_return_pct=-2, net_return_pct=-2.3, passive_net_return_pct=-2.3)
        self.rebuild_summaries(prediction)
        self.assertTrue(prediction['validation_decision']['eligible'])
        failures = core.prediction_failures(prediction, self.target)
        self.assertTrue(any('独立重算的成本压力后每机会收益' in text for text in failures), failures)

    def test_forged_sample_summaries_cannot_hide_actual_small_selected_subset(self):
        prediction = self.prediction()
        opportunities = prediction['validation_decision']['opportunities']
        for item in opportunities:
            item.update(estimate_pct=.3, selected=False, net_return_pct=0)
        for item in opportunities[:5]:
            item.update(estimate_pct=1, selected=True, net_return_pct=item['gross_return_pct'] - .3)
        self.rebuild_summaries(prediction)
        failures = core.prediction_failures(prediction, self.target)
        self.assertTrue(any('独立重算的汇总或最近段选择周期不足' in text for text in failures), failures)

    def test_prediction_gate_recheck_does_not_rewrite_original_snapshot(self):
        prediction = self.prediction()
        prediction['current_feature_max_abs_z'] = 5
        archived = {'symbol': 'sh600298', 'eligible': True, 'entry_low': 10,
                    'prediction': prediction, 'published_at': '2026-10-19T09:01:00+08:00'}
        original = copy.deepcopy(archived)
        result = core.prediction_gate(archived, self.target)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['prediction']['eligible'])
        self.assertIsNone(result['prediction']['rank_value'])
        self.assertEqual(archived, original)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = patch.object(core, 'DATA', Path(self.temp.name))
        self.override.start()
        self.service = core.Service()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def candidate(self, *, prediction_eligible=True):
        return {'symbol': 'sh600298', 'name': '安琪酵母', 'date': '2026-10-15',
                'eligible': True, 'excluded': False, 'stale': False, 'score': 80,
                'entry_low': 9.99, 'entry_high': 10.02, 'max_position_pct': 10,
                'raw_close': 10, 'prediction': {'available': True,
                    'eligible': prediction_eligible, 'as_of_date': '2026-10-15',
                    'expected_return_pct': 3, 'rank_value': 1.2,
                    'training_samples': 120, 'validation_samples': 60}}

    def quote(self, timestamp):
        return {'symbol': 'sh600298', 'name': '安琪酵母', 'datetime': timestamp.isoformat(),
                'date': timestamp.date().isoformat(), 'price': 10, 'previous_close': 10,
                'volume': 1000, 'actionable': True, 'suspended': False,
                'is_st': False, 'is_delisting': False, 'change_pct': 0}

    def seed_candidate(self, signal=None, index_date='2026-10-15'):
        signal = signal or self.candidate()
        with core.connect() as con:
            con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)',
                        ('2026-10-15', 'sh600298', core.encode(signal)))
            con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)',
                        ('2026-10-15', 'sh000001', core.encode({'symbol': 'sh000001', 'date': index_date})))

    def seed_order(self, timestamp):
        self.seed_candidate()
        with core.connect() as con:
            return con.execute('INSERT INTO orders(created_at,symbol,side,quantity,limit_price,status) VALUES(?,?,?,?,?,?)',
                               (timestamp.isoformat(), 'sh600298', 'buy', 100, 10.02, 'pending')).lastrowid

    def test_stale_signal_never_exposes_entry(self):
        signal = {'symbol': 'sh600036', 'date': '2026-10-15', 'eligible': True, 'score': 90, 'entry_low': 20, 'entry_high': 21, 'stop_loss': 19, 'take_profit': 23, 'max_position_pct': 10}
        result = core.gate_signal(signal, '2026-10-16')
        self.assertFalse(result['eligible'])
        self.assertIsNone(result['entry_low'])
        self.assertEqual(result['max_position_pct'], 0)
        self.assertEqual(signal['entry_low'], 20)

    def test_missing_index_disables_buy(self):
        result = core.gate_signal({'date': '2026-10-16', 'eligible': True}, '2026-10-16', False)
        self.assertFalse(result['eligible'])

    def test_fresh_quote_risk_flags_and_price_disagreement_override_old_security_name(self):
        stock=self.candidate()
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        for changes in ({'is_st':True},{'is_delisting':True},{'name':'*ST测试'},{'name':'测试退'},{'price':10.5}):
            with self.subTest(changes=changes):
                result=core.quote_gate(stock,dict(quote,**changes),stock['date'])
                self.assertFalse(result['eligible']);self.assertFalse(result['quote_valid'])
                self.assertEqual(result['levels_status'],'research_only')
        self.assertTrue(core.quote_gate(stock,dict(quote,price=10.01),stock['date'])['eligible'])

    def test_missing_unadjusted_close_cannot_confirm_quote_consistency(self):
        stock=self.candidate();stock.pop('raw_close',None)
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        result=core.quote_gate(stock,quote,stock['date'])
        self.assertFalse(result['eligible']);self.assertFalse(result['quote_valid'])

    def test_chase_gate_rejects_old_policy_or_bands_above_ceiling_without_mutating_source(self):
        stock=self.candidate()
        self.assertFalse(core.chase_gate(stock,stock['date'])['eligible'])
        risk={'version':'anti-chase-v1','as_of_date':stock['date'],'blocked':False,'gate_passed':True,
              'entry_ceiling':{'reference_date':stock['date'],'price_basis':'latest_raw_close_scaled_ma20','requires_live_quote':True,'price':10.03}}
        stock['chase_risk']=risk
        self.assertTrue(core.chase_gate(stock,stock['date'])['eligible'])
        risk['entry_ceiling']['price']=10
        rejected=core.chase_gate(stock,stock['date'])
        self.assertFalse(rejected['eligible']);self.assertIsNone(rejected['entry_high'])
        self.assertEqual(stock['entry_high'],10.02)

    def test_old_prediction_cache_cannot_authorize_new_recommendation(self):
        stock=self.candidate(); original=dict(stock['prediction'])
        result=core.prediction_gate(stock,stock['date'])
        self.assertFalse(result['eligible']);self.assertFalse(result['prediction']['eligible'])
        self.assertIsNone(result['prediction']['rank_value']);self.assertEqual(stock['prediction'],original)
        self.assertEqual(result['levels_status'],'research_only')

    def test_calendar_failure_is_closed(self):
        with patch.object(core.md, 'is_trading_day', side_effect=RuntimeError('missing calendar')):
            self.assertFalse(core.is_open(dt.datetime(2027, 1, 4, 10, 0, tzinfo=core.TZ)))

    def test_config_validation(self):
        for payload in ({'universe': 'random'}, {'watchlist': '../../private'}, {'refresh_minutes': 0}):
            with self.assertRaises(ValueError):
                core.set_config(payload)
        self.assertEqual(core.set_config({'watchlist': 'sh600036 sh600519'})['watchlist'], 'sh600036,sh600519')

    def test_missing_or_wrong_day_market_cap_cannot_pass_preference_filter(self):
        stock=self.candidate()
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        self.assertTrue(core.quote_gate(stock,quote,stock['date'])['eligible'])
        for bad in (None,dict(quote,date='2026-10-14'),dict(quote,market_cap_100m=None),dict(quote,suspended=True)):
            self.assertFalse(core.quote_gate(stock,bad,stock['date'])['eligible'])
        mega=core.quote_gate(stock,dict(quote,market_cap_100m=5000),stock['date'])
        self.assertTrue(mega['excluded']);self.assertIsNone(mega['entry_low'])

    def test_orders_reserve_cash_and_enforce_t1(self):
        report = {'stock': self.candidate()}
        # Existing holdings keep equity at 100,000 while only 10,000 is cash.
        # Both proposed orders fit the exposure cap; only fee-inclusive cash
        # reservations must reject the second one.
        core.putkv('cash', 10000)
        with core.connect() as con:
            con.execute('INSERT INTO lots(symbol,quantity,price,buy_date) VALUES(?,?,?,?)',
                        ('sh603010', 9000, 10, '2026-10-14'))
        with patch.object(self.service, 'stock', return_value=report):
            first = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 900, 'limit_price': 10})
            self.assertEqual(first['status'], 'pending')
            with self.assertRaisesRegex(ValueError, '可用资金不足'):
                self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
            with self.assertRaises(ValueError):
                self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 10})
            self.service.cancel_order(first['id'])
            released = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
            self.assertEqual(released['status'], 'pending')
        self.assertEqual(self.service.portfolio()['cash'], 10000)

    def test_prediction_filter_blocks_order_without_weakening_the_rule(self):
        for prediction in (None, {'eligible': False}):
            signal = self.candidate()
            if prediction is None:
                signal.pop('prediction')
            else:
                signal['prediction'] = prediction
            with patch.object(self.service, 'stock', return_value={'stock': signal}):
                with self.assertRaisesRegex(ValueError, '未通过'):
                    self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
        self.assertEqual(self.service.portfolio()['orders'], [])

    def test_old_quote_cannot_fill_new_order(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp)
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=10))])
        self.assertEqual(len(self.service.portfolio()['trades']), 0)
        # Same order, signal and price: advancing only the quote makes it fill.
        with patch.object(core, 'now', return_value=timestamp+dt.timedelta(seconds=3)), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp+dt.timedelta(seconds=2))])
        self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_valid_quote_fill_and_t1(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_candidate()
        with patch.object(core, 'now', return_value=timestamp-dt.timedelta(seconds=30)), patch.object(self.service, 'stock', return_value={'stock': self.candidate()}):
            order = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10.02})
        quote = self.quote(timestamp-dt.timedelta(seconds=2))
        core.putkv('quotes', {'quotes': [quote]})
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([quote])
            portfolio = self.service.portfolio()
            self.assertEqual(len(portfolio['trades']), 1)
            self.assertEqual(portfolio['trades'][0]['order_id'], order['id'])
            self.assertEqual(portfolio['orders'][0]['status'], 'filled')
            trade = portfolio['trades'][0]
            self.assertEqual(trade['quantity'], 100)
            self.assertGreaterEqual(trade['fee'], 5)
            self.assertEqual(portfolio['positions'][0]['available'], 0)
            self.assertAlmostEqual(portfolio['cash'], 100000 - trade['price'] * 100 - trade['fee'], places=2)
            with patch.object(self.service, 'stock', return_value={'stock': self.candidate()}), self.assertRaisesRegex(ValueError, r'T\+1'):
                self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 9})
        # A real next-session sell closes the lot and charges sell-side fees.
        next_session = dt.datetime(2026, 10, 19, 10, 0, tzinfo=core.TZ)
        with patch.object(core, 'now', return_value=next_session-dt.timedelta(seconds=30)), patch.object(self.service, 'stock', return_value={'stock': self.candidate()}):
            sale = self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 9.98})
        with patch.object(core, 'now', return_value=next_session), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(next_session-dt.timedelta(seconds=2))])
        closed = self.service.portfolio()
        self.assertEqual(len(closed['trades']), 2)
        self.assertEqual(closed['positions'], [])
        sell_trade = next(t for t in closed['trades'] if t['order_id'] == sale['id'])
        self.assertGreater(sell_trade['fee'], trade['fee'])
        self.assertAlmostEqual(closed['cash'], portfolio['cash'] + sell_trade['price'] * 100 - sell_trade['fee'], places=2)

    def test_pending_buy_rechecks_prediction_before_filling(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp-dt.timedelta(seconds=30))
        self.seed_candidate(self.candidate(prediction_eligible=False))
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=2))])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.seed_candidate()
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=1))])
            self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_pending_buy_rechecks_index_date_and_corporate_action_reference(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp-dt.timedelta(seconds=30))
        self.seed_candidate(index_date='2026-10-14')
        quote = self.quote(timestamp-dt.timedelta(seconds=2))
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([quote])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.seed_candidate()
            self.service.match_orders([dict(quote, previous_close=9.5)])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.assertIn('除权', self.service.portfolio()['orders'][0]['note'])
            self.service.match_orders([quote])
            self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_http_csrf_and_host_guard(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.make_handler(self.service, 0))
        port = server.server_address[1]
        server.RequestHandlerClass = app.make_handler(self.service, port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{port}'
        try:
            with urlopen(url+'/api/health', timeout=5) as response:
                self.assertTrue(json.load(response)['ok'])
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url+'/api/config', data=b'{}', method='POST'), timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            request = Request(url+'/api/config', data=b'{"refresh_minutes":6}', headers={'X-CSRF-Token': self.service.csrf, 'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response)['refresh_minutes'], 6)
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url+'/api/health', headers={'Host': 'evil.example'}), timeout=5)
            self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
