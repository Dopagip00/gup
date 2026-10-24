import copy
import datetime as dt
import unittest

import factor_screen as screen
import daily_watchlist as watch


class FactorTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat('2026-10-24T12:00:00+08:00')
        self.target = '2026-10-23'
        self.record = dict(symbol='sh600007', date=self.target, quote_date=self.target, score=99,
            pe_ttm=15, pb=2, roe=10, revenue_growth=8, profit_growth=12, operating_cash_to_profit=1,
            recurring_profit_ratio=.8, debt_ratio=40, ttm_parent_netprofit_100m=2,
            report_date='2026-06-30', publication_date='2026-08-20', provider_update_date='2026-08-20',
            roe_report_date='2025-12-31', roe_publication_date='2026-04-20')
        self.snapshot = dict(as_of=self.target, fetched_at='2026-10-23T16:00:00+08:00', stocks=[self.record])
        self.stock = dict(symbol='sh600007', date=self.target, stale=False, score=99,
            chase_risk=dict(version='anti-chase-v1', as_of_date=self.target, gate_passed=True))
        self.wave = dict(as_of=self.target, market=dict(regime='strong'), metrics=dict(trend_aligned=True),
            phase='volume_breakout', sector=dict(passed=True, basis='official_industry_proxy'))

    def report(self):
        return screen.report(self.stock, self.wave, self.snapshot, self.now)

    def test_complete_dates_admit_current_evidence(self):
        self.assertTrue(watch._financial(self.record, self.snapshot, self.target, self.now))
        self.assertEqual(self.report()['status'], 'pass')

    def test_future_missing_nonannual_and_old_financial_evidence_never_get_full_weight(self):
        for change in ({'roe_publication_date': None}, {'roe_publication_date': '2026-10-25'},
                       {'roe_report_date': '2026-06-30'}, {'roe_report_date': '2026-12-31'},
                       {'report_date': '2025-12-31'}, {'provider_update_date': '2026-10-25'}):
            with self.subTest(change=change):
                record = dict(self.record, **change)
                self.assertFalse(watch._financial(record, self.snapshot, self.target, self.now))
                self.assertIsNone(watch._disclosed_quality(record, self.snapshot, self.target, self.now))

    def test_score_does_not_override_individual_failure(self):
        self.record['operating_cash_to_profit'] = .1
        result = self.report()
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['groups'][1]['status'], 'fail')
        self.assertFalse(result['ranking_applied'])
        self.assertEqual(self.record['score'], 99)

    def test_all_conditions_must_pass_unknown_is_not_pass(self):
        self.record.pop('roe')
        self.assertEqual(self.report()['status'], 'unknown')
        self.wave['metrics']['trend_aligned'] = False
        self.assertEqual(self.report()['status'], 'fail')

    def test_boolean_nan_and_infinite_are_not_numeric_evidence(self):
        for value in (True, float('nan'), float('inf'), '10', None):
            self.record['roe'] = value
            self.assertEqual(self.report()['groups'][1]['status'], 'unknown')

    def test_old_operating_facts_do_not_relabel_old_valuation(self):
        self.snapshot['as_of'] = self.record['date'] = self.record['quote_date'] = '2026-10-22'
        result = self.report()
        by_key = {r['key']: r for r in result['groups'][1]['items']}
        self.assertEqual(by_key['pe_ttm']['status'], 'unknown')
        self.assertIsNone(by_key['pe_ttm']['value'])
        self.assertEqual(by_key['roe']['status'], 'pass')
        self.assertEqual(result['financial_snapshot_date'], '2026-10-22')

    def test_stale_and_duplicate_records_are_not_combined(self):
        self.snapshot['stocks'].append(copy.deepcopy(self.record))
        self.assertEqual(self.report()['groups'][1]['status'], 'unknown')
        self.stock['stale'] = True
        self.assertTrue(all(g['status'] == 'unknown' for g in self.report()['groups']))

    def test_future_fetch_and_naive_times_rejected(self):
        for fetched in ('2026-10-25T16:00:00+08:00', '2026-10-23T16:00:00'):
            self.snapshot['fetched_at'] = fetched
            self.assertFalse(watch._financial(self.record, self.snapshot, self.target, self.now))

    def test_missing_snapshot_is_safe(self):
        for snapshot in (None, {}, {'stocks': None}):
            self.assertEqual(screen.report(self.stock, self.wave, snapshot, self.now)['groups'][1]['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
