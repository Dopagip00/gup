import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import universe_receipt as receipt


class MarketDataError(Exception):pass
class MarketNetworkError(MarketDataError):pass


DAY='2026-10-21'
START=dt.datetime(2026,10,21,8,0,tzinfo=receipt.TZ)
FETCHED=START+dt.timedelta(minutes=1)
FINISH=START+dt.timedelta(minutes=2)
NOW=START+dt.timedelta(minutes=3)
ENV={'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'123456','RUNNER_OS':'Windows'}


def make_record():
    value={'protocol':receipt.PROTOCOL,'date':DAY,'source':receipt.SOURCE,
        'source_api':sorted(receipt.APIS)[0],'count':2000,
        'rows':[{'symbol':f'sh{600000+i}','name':f'测试公司{i}',
                 'listing_date':'2020-01-01','industry':'制造业'} for i in range(2000)],
        'collection_started_at':START.isoformat(),'fetched_at':FETCHED.isoformat(),
        'collection_finished_at':FINISH.isoformat(),'collector_runner':'Windows',
        'collector_context':'github_actions','workflow_run_id':'123456'}
    value['sha256']=receipt._hash(value)
    return value


def rehash(value):
    value['sha256']=receipt._hash({k:v for k,v in value.items() if k!='sha256'})
    return value


def make_md():
    return types.SimpleNamespace(SSE_LIST_SOURCE=receipt.SOURCE,SSE_LIST_APIS=tuple(receipt.APIS),
        MarketDataError=MarketDataError,MarketNetworkError=MarketNetworkError,
        _flags=lambda symbol,name:{'is_st':name.startswith('ST'),'actionable':not name.startswith('ST')},
        is_trading_day=Mock(return_value=True),_sse_universe=Mock(),
        get_universe=Mock(side_effect=MarketNetworkError('HTTP 403 https://secret.invalid/path?token=private')))


class FakeCore:
    def __init__(self,md):self.md=md;self.values={};self.now=lambda:NOW
    def getkv(self,key,default=None):return self.values.get(key,default)
    def putkv(self,key,value):self.values[key]=value


class UniverseReceiptTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,ENV,clear=False);self.env.start();self.addCleanup(self.env.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.md=make_md();self.record=make_record()

    def write(self,value=None,path=None):
        path=path or self.directory/(DAY+'.json')
        path.write_text(receipt._canonical(value or self.record),encoding='utf-8')
        return path

    def test_valid_receipt_restores_flags_and_original_collection_evidence(self):
        self.record['rows'][0]['name']='ST测试';self.record['rows'][0]['industry']=None;rehash(self.record)
        rows,meta=receipt.validate(self.record,NOW,self.md)
        self.assertEqual(len(rows),2000);self.assertTrue(rows[0]['is_st']);self.assertFalse(rows[0]['actionable'])
        self.assertEqual(rows[0]['fetched_at'],FETCHED.isoformat())
        self.assertEqual(meta['original_fetched_at'],FETCHED.isoformat())
        self.assertEqual(meta['checked_at'],NOW.isoformat())
        self.assertIsNone(rows[0]['industry'])
        self.assertTrue(meta['cache_reused']);self.assertEqual(meta['source_provenance'],'sse_same_day_other_runner')

    def test_receipt_bound_to_current_run_and_github_context(self):
        for field,value in [('workflow_run_id','123455'),('collector_runner','Linux'),
                            ('collector_context','local'),('protocol','old'),('date','2026-10-20')]:
            with self.subTest(field=field):
                data=copy.deepcopy(self.record);data[field]=value;rehash(data)
                with self.assertRaises(ValueError):receipt.validate(data,NOW,self.md)
        for env in ({'GITHUB_RUN_ID':''},{'GITHUB_ACTIONS':'false'}):
            with self.subTest(env=env),patch.dict(os.environ,env):
                with self.assertRaises(ValueError):receipt.validate(self.record,NOW,self.md)

    def test_receipt_requires_allowed_official_source_and_current_engine_allowlist(self):
        for field,value in [('source','https://example.com/'),('source_api','https://query.sse.com.cn/other')]:
            data=copy.deepcopy(self.record);data[field]=value;rehash(data)
            with self.subTest(field=field),self.assertRaises(ValueError):receipt.validate(data,NOW,self.md)
        self.md.SSE_LIST_APIS=()
        with self.assertRaises(ValueError):receipt.validate(self.record,NOW,self.md)

    def test_receipt_rejects_naive_future_reversed_or_cross_day_clocks(self):
        for field,value in [('fetched_at','2026-10-21T08:01:00'),
                            ('collection_finished_at',(NOW+dt.timedelta(seconds=1)).isoformat()),
                            ('collection_started_at',FINISH.isoformat()),
                            ('collection_started_at','2026-10-20T23:59:00+08:00')]:
            with self.subTest(field=field,value=value):
                data=copy.deepcopy(self.record);data[field]=value;rehash(data)
                with self.assertRaises(ValueError):receipt.validate(data,NOW,self.md)
        with self.assertRaises(ValueError):receipt.validate(self.record,NOW.replace(tzinfo=None),self.md)

    def test_complete_unique_ordered_sse_rows_required(self):
        edits=[lambda data:data.update(count=1999,rows=data['rows'][:-1]),
               lambda data:data['rows'].__setitem__(1,copy.deepcopy(data['rows'][0])),
               lambda data:data['rows'].reverse(),
               lambda data:data['rows'][0].update(symbol='sz000001'),
               lambda data:data['rows'][0].update(name='sh600000'),
               lambda data:data['rows'][0].update(name='测试\n公司'),
               lambda data:data['rows'][0].update(listing_date='2026-10-22'),
               lambda data:data['rows'][0].update(extra='unexpected')]
        for index,edit in enumerate(edits):
            with self.subTest(index=index):
                data=copy.deepcopy(self.record);edit(data);rehash(data)
                with self.assertRaises(ValueError):receipt.validate(data,NOW,self.md)

    def test_hash_and_top_level_whitelist_required(self):
        self.record['rows'][0]['name']='篡改公司'
        with self.assertRaisesRegex(ValueError,'SHA256'):receipt.validate(self.record,NOW,self.md)
        self.record['extra']='unexpected';rehash(self.record)
        with self.assertRaises(ValueError):receipt.validate(self.record,NOW,self.md)

    def test_load_requires_today_fixed_filename_and_bounded_file(self):
        path=self.write();self.assertEqual(len(receipt.load_receipt(path,NOW,self.md)[0]),2000)
        wrong=self.write(path=self.directory/'2026-10-20.json')
        with self.assertRaises(ValueError):receipt.load_receipt(wrong,NOW,self.md)
        with patch.object(receipt,'MAX_BYTES',100):
            with self.assertRaises(ValueError):receipt.load_receipt(path,NOW,self.md)

    def raw_rows(self):
        return [dict(row,source=self.record['source'],source_api=self.record['source_api'],
                     universe_total=self.record['count'],fetched_at=FETCHED.isoformat())
                for row in self.record['rows']]

    def test_collect_uses_official_calendar_live_rows_and_actual_platform(self):
        raw=self.raw_rows();raw[0]['listing_date']='19991110';raw[4]['listing_date']='20030428'
        self.md._sse_universe.return_value=raw
        with patch.object(receipt.sys,'platform','win32'),patch.object(receipt,'_now',side_effect=[START,FINISH,NOW]):
            result=receipt.collect(self.directory,self.md)
        self.md.is_trading_day.assert_called_once_with(DAY);self.md._sse_universe.assert_called_once_with()
        self.assertTrue(result['receipt_ready']);self.assertEqual(result['status'],'complete');self.assertNotIn('rows',result)
        self.assertEqual([path.name for path in self.directory.iterdir()],[DAY+'.json'])
        rows,meta=receipt.load_receipt(self.directory/(DAY+'.json'),NOW,self.md)
        self.assertEqual(len(rows),2000);self.assertEqual(meta['fetched_at'],FETCHED.isoformat())
        self.assertEqual(rows[0]['listing_date'],'1999-11-10');self.assertEqual(rows[4]['listing_date'],'2003-04-28')
        self.assertEqual(raw[0]['listing_date'],'19991110')

    def test_collect_rejects_invalid_or_future_official_listing_dates(self):
        for listing in ['20260230','20261022','19991301','1999111','1999/11/10',19991110]:
            with self.subTest(listing=listing):
                rows=self.raw_rows();rows[0]['listing_date']=listing;self.md._sse_universe.return_value=rows
                with patch.object(receipt.sys,'platform','win32'),patch.object(receipt,'_now',side_effect=[START,FINISH]):
                    with self.assertRaises(ValueError):receipt.collect(self.directory,self.md)
                self.assertEqual(list(self.directory.iterdir()),[])

    def test_receipt_remains_iso_only_after_collector_normalization(self):
        self.record['rows'][0]['listing_date']='19991110';rehash(self.record)
        with self.assertRaises(ValueError):receipt.validate(self.record,NOW,self.md)

    def test_closed_or_unknown_calendar_never_collects_or_writes_receipt(self):
        self.md.is_trading_day.return_value=False
        with patch.object(receipt,'_now',return_value=START):result=receipt.collect(self.directory,self.md)
        self.assertEqual(result['status'],'closed');self.assertFalse(result['receipt_ready'])
        self.assertEqual(list(self.directory.iterdir()),[]);self.md._sse_universe.assert_not_called()
        self.md.is_trading_day.side_effect=MarketNetworkError('calendar unavailable')
        with self.assertRaises(MarketNetworkError):receipt.collect(self.directory,self.md)
        self.md._sse_universe.assert_not_called()

    def test_collect_rejects_misreported_or_local_runner_before_fetch(self):
        with patch.object(receipt.sys,'platform','linux'):
            with self.assertRaises(ValueError):receipt.collect(self.directory,self.md)
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}),patch.object(receipt.sys,'platform','win32'):
            with self.assertRaises(ValueError):receipt.collect(self.directory,self.md)
        self.md._sse_universe.assert_not_called();self.assertEqual(list(self.directory.iterdir()),[])

    def test_collect_rejects_non_current_actual_row_times_and_incomplete_sources(self):
        for edit in [lambda row:row.update(fetched_at=(START-dt.timedelta(seconds=1)).isoformat()),
                     lambda row:row.update(fetched_at=(FINISH+dt.timedelta(seconds=1)).isoformat()),
                     lambda row:row.update(source='https://example.com/'),
                     lambda row:row.update(universe_total=1999),
                     lambda row:row.update(source_api='https://query.sse.com.cn/other')]:
            rows=self.raw_rows();edit(rows[0]);self.md._sse_universe.return_value=rows
            with patch.object(receipt.sys,'platform','win32'),patch.object(receipt,'_now',side_effect=[START,FINISH]):
                with self.assertRaises(ValueError):receipt.collect(self.directory,self.md)
            self.assertEqual(list(self.directory.iterdir()),[])

    def test_network_fallback_restores_original_receipt_after_core_sets_live_metadata(self):
        self.write();core=FakeCore(self.md);original=self.md.get_universe
        with receipt.use_receipt(core,self.directory):
            rows=core.md.get_universe('shanghai')
            original.assert_called_once_with('shanghai');self.assertEqual(len(rows),2000)
            core.putkv('universe_info',{'mode':'shanghai','count':2000,'fetched_at':NOW.isoformat(),
                                       'cache_reused':False,'source_provenance':'sse_live_verified'})
        self.assertIs(self.md.get_universe,original)
        meta=core.getkv('universe_info')
        self.assertEqual(meta['fetched_at'],FETCHED.isoformat());self.assertTrue(meta['cache_reused'])
        self.assertEqual(meta['last_live_attempt_at'],NOW.isoformat());self.assertIn('HTTP 403',meta['source_error'])
        self.assertNotIn('secret',meta['source_error']);self.assertNotIn('token',meta['source_error'])

    def test_live_success_never_reads_receipt(self):
        core=FakeCore(self.md);self.md.get_universe.side_effect=None;self.md.get_universe.return_value=[{'live':True}]
        with receipt.use_receipt(core,self.directory),patch.object(receipt,'load_receipt') as load:
            self.assertEqual(core.md.get_universe('shanghai'),[{'live':True}]);load.assert_not_called()

    def test_invalid_live_data_never_uses_transfer_receipt(self):
        self.write();core=FakeCore(self.md);self.md.get_universe.side_effect=MarketDataError('malformed live list')
        original=self.md.get_universe
        with self.assertRaisesRegex(MarketDataError,'malformed'),receipt.use_receipt(core,self.directory):
            core.md.get_universe('shanghai')
        self.assertIs(self.md.get_universe,original);self.assertEqual(core.values,{})

    def test_only_shanghai_with_current_valid_file_can_fallback(self):
        core=FakeCore(self.md)
        for mode,directory in [('all',self.directory),('shanghai',None)]:
            with self.subTest(mode=mode,directory=directory):
                with self.assertRaises(MarketNetworkError),receipt.use_receipt(core,directory):core.md.get_universe(mode)
        with self.assertRaises(MarketDataError) as caught,receipt.use_receipt(core,self.directory):core.md.get_universe('shanghai')
        self.assertNotIsInstance(caught.exception,MarketNetworkError)

    def test_fallback_does_not_rewrite_existing_receipt_when_refresh_failed_before_persist(self):
        self.write();core=FakeCore(self.md)
        existing={'mode':'shanghai','count':2000,'fetched_at':'2026-10-20T08:00:00+08:00'}
        core.putkv('universe_info',copy.deepcopy(existing));original=self.md.get_universe
        with self.assertRaises(RuntimeError),receipt.use_receipt(core,self.directory):
            core.md.get_universe('shanghai');raise RuntimeError('refresh interrupted before storing universe')
        self.assertEqual(core.getkv('universe_info'),existing);self.assertIs(self.md.get_universe,original)

    def test_main_outputs_only_small_metadata_and_real_ready_flag(self):
        output=self.directory/'github-output'
        for response,expected in [({'status':'complete','receipt_ready':True},0),({'status':'closed','receipt_ready':False},0),
                                  (MarketNetworkError('secret endpoint'),1)]:
            with self.subTest(response=response):
                target=patch.object(receipt,'collect',side_effect=response) if isinstance(response,Exception) else patch.object(receipt,'collect',return_value=response)
                stream=io.StringIO()
                with target,patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}),patch('sys.stdout',stream):
                    self.assertEqual(receipt.main(['collect','--output-dir',str(self.directory)]),expected)
                data=json.loads(stream.getvalue());self.assertNotIn('rows',data);self.assertNotIn('secret',stream.getvalue())
        self.assertEqual(output.read_text(encoding='utf-8').splitlines(),['receipt_ready=true','receipt_ready=false','receipt_ready=false'])


if __name__=='__main__':unittest.main()
