import copy
import datetime as dt
import unittest
from unittest.mock import patch

import main_wave as wave


def bars(up=True):
    start = dt.date(2026, 5, 1)
    days = [start + dt.timedelta(days=i) for i in range(120)]
    days = [d.isoformat() for d in days if d.weekday() < 5][-65:]
    rows = []
    for i, day in enumerate(days):
        p = 10 + i * .05 if up else 15 - i * .05
        rows.append(dict(date=day, open=p-.02, high=p+.03, low=p-.04, close=p, volume=100, raw_close=p))
    return rows


def sector():
    return dict(industry='测试行业', peer_count=10, rank20=1, return20_mean_pct=5., above_ma20_ratio=.8,
                membership_at='2026-08-28T16:00:00+08:00', passed=True, basis='official_industry_proxy', theme_verified=False)


class MainWaveTests(unittest.TestCase):
    def setUp(self):
        self.rows = bars(); self.index = bars(); self.target = self.rows[-1]['date']
        self.rows[-1]['volume'] = 180
        self.rows[-1]['high'] = self.rows[-1]['close'] + .01

    def test_breakout_is_a_condition_not_a_probability_or_order(self):
        result = wave.evidence(self.rows, self.index, self.target, sector())
        self.assertTrue(result['passed']); self.assertEqual(result['phase'], 'volume_breakout')
        self.assertTrue(wave.valid(result, self.target))
        self.assertIsNone(result['launch_probability_pct']); self.assertIsNone(result['limit_up_probability_pct'])
        self.assertEqual(result['suggested_position_pct'], 0)
        self.assertFalse(result['intraday_confirmed'])
        self.assertEqual(len(wave.decision(result)), 4)

    def test_weak_or_flat_market_does_not_authorize_heavy_position(self):
        for index in (bars(False), [dict(r, open=10, high=10, low=10, close=10) for r in self.index]):
            result = wave.assess(self.rows, index, self.target, sector())
            self.assertFalse(result['passed']); self.assertEqual(result['action'], '空仓等待')
            self.assertTrue(result['failure'].startswith('market_'))

    def test_detail_facts_do_not_change_frozen_evidence(self):
        result=wave.evidence(self.rows,self.index,self.target,sector());original=copy.deepcopy(result)
        facts={item['label']:item['value'] for item in wave.decision(result)[0]['facts']}
        self.assertEqual(facts['成交量 / 前20日均量'],'1.80 倍')
        self.assertEqual(facts['同行站上MA20比例'],'80.00%')
        self.assertEqual(result,original);self.assertTrue(wave.valid(result,self.target))

    def test_intraday_distribution_and_excessive_volume_do_not_become_breakouts(self):
        for volume, high in ((180, 16), (700, self.rows[-1]['high'])):
            rows = copy.deepcopy(self.rows); rows[-1].update(volume=volume, high=high)
            self.assertFalse(wave.assess(rows, self.index, self.target, sector())['passed'])

    def test_future_rows_do_not_change_signal_and_missing_alignment_is_rejected(self):
        expected = wave.assess(self.rows, self.index, self.target, sector())
        future = dict(self.rows[-1], date='2099-01-01', close=100000)
        self.assertEqual(expected, wave.assess(self.rows + [future], self.index + [future], self.target, sector()))
        self.assertFalse(wave.assess(self.rows[:-2]+self.rows[-1:], self.index, self.target, sector())['passed'])

    def test_unknown_sector_is_not_called_a_leader(self):
        result = wave.assess(self.rows, self.index, self.target)
        self.assertEqual(result['failure'], 'sector_unverified')
        self.assertIn('未确认', wave.decision(result)[0]['text'])
        self.assertFalse(wave.assess(self.rows, self.index, self.target, dict(sector(), passed=False))['passed'])

    def test_archived_math_and_probability_tampering_are_rejected(self):
        original = wave.evidence(self.rows, self.index, self.target, sector())
        for field, value in [('passed', False), ('launch_probability_pct', 80), ('suggested_position_pct', 60), ('phase','dry_pullback')]:
            self.assertFalse(wave.valid(dict(original, **{field:value}), self.target))
        changed = copy.deepcopy(original); changed['metrics']['ma10'] += 1
        self.assertFalse(wave.valid(changed, self.target))

    def test_cash_market_proof_is_recomputed(self):
        proof = wave.market(bars(False), self.target)
        self.assertTrue(wave.valid_market(proof, self.target))
        self.assertFalse(wave.valid_market(dict(proof, regime='strong'), self.target))
        self.assertFalse(wave.valid_market(dict(proof, closes=proof['closes'][:-1]), self.target))

    def test_sector_membership_future_and_stale_records_are_not_used(self):
        moment = dt.datetime.fromisoformat(self.target+'T18:00:00+08:00')
        stocks = [dict(symbol=f'sh60000{i}', date=self.target, stale=False, excluded=False,
                       close=11, ma20=10, momentum20=10-i) for i in range(10)]
        membership = dict(source='https://www.sse.com.cn/assortment/stock/list/share/',
                          fetched_at=moment.isoformat(), industries={s['symbol']:'行业' for s in stocks})
        contexts = wave.sectors(stocks, membership, self.target, moment)
        self.assertTrue(contexts['sh600000']['passed']); self.assertFalse(contexts['sh600009']['passed'])
        self.assertFalse(contexts['sh600000']['theme_verified'])
        self.assertEqual(wave.sectors(stocks, dict(membership, fetched_at='2099-01-01T00:00:00+08:00'), self.target, moment), {})
        self.assertEqual(wave.sectors(stocks, dict(membership, fetched_at='2020-01-01T00:00:00+08:00'), self.target, moment), {})

    def test_nonfinite_and_invalid_ohlc_are_rejected(self):
        for key, value in [('close', float('nan')), ('volume', 0), ('low', 999)]:
            rows = copy.deepcopy(self.rows); rows[-1][key] = value
            self.assertFalse(wave.assess(rows, self.index, self.target, sector())['passed'])
        self.assertFalse(wave.assess([], [], None)['passed'])

    def test_dry_pullback_and_original_risk_gate(self):
        rows = copy.deepcopy(self.rows)
        p = sum(r['close'] for r in rows[-5:])/5
        rows[-1].update(close=p, open=p-.02, high=p+.03, low=p-.04, volume=60)
        result = wave.assess(rows, self.index, self.target, sector())
        self.assertTrue(result['passed']); self.assertEqual(result['phase'], 'dry_pullback')
        wave.apply_stock_gates(result, {'stale':False, 'chase_risk':{'gate_passed':False}})
        self.assertFalse(result['passed']); self.assertEqual(result['action'], '空仓等待')

    def test_cash_is_persisted_and_released_without_inventing_a_stock(self):
        from test_daily_watchlist import WatchlistTests
        import daily_watchlist as watch
        case = WatchlistTests(); case.setUp()
        try:
            with patch.object(watch, 'RANKING_POLICY', wave.POLICY):
                early = watch.publish(service=case.service, now=case.moment.replace(hour=8))
                self.assertEqual(early['status'], 'cash'); self.assertEqual(early['items'], [])
                self.assertIsNone(early['published_at'])
                self.assertTrue(wave.valid_market(early['main_wave_market'], early['data_date']))
                later = watch.publish(service=case.service, now=case.moment)
                self.assertEqual(later['status'], 'cash'); self.assertIsNone(later['top_pick'])
                self.assertEqual(later['published_at'], case.moment.isoformat())
                self.assertEqual(watch.read(service=case.service, now=case.moment)['published_at'], later['published_at'])
        finally:
            case.tearDown()


if __name__ == '__main__':
    unittest.main()
