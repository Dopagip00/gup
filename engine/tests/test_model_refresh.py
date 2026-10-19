import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core
import model_refresh


DATE = '2026-10-16'
NOW = dt.datetime(2026, 10, 18, 21, 7, tzinfo=core.TZ)


class ModelRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = patch.object(core, 'DATA', Path(self.temp.name))
        self.directory.start()
        core.init_db()
        self.clock = patch.object(core, 'now', return_value=NOW)
        self.clock.start()
        self.add_stock('sh600001', '测试一')
        self.add_stock('sh600002', '测试二')
        self.history('sh000001')
        self.analyze = patch.object(core.strategy, 'analyze', side_effect=self.analysis).start()
        self.enrich = patch.object(core, 'enrich_signal', side_effect=self.enrichment).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    def prediction(self, date=DATE):
        return {'model_version': core.forecast.MODEL_VERSION,
                'validation_protocol': core.forecast.VALIDATION_PROTOCOL,
                'validation_protocol_complete': True,
                'as_of_date': date, 'available': True, 'eligible': True, 'rank_value': 3}

    def old(self, symbol, name, date=DATE):
        return {'symbol': symbol, 'name': name, 'date': date, 'eligible': True,
                'prediction': {'available': True, 'eligible': True, 'rank_value': 8,
                               'model_version': 'ridge-cycle-v1', 'as_of_date': date},
                'source': '原报告来源', 'adjustment': 'qfq', 'fetched_at': '2026-10-16T16:30:00+08:00',
                'warnings': [], 'entry_low': 9, 'entry_high': 9.1,
                'stop_loss': 8, 'take_profit': 12}

    def history(self, symbol, dates=('2026-10-15', DATE, '2026-10-19')):
        data = {'source': '缓存行情来源', 'adjustment': 'qfq', 'fetched_at': '2026-10-18T18:00:00+08:00',
                'bars': [{'date': day, 'open': 10, 'close': 10.2, 'high': 10.5, 'low': 9.9,
                          'raw_close': 10.2, 'volume': 100} for day in dates]}
        core.save_history(symbol, data)

    def add_stock(self, symbol, name, active=1):
        with core.connect() as con:
            con.execute('INSERT INTO securities VALUES(?,?,?)', (symbol, name, active))
            con.execute('INSERT INTO signals VALUES(?,?,?)', (DATE, symbol, core.encode(self.old(symbol, name))))
        self.history(symbol)

    def read(self, symbol, date=DATE):
        with core.connect() as con:
            row = con.execute('SELECT payload FROM signals WHERE report_date=? AND symbol=?', (date, symbol)).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self, table):
        with core.connect() as con:
            return [tuple(row) for row in con.execute('SELECT * FROM ' + table + ' ORDER BY 1,2')]

    def analysis(self, bars, symbol, name, benchmark_bars=None):
        self.assertTrue(all(bar['date'] <= DATE for bar in bars))
        self.assertTrue(all(bar['date'] <= DATE for bar in benchmark_bars))
        return {'symbol': symbol, 'name': name, 'date': bars[-1]['date'], 'eligible': True,
                'chase_risk':{'version':'anti-chase-v1','as_of_date':bars[-1]['date']},
                'entry_low': 9, 'entry_high': 9.1, 'stop_loss': 8, 'take_profit': 12, 'warnings': []}

    def enrichment(self, result, bars, benchmark):
        return dict(result, prediction=self.prediction(bars[-1]['date']), excluded=False)

    def test_old_versions_recompute_on_closed_day_and_truncate_future_bars(self):
        histories = self.snapshot('histories')
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual(receipt['status'], 'success')
        self.assertEqual((receipt['ok'], receipt['updated'], receipt['failed']), (2, 2, 0))
        self.assertEqual(receipt['original_data_date'], DATE)
        self.assertEqual(receipt['finished_at'], NOW.isoformat())
        self.assertFalse(receipt['morning_collection'])
        self.assertFalse(receipt['network_used'])
        self.assertTrue(receipt['committed'])
        self.assertEqual(self.snapshot('histories'), histories)
        for call in self.analyze.call_args_list:
            self.assertEqual([bar['date'] for bar in call.args[0]], ['2026-10-15', DATE])
            self.assertEqual([bar['date'] for bar in call.kwargs['benchmark_bars']], ['2026-10-15', DATE])
        signal = self.read('sh600001')
        self.assertEqual(signal['prediction']['model_version'], core.forecast.MODEL_VERSION)
        self.assertEqual(signal['recomputed_at'], NOW.isoformat())
        self.assertEqual(signal['source'], '原报告来源')
        self.assertEqual(signal['fetched_at'], '2026-10-16T16:30:00+08:00')

    def test_current_model_and_matching_date_fast_noop(self):
        model_refresh.refresh_cached_predictions(core)
        saved = self.snapshot('signals')
        self.analyze.reset_mock()
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['status'], receipt['current'], receipt['attempted']), ('noop', 2, 0))
        self.analyze.assert_not_called()
        self.assertEqual(self.snapshot('signals'), saved)

    def test_old_or_missing_chase_policy_requires_recompute_even_with_current_point_model(self):
        model_refresh.refresh_cached_predictions(core)
        signal=self.read('sh600001');signal.pop('chase_risk')
        with core.connect() as con:
            con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?',(core.encode(signal),DATE,'sh600001'))
        receipt=model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['current'],receipt['attempted'],receipt['ok']),(1,1,1))
        self.assertEqual(self.read('sh600001')['chase_risk']['version'],'anti-chase-v1')

    def test_wrong_original_or_prediction_date_is_recomputed(self):
        model_refresh.refresh_cached_predictions(core)
        for key in ('date', 'as_of_date'):
            with self.subTest(key=key):
                signal = self.read('sh600001')
                if key == 'date':
                    signal['date'] = '2026-10-15'
                else:
                    signal['prediction']['as_of_date'] = '2026-10-15'
                with core.connect() as con:
                    con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?', (core.encode(signal), DATE, 'sh600001'))
                self.analyze.reset_mock()
                receipt = model_refresh.refresh_cached_predictions(core)
                self.assertEqual((receipt['attempted'], receipt['updated'], receipt['current']), (1, 1, 1))
                self.assertEqual(self.read('sh600001')['prediction']['as_of_date'], DATE)

    def test_preserves_published_advice_private_keys_old_reports_and_inactive_rows(self):
        published = {'status': 'published', 'buy': {'symbol': 'sh600001', 'entry_low': 7, 'stop_loss': 6}}
        for key, value in {'daily_plan:2026-10-16': published, 'cash': 123.4,
                           'morning_news': {'status': 'verified', 'as_of': 'original'},
                           'last_success': {'finished_at': 'original'}, 'private_config': {'sentinel': 'unchanged'}}.items():
            core.putkv(key, value)
        self.add_stock('sh600003', '停用股', active=0)
        with core.connect() as con:
            con.execute('INSERT INTO signals VALUES(?,?,?)', ('2026-10-15', 'sh600001', core.encode(self.old('sh600001', '旧报告', '2026-10-15'))))
        kv, securities = self.snapshot('kv'), self.snapshot('securities')
        prior, inactive = self.read('sh600001', '2026-10-15'), self.read('sh600003')
        model_refresh.refresh_cached_predictions(core)
        self.assertEqual(self.snapshot('kv'), kv)
        self.assertEqual(self.snapshot('securities'), securities)
        self.assertEqual(self.read('sh600001', '2026-10-15'), prior)
        self.assertEqual(self.read('sh600003'), inactive)

    def test_missing_stock_data_disables_old_flag_and_reports_partial(self):
        with core.connect() as con:
            con.execute('DELETE FROM histories WHERE symbol=?', ('sh600002',))
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['status'], receipt['ok'], receipt['failed']), ('partial', 1, 1))
        signal = self.read('sh600002')
        self.assertFalse(signal['eligible'])
        self.assertFalse(signal['prediction']['available'])
        self.assertFalse(signal['prediction']['eligible'])
        self.assertIsNone(signal['prediction']['rank_value'])
        self.assertIn('原报告交易日', signal['prediction']['reason'])
        self.history('sh600002')
        self.assertEqual(model_refresh.refresh_cached_predictions(core)['updated'], 1)

    def test_missing_benchmark_disables_all_recomputed_predictions_and_is_error(self):
        with core.connect() as con:
            con.execute('DELETE FROM histories WHERE symbol=?', ('sh000001',))
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['status'], receipt['ok'], receipt['failed']), ('error', 0, 2))
        self.assertTrue(receipt['committed'])
        self.analyze.assert_not_called()
        self.assertFalse(self.read('sh600001')['prediction']['eligible'])

    def test_repeated_stale_stock_retries_keep_current_market_report_partial(self):
        # Match the production shape: the first upgrade succeeds for 2316
        # stocks, then subsequent stages retry only the four stale histories.
        symbols = ['sh' + str(600001 + index) for index in range(2320)]
        good_history = {'bars': [{'date': DATE, 'open': 10, 'close': 10.2,
                                 'high': 10.5, 'low': 9.9, 'volume': 100}]}
        stale_history = {'bars': [dict(good_history['bars'][0], date='2026-10-15')]}
        with core.connect() as con:
            con.executemany('INSERT OR REPLACE INTO securities VALUES(?,?,?)',
                            [(symbol, '测试股', 1) for symbol in symbols])
            con.executemany('INSERT OR REPLACE INTO signals VALUES(?,?,?)',
                            [(DATE, symbol, core.encode(self.old(symbol, '测试股')))
                             for symbol in symbols])
            con.executemany('INSERT OR REPLACE INTO histories VALUES(?,?,?)',
                            [(symbol, core.encode(good_history if index < 2316 else stale_history), NOW.isoformat())
                             for index, symbol in enumerate(symbols)])
        first = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((first['status'], first['current'], first['ok'], first['failed']),
                         ('partial', 0, 2316, 4))

        def current_payloads():
            with core.connect() as con:
                return dict(con.execute('SELECT symbol,payload FROM signals WHERE report_date=? AND symbol<?',
                                        (DATE, symbols[2316])))

        saved_current = current_payloads()
        for attempt in range(2):
            with self.subTest(retry=attempt):
                self.analyze.reset_mock()
                receipt = model_refresh.refresh_cached_predictions(core)
                self.assertEqual((receipt['status'], receipt['current'], receipt['attempted'],
                                  receipt['ok'], receipt['failed'], receipt['updated']),
                                 ('partial', 2316, 4, 0, 4, 4))
                self.assertTrue(receipt['committed'])
                self.analyze.assert_not_called()
                self.assertEqual(current_payloads(), saved_current)
                for symbol in symbols[2316:]:
                    self.assertFalse(self.read(symbol)['prediction']['available'])
                    self.assertEqual(self.read(symbol)['recompute_status'], 'error')

    def test_failed_recompute_cannot_relabel_old_numbers_as_current_model(self):
        signal = self.read('sh600002')
        signal['prediction'].update(expected_return_pct=12.3, interval_low_pct=9.1,
            interval_high_pct=15.8, rank_value=8.9, raw_rank_value=9.2,
            cost_stress_return_pct=12, validation_mae_pct=1.2,
            validation_aggregate={'mae_pct': 1.1, 'direction_accuracy_pct': 88},
            validation_folds=[{'mae_pct': 1.3, 'direction_accuracy_pct': 90}],
            next_day={'available': True, 'expected_return_pct': 4.5,
                      'interval_low_pct': 2.3, 'interval_high_pct': 6.7})
        with core.connect() as con:
            con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?',
                        (core.encode(signal), DATE, 'sh600002'))
            con.execute('DELETE FROM histories WHERE symbol=?', ('sh600002',))
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual(receipt['status'], 'partial')
        prediction = self.read('sh600002')['prediction']
        self.assertEqual(prediction['model_version'], core.forecast.MODEL_VERSION)
        self.assertEqual(prediction['previous_model_version'], 'ridge-cycle-v1')
        self.assertFalse(prediction['available'])
        for key in ('expected_return_pct', 'interval_low_pct', 'interval_high_pct',
                    'rank_value', 'raw_rank_value'):
            self.assertIn(key, prediction)
            self.assertIsNone(prediction[key])
        for key in ('cost_stress_return_pct', 'validation_mae_pct',
                    'validation_aggregate', 'validation_folds', 'next_day'):
            self.assertNotIn(key, prediction)
        self.assertEqual(prediction['warnings'], [prediction['reason']])

    def test_analyzer_exception_does_not_become_success(self):
        self.analyze.side_effect = ValueError('计算失败')
        for attempt in range(2):
            with self.subTest(retry=attempt):
                receipt = model_refresh.refresh_cached_predictions(core)
                self.assertEqual((receipt['status'], receipt['current'], receipt['ok'], receipt['failed']),
                                 ('error', 0, 0, 2))
                self.assertIn('计算失败', receipt['errors'][0]['error'])
                self.assertFalse(self.read('sh600001')['prediction']['eligible'])

    def test_existing_exclusion_survives_offline_recompute(self):
        signal = self.read('sh600001')
        signal.update(excluded=True, exclusion_reason='已有总市值排除')
        with core.connect() as con:
            con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?', (core.encode(signal), DATE, 'sh600001'))
        model_refresh.refresh_cached_predictions(core)
        signal = self.read('sh600001')
        self.assertTrue(signal['excluded'])
        self.assertFalse(signal['eligible'])
        self.assertIsNone(signal['entry_low'])

    def test_concurrent_signal_change_aborts_entire_set(self):
        before = self.read('sh600001')
        changed = self.read('sh600002')
        changed['concurrent_writer'] = True
        wrote = False

        def enrich(result, bars, benchmark):
            nonlocal wrote
            if not wrote:
                wrote = True
                with core.connect() as con:
                    con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?', (core.encode(changed), DATE, 'sh600002'))
            return self.enrichment(result, bars, benchmark)

        self.enrich.side_effect = enrich
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['status'], receipt['updated'], receipt['committed']), ('conflict', 0, False))
        self.assertEqual(self.read('sh600001'), before)
        self.assertEqual(self.read('sh600002'), changed)

    def test_concurrent_history_change_aborts_before_commit(self):
        before = self.snapshot('signals')
        wrote = False

        def enrich(result, bars, benchmark):
            nonlocal wrote
            if not wrote:
                wrote = True
                self.history('sh000001', dates=('2026-10-14', DATE))
            return self.enrichment(result, bars, benchmark)

        self.enrich.side_effect = enrich
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual(receipt['status'], 'conflict')
        self.assertEqual(self.snapshot('signals'), before)

    def test_commit_error_rolls_back_preceding_stock_write(self):
        before = self.snapshot('signals')
        with core.connect() as con:
            con.execute("CREATE TRIGGER refuse_second BEFORE UPDATE ON signals WHEN NEW.symbol='sh600002' BEGIN SELECT RAISE(ABORT,'test write failure'); END")
        receipt = model_refresh.refresh_cached_predictions(core)
        self.assertEqual((receipt['status'], receipt['updated'], receipt['committed']), ('error', 0, False))
        self.assertEqual(self.snapshot('signals'), before)

    def test_empty_cache_and_mismatched_target_do_not_write(self):
        before = self.snapshot('signals')
        self.assertEqual(model_refresh.refresh_cached_predictions(core, target='2026-10-15')['status'], 'skipped')
        self.assertEqual(self.snapshot('signals'), before)
        with core.connect() as con:
            con.execute('DELETE FROM signals')
        self.assertEqual(model_refresh.refresh_cached_predictions(core)['status'], 'noop')
        self.analyze.assert_not_called()


if __name__ == '__main__':
    unittest.main()
