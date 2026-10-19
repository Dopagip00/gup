import datetime as dt
import json
import math
from pathlib import Path
import sys
import urllib.parse
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_data as md


class NetworkErrorTests(unittest.TestCase):
    def test_transport_and_http_failures_have_a_distinct_safe_type(self):
        for cause in [OSError('private network detail'), md.urllib.error.URLError('private network detail'),
                      md.urllib.error.HTTPError('https://query.sse.com.cn/?private=detail', 403, 'denied', None, None)]:
            with self.subTest(cause=type(cause).__name__), patch.object(md, '_throttle'), patch.object(md.time, 'sleep'), patch.object(md.urllib.request, 'urlopen', side_effect=cause):
                with self.assertRaises(md.MarketNetworkError) as error:
                    md._request(md.SSE_LIST_APIS[0])
                self.assertIn('query.sse.com.cn: request failed after 3 attempts', str(error.exception))
                self.assertNotIn('private', str(error.exception))

    def test_bad_encoding_is_data_failure_even_if_a_later_retry_has_a_network_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'\xff'
        for failures in [[response] * 3, [response, OSError('offline'), OSError('offline')]]:
            with self.subTest(mixed=failures[1] is not response), patch.object(md, '_throttle'), patch.object(md.time, 'sleep'), patch.object(md.urllib.request, 'urlopen', side_effect=failures):
                with self.assertRaises(md.MarketDataError) as error:
                    md._request(md.SSE_LIST_APIS[0])
                self.assertNotIsInstance(error.exception, md.MarketNetworkError)

    def test_invalid_url_or_json_does_not_become_a_network_failure(self):
        with patch.object(md, '_throttle'), patch.object(md.time, 'sleep'), patch.object(md.urllib.request, 'urlopen', side_effect=ValueError('invalid URL')):
            with self.assertRaises(md.MarketDataError) as error:
                md._request(md.SSE_LIST_APIS[0])
            self.assertNotIsInstance(error.exception, md.MarketNetworkError)
        with patch.object(md, '_request', return_value='{invalid JSON'):
            with self.assertRaises(md.MarketDataError) as error:
                md._json(md.SSE_LIST_APIS[0])
            self.assertNotIsInstance(error.exception, md.MarketNetworkError)


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
    @staticmethod
    def sse_payload():
        return {'result': [{'A_STOCK_CODE': '600000', 'SEC_NAME_CN': '浦发银行'},
                           {'A_STOCK_CODE': '688981', 'SEC_NAME_CN': '中芯国际'}],
                'pageHelp': {'total': 2}}

    def test_sse_primary_route_is_preferred_and_records_actual_api(self):
        with patch.object(md, '_json', return_value=self.sse_payload()) as request:
            rows = md.get_universe('shanghai')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(urllib.parse.urlsplit(request.call_args.args[0]).path, '/sseQuery/commonQuery.do')
        self.assertTrue(all(row['source_api'] == md.SSE_LIST_APIS[0] for row in rows))
        self.assertTrue(all(row['source'] == md.SSE_LIST_SOURCE for row in rows))

    def test_sse_official_same_host_fallback_keeps_identical_query_and_full_validation(self):
        with patch.object(md, '_json', side_effect=[md.MarketDataError('HTTP 403'), self.sse_payload()]) as request:
            rows = md.get_universe('shanghai')
        calls = [urllib.parse.urlsplit(call.args[0]) for call in request.call_args_list]
        self.assertEqual([call.hostname for call in calls], ['query.sse.com.cn'] * 2)
        self.assertEqual([call.scheme for call in calls], ['https'] * 2)
        self.assertEqual([call.path for call in calls], ['/sseQuery/commonQuery.do', '/commonQuery.do'])
        self.assertEqual(calls[0].query, calls[1].query)
        self.assertTrue(all(call.kwargs['referer'] == md.SSE_LIST_SOURCE for call in request.call_args_list))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['source_api'] == md.SSE_LIST_APIS[1] for row in rows))

    def test_sse_primary_incomplete_or_duplicate_response_can_only_fall_back_to_an_independent_complete_list(self):
        for invalid in [
            {'result': self.sse_payload()['result'][:1], 'pageHelp': {'total': 2}},
            {'result': [self.sse_payload()['result'][0]] * 2, 'pageHelp': {'total': 2}},
            {'result': [], 'pageHelp': None},
            {'result': [None], 'pageHelp': {'total': 1}},
        ]:
            with self.subTest(invalid=invalid), patch.object(md, '_json', side_effect=[invalid, self.sse_payload()]) as request:
                rows = md.get_universe('shanghai')
                self.assertEqual(request.call_count, 2)
                self.assertEqual([row['symbol'] for row in rows], ['sh600000', 'sh688981'])
                self.assertTrue(all(row['source_api'] == md.SSE_LIST_APIS[1] for row in rows))

    def test_sse_fallback_never_returns_partial_or_duplicate_universe(self):
        for invalid in [
            {'result': self.sse_payload()['result'][:1], 'pageHelp': {'total': 2}},
            {'result': [self.sse_payload()['result'][0]] * 2, 'pageHelp': {'total': 2}},
        ]:
            with self.subTest(invalid=invalid), patch.object(md, '_json', side_effect=[md.MarketDataError('HTTP 403'), invalid]):
                with self.assertRaises(md.MarketDataError) as error:
                    md.get_universe('shanghai')
                self.assertIn('/sseQuery/commonQuery.do: HTTP 403', str(error.exception))
                self.assertIn('/commonQuery.do:', str(error.exception))

    def test_sse_both_official_routes_failing_reports_both_errors(self):
        with patch.object(md, '_json', side_effect=[md.MarketNetworkError('HTTP 403'), md.MarketNetworkError('HTTP 503')]) as request:
            with self.assertRaises(md.MarketNetworkError) as error:
                md.get_universe('shanghai')
        self.assertEqual(request.call_count, 2)
        self.assertIn('/sseQuery/commonQuery.do: HTTP 403', str(error.exception))
        self.assertIn('/commonQuery.do: HTTP 503', str(error.exception))
        self.assertNotIn('sqlId=', str(error.exception))

    def test_sse_any_data_failure_prevents_network_only_classification(self):
        invalid = {'result': self.sse_payload()['result'][:1], 'pageHelp': {'total': 2}}
        for failures in [[invalid, md.MarketNetworkError('HTTP 403')],
                         [md.MarketNetworkError('HTTP 403'), invalid],
                         [md.MarketDataError('invalid JSON'), md.MarketNetworkError('HTTP 403')]]:
            with self.subTest(primary=type(failures[0]).__name__), patch.object(md, '_json', side_effect=failures):
                with self.assertRaises(md.MarketDataError) as error:
                    md.get_universe('shanghai')
                self.assertNotIsInstance(error.exception, md.MarketNetworkError)
                self.assertIn('/sseQuery/commonQuery.do:', str(error.exception))
                self.assertIn('/commonQuery.do:', str(error.exception))

    def test_sse_fallback_does_not_mask_unexpected_programming_errors(self):
        with patch.object(md, '_json', side_effect=ValueError('unexpected fixture error')) as request:
            with self.assertRaises(ValueError):
                md.get_universe('shanghai')
        self.assertEqual(request.call_count, 1)

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
