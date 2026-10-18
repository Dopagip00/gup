import datetime as dt
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_data as md


class CalendarTests(unittest.TestCase):
    def test_weekend_makeup_never_opens_stock_exchange(self):
        self.assertFalse(md.is_trading_day('2026-10-10'))
        self.assertFalse(md.is_trading_day('2026-02-14'))
        self.assertFalse(md.is_trading_day('2026-10-01'))
        self.assertTrue(md.is_trading_day('2026-10-08'))

    def test_completed_session_cutoff_and_holiday(self):
        self.assertEqual(md.last_completed_session(dt.datetime(2026, 10, 8, 15, 19, tzinfo=md.CHINA)), '2026-09-30')
        self.assertEqual(md.last_completed_session(dt.datetime(2026, 10, 8, 15, 20, tzinfo=md.CHINA)), '2026-10-08')
        self.assertEqual(md.last_completed_session(dt.datetime(2026, 10, 17, 10, 0, tzinfo=md.CHINA)), '2026-10-16')

    @patch.object(md, '_request', side_effect=md.MarketDataError('offline'))
    def test_unknown_year_fails_closed(self, request):
        with self.assertRaises(md.CalendarUnavailable):
            md.is_trading_day('2040-01-03')

    def test_parse_annual_page(self):
        page = '2027年休市安排 元旦：1月1日（星期五）至1月3日（星期日）休市。春节：2月5日（星期五）至2月13日（星期六）休市。清明节：4月4日（星期日）至4月6日（星期二）休市。劳动节：5月1日（星期六）至5月5日（星期三）休市。端午节：6月8日（星期二）至6月10日（星期四）休市。中秋节：9月15日（星期三）至9月17日（星期五）休市。国庆节：10月1日（星期五）至10月7日（星期四）休市。相关公告'
        parsed = md._parse_calendar_page(page, 2027)
        self.assertIn('2027-02-13', parsed['holidays'])
        self.assertNotIn('2027-10-08', parsed['holidays'])
        with self.assertRaises(md.CalendarUnavailable):
            md._parse_calendar_page(page, 2028)


def history(rows, kind='qfqday'):
    return {'code': 0, 'data': {'sh600519': {kind: rows, 'qt': {'sh600519': ['', '贵州茅台']}}}}


class HistoryTests(unittest.TestCase):
    def test_tencent_column_order(self):
        rows, _ = md._parse_bars(history([['2026-10-16', '10', '11', '12', '9', '123']]), 'sh600519', 'qfq')
        self.assertEqual(rows[0]['close'], 11)
        self.assertEqual(rows[0]['high'], 12)
        self.assertEqual(rows[0]['low'], 9)

    @patch.object(md, '_now', return_value=dt.datetime(2026, 10, 16, 14, 0, tzinfo=md.CHINA))
    def test_aligns_raw_prices_and_excludes_incomplete_today(self, now):
        q = history([['2026-10-15', 9, 10, 11, 8, 100], ['2026-10-16', 10, 11, 12, 9, 100]])
        r = history([['2026-10-15', 18, 20, 22, 16, 100], ['2026-10-16', 20, 22, 24, 18, 100]], 'day')
        with patch.object(md, '_json', side_effect=[q, r]):
            result = md.fetch_history('sh600519')
        self.assertEqual(len(result['bars']), 1)
        self.assertEqual(result['bars'][0]['raw_close'], 20)
        self.assertFalse(result['stale'])

    @patch.object(md, '_now', return_value=dt.datetime(2026, 10, 17, 10, 0, tzinfo=md.CHINA))
    def test_old_history_marked_stale(self, now):
        q = history([['2026-10-15', 9, 10, 11, 8, 100]])
        r = history([['2026-10-15', 9, 10, 11, 8, 100]], 'day')
        with patch.object(md, '_json', side_effect=[q, r]):
            self.assertTrue(md.fetch_history('sh600519')['stale'])

    @patch.object(md, '_now', return_value=dt.datetime(2026, 10, 17, 10, 0, tzinfo=md.CHINA))
    def test_missing_raw_date_rejected(self, now):
        q = history([['2026-10-16', 9, 10, 11, 8, 100]])
        r = history([['2026-10-15', 9, 10, 11, 8, 100]], 'day')
        with patch.object(md, '_json', side_effect=[q, r]), patch.object(md, '_eastmoney_history', side_effect=md.MarketDataError('fixture unavailable')), self.assertRaises(md.MarketDataError):
            md.fetch_history('sh600519')

    def test_nan_and_duplicate_dates_rejected(self):
        for rows in ([['2026-10-16', 9, 'NaN', 11, 8, 100]],
                     [['2026-10-16', 9, 10, 11, 8, 100]] * 2):
            with self.assertRaises(md.MarketDataError):
                md._parse_bars(history(rows), 'sh600519', 'qfq')

    def test_adjustment_does_not_silently_fallback(self):
        with self.assertRaises(md.MarketDataError):
            md._parse_bars(history([['2026-10-16', 9, 10, 11, 8, 100]], 'day'), 'sh600519', 'qfq')

    @patch.object(md, '_now', return_value=dt.datetime(2026, 10, 17, 10, 0, tzinfo=md.CHINA))
    def test_unadjusted_provider_window_is_explicitly_cross_checked(self, now):
        q = history([['2026-10-16', 9, 10, 11, 8, 100]], 'day')
        with patch.object(md, '_json', side_effect=[q, q]):
            result = md.fetch_history('sh600519')
        self.assertEqual(result['provider_adjustment_key'], 'day')
        self.assertEqual(result['bars'][0]['close'], result['bars'][0]['raw_close'])
        changed = history([['2026-10-16', 10, 11, 12, 9, 100]], 'day')
        with patch.object(md, '_json', side_effect=[q, changed]), patch.object(md, '_eastmoney_history', side_effect=md.MarketDataError('fixture unavailable')), self.assertRaises(md.MarketDataError):
            md.fetch_history('sh600519')

    def test_star_volume_normalized(self):
        payload = {'data': {'sh688981': {'day': [['2026-10-16', 100, 101, 102, 99, 37855220]]}}}
        bars, _ = md._parse_bars(payload, 'sh688981', 'raw')
        self.assertEqual(bars[0]['volume'], 378552.2)
        self.assertEqual(bars[0]['volume_shares'], 37855220)

    def test_eastmoney_star_volume_already_lots(self):
        data = {'data': {'code': '688981', 'name': '中芯国际', 'klines': ['2026-10-16,100,101,102,99,378552.2,3800000000,0,0,0,0']}}
        bars, name = md._parse_eastmoney_bars(data, 'sh688981')
        self.assertEqual(bars[0]['volume'], 378552.2)
        self.assertEqual(bars[0]['volume_shares'], 37855220)

    @patch.object(md, '_now', return_value=dt.datetime(2026, 10, 17, 10, 0, tzinfo=md.CHINA))
    def test_fallback_switches_whole_pair_and_records_source(self, now):
        q = {'data': {'code': '600519', 'name': '贵州茅台', 'klines': ['2026-10-16,9,10,11,8,100,100000,0,0,0,0']}}
        r = {'data': {'code': '600519', 'name': '贵州茅台', 'klines': ['2026-10-16,18,20,22,16,100,100000,0,0,0,0']}}
        with patch.object(md, '_tencent_history', side_effect=md.MarketDataError('HTTP 501')), patch.object(md, '_json', side_effect=[q, r]):
            result = md.fetch_history('sh600519')
        self.assertEqual(result['source'], md.EASTMONEY_HISTORY_SOURCE)
        self.assertEqual(result['bars'][0]['raw_close'], 20)
        self.assertEqual(result['warnings'], ['HTTP 501'])

    def test_eastmoney_partial_alignment_rejected(self):
        q = {'data': {'code': '600519', 'klines': ['2026-10-16,9,10,11,8,100,100000']}}
        r = {'data': {'code': '600519', 'klines': ['2026-10-15,9,10,11,8,100,100000']}}
        with patch.object(md, '_json', side_effect=[q, r]), self.assertRaises(md.MarketDataError):
            md._eastmoney_history('sh600519', 640, dt.datetime(2026, 10, 17, tzinfo=md.CHINA), '2026-10-16')


class UniverseQuoteTests(unittest.TestCase):
    def test_sse_completeness_and_flags(self):
        rows = [{'A_STOCK_CODE': '600000', 'SEC_NAME_CN': '*ST测试'}, {'A_STOCK_CODE': '688981', 'SEC_NAME_CN': '中芯国际'}]
        value = md._parse_sse_universe({'result': rows, 'pageHelp': {'total': 2}})
        self.assertTrue(value[0]['is_st'])
        self.assertFalse(value[0]['actionable'])
        self.assertEqual(value[1]['board'], 'star')
        with self.assertRaises(md.MarketDataError):
            md._parse_sse_universe({'result': rows, 'pageHelp': {'total': 3}})
        with self.assertRaises(md.MarketDataError):
            md._parse_sse_universe({'result': [rows[0], rows[0]], 'pageHelp': {'total': 2}})

    def test_tencent_quote(self):
        fields = ['0'] * 50
        for i, v in {1: '贵州茅台', 3: '1329', 4: '1312.97', 5: '1311.57', 6: '38502', 30: '20261016161447', 32: '1.22', 33: '1329.88', 34: '1301.68', 39: '20.4', 46: '6.61', 45: '16613.58', 44: '16613.58'}.items():
            fields[i] = v
        text = 'v_sh600519="' + '~'.join(fields) + '";'
        row = md._parse_quotes(text, ['sh600519'])['sh600519']
        self.assertEqual(row['price'], 1329)
        self.assertEqual(row['datetime'], '2026-10-16T16:14:47+08:00')
        self.assertEqual(row['name'], '贵州茅台')
        self.assertEqual(row['pe'], 20.4)
        self.assertEqual(row['pb'], 6.61)
        self.assertEqual(row['market_cap_100m'], 16613.58)
        with self.assertRaises(md.MarketDataError):
            md._parse_quotes(text, ['sh600519', 'sh600000'])


if __name__ == '__main__':
    unittest.main()
