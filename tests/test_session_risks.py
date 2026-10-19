"""Tests use actual public SSE records fetched 2026-10-18T21:51:35+08:00.

Original response SHA256: 597cdcae6511cc14adcf11eea9f206635b927a440a83da62d3a0ec077e19784f.
Fixture rows below preserve all nine source fields; pageHelp.data is reconstructed
from that identical result list to avoid embedding the same five records twice.
Malformed mutations test rejection paths; they are not claimed as source facts.
"""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import session_risks as sr

NOW = dt.datetime(2026, 10, 18, 21, 51, 35, tzinfo=sr.TZ)
REAL_ROWS = [
    {'startStopDate':'20261019','stopReason':'重要公告','productCode':'113605','endStopReason':'摘牌','controlType':'GP','endStopDate':'20261022','stopTime':'','type':'LXTP','productName':'大参转债'},
    {'startStopDate':'20261019','stopReason':'重要公告','productCode':'113631','endStopReason':'','controlType':'GB','endStopDate':'','stopTime':'','type':'LXTP','productName':'皖天转债'},
    {'startStopDate':'20261015','stopReason':'重要公告','productCode':'110084','endStopReason':'重要公告','controlType':'GB','endStopDate':'20261022','stopTime':'','type':'LXTP','productName':'贵燃转债'},
    {'startStopDate':'20260929','stopReason':'拟筹划重大资产重组','productCode':'600293','endStopReason':'','controlType':'TR','endStopDate':'','stopTime':'','type':'LXTP','productName':'三峡新材'},
    {'startStopDate':'20260911','stopReason':'重要公告','productCode':'688496','endStopReason':'','controlType':'TR','endStopDate':'','stopTime':'','type':'LXTP','productName':'*ST清越'},
]


def fixture():
    rows = copy.deepcopy(REAL_ROWS)
    return {'actionErrors': [], 'actionMessages': [], 'fieldErrors': {}, 'isPagination':'true',
            'sqlId':sr.SQL_ID, 'result':rows,
            'pageHelp':{'data':copy.deepcopy(rows),'pageCount':1,'pageNo':1,'pageSize':25,'total':5}}


def receipt():
    return {'http_status':200,'content_type':'application/json;charset=UTF-8',
            'url':sr.ENDPOINT, 'final_url':sr.ENDPOINT,
            'response_sha256':'597cdcae6511cc14adcf11eea9f206635b927a440a83da62d3a0ec077e19784f','bytes':2491}


class SessionRiskTests(unittest.TestCase):
    def collect_payload(self, payload):
        with patch.object(sr, '_request', return_value=(payload, receipt())):
            return sr.collect('2026-10-19', NOW)

    def test_real_official_rows_exclude_only_equities(self):
        with patch.object(sr, '_request', return_value=(fixture(), receipt())) as fetch:
            result = sr.collect('2026-10-19', NOW)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['scope']['source_scope_complete'])
        self.assertEqual(len(result['records']), 5)
        self.assertEqual({row['symbol'] for row in result['risk_exclusions']}, {'sh600293','sh688496'})
        self.assertTrue(all(row['symbol'] is None for row in result['records'][:3]))
        self.assertTrue(all(row['resumption_confirmed'] is False for row in result['records']))
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params['startStopDate'], ['20261019'])
        self.assertEqual(params['endStopDate'], ['20261019'])
        self.assertEqual(params['sqlId'], [sr.SQL_ID])
        self.assertEqual(result['receipt']['total'],5)
        self.assertFalse(result['scope']['historical_information_set_safe'])

    def test_empty_is_not_complete_or_risk_clearance(self):
        data = fixture()
        data['result'] = data['pageHelp']['data'] = []
        data['pageHelp'].update(total=0,pageCount=0)
        result = self.collect_payload(data)
        self.assertEqual(result['status'],'empty_unconfirmed')
        self.assertFalse(result['scope']['source_scope_complete'])
        self.assertEqual(result['risk_exclusions'],[])
        self.assertTrue(result['errors'])

    def test_duplicate_records_fail_complete_scope(self):
        data = fixture()
        data['result'][1] = copy.deepcopy(data['result'][0])
        data['pageHelp']['data'] = copy.deepcopy(data['result'])
        self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_page_number_mismatch(self):
        data = fixture()
        data['pageHelp']['pageNo'] = 2
        self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_total_or_pagecount_mismatch(self):
        for field, value in [('total',6),('pageCount',2),('pageSize',200)]:
            data = fixture()
            data['pageHelp'][field] = value
            self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_result_and_page_data_must_match(self):
        data = fixture()
        data['pageHelp']['data'] = []
        self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_bad_date_or_outside_query_range_is_unavailable(self):
        for field, value in [('startStopDate','20261319'),('startStopDate','20261020'),('endStopDate','20261016')]:
            data = fixture()
            data['result'][3][field] = value
            data['pageHelp']['data'] = copy.deepcopy(data['result'])
            self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_unknown_semantics_are_not_silently_ignored(self):
        for field, value in [('controlType','NEW'),('stopTime','NEW'),('type','NEW')]:
            data = fixture()
            data['result'][3][field] = value
            data['pageHelp']['data'] = copy.deepcopy(data['result'])
            self.assertEqual(self.collect_payload(data)['status'],'unavailable')

    def test_ending_on_target_does_not_prove_resumption(self):
        data = fixture()
        data['result'][3].update(endStopDate='20261019',endStopReason='终止上市')
        data['pageHelp']['data'] = copy.deepcopy(data['result'])
        result = self.collect_payload(data)
        self.assertEqual(result['status'],'complete')
        self.assertIn('sh600293',{row['symbol'] for row in result['risk_exclusions']})
        self.assertFalse(result['records'][3]['resumption_confirmed'])

    def test_naive_time_or_past_reconstruction_is_rejected_without_fetch(self):
        with patch.object(sr,'_request') as fetch:
            self.assertEqual(sr.collect('2026-10-19',NOW.replace(tzinfo=None))['status'],'unavailable')
            self.assertEqual(sr.collect('2026-10-17',NOW)['status'],'unavailable')
            self.assertEqual(sr.collect('2026-02-30',NOW)['status'],'unavailable')
            fetch.assert_not_called()

    def test_aware_time_is_converted_to_shanghai(self):
        with patch.object(sr,'_request',return_value=(fixture(),receipt())):
            result = sr.collect('2026-10-19', NOW.astimezone(dt.timezone.utc))
        self.assertEqual(result['as_of'],NOW.isoformat())

    def test_real_clock_records_start_response_and_completion_separately(self):
        start = NOW
        received = NOW + dt.timedelta(seconds=3)
        completed = NOW + dt.timedelta(seconds=4)
        with patch.object(sr,'_clock',side_effect=[start,received,completed]), patch.object(sr,'_request',return_value=(fixture(),receipt())):
            result = sr.collect('2026-10-19')
        self.assertEqual(result['collection_started_at'],start.isoformat())
        self.assertEqual(result['receipt']['pages'][0]['fetched_at'],received.isoformat())
        self.assertEqual(result['as_of'],completed.isoformat())

    def test_failed_request_reports_actual_completion_time(self):
        completed = NOW + dt.timedelta(seconds=20)
        with patch.object(sr,'_clock',side_effect=[NOW,completed]), patch.object(sr,'_request',side_effect=OSError('timeout')):
            result = sr.collect('2026-10-19')
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['collection_started_at'],NOW.isoformat())
        self.assertEqual(result['as_of'],completed.isoformat())

    def test_html_or_transport_failure_is_unavailable(self):
        for error in (OSError('offline'),json.JSONDecodeError('HTML is not JSON','<html></html>',0)):
            with patch.object(sr,'_request',side_effect=error):
                result = sr.collect('2026-10-19',NOW)
            self.assertEqual(result['status'],'unavailable')
            self.assertFalse(result['scope']['source_scope_complete'])
            self.assertEqual(result['risk_exclusions'],[])


if __name__ == '__main__':
    unittest.main()
