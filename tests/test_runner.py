import contextlib
import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import runner


def make_db(path):
    with contextlib.closing(sqlite3.connect(path)) as con, con:
        con.executescript('''
        CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE securities(symbol TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE histories(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE signals(report_date TEXT NOT NULL,symbol TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(report_date,symbol));
        CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT,started_at TEXT NOT NULL,ended_at TEXT,target_date TEXT,status TEXT,total INTEGER DEFAULT 0,ok INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,error TEXT);
        CREATE TABLE orders(id INTEGER PRIMARY KEY,note TEXT);
        CREATE TABLE lots(id INTEGER PRIMARY KEY,note TEXT);
        CREATE TABLE trades(id INTEGER PRIMARY KEY,note TEXT);
        ''')


class FakeCore:
    encode=staticmethod(lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    def __init__(self, moment=None, database=None):
        self.values={}; self.database=database
        self.now=lambda: moment or dt.datetime(2026,10,19,8,30,tzinfo=runner.TZ)
        self.md=types.SimpleNamespace(is_trading_day=lambda date: True,last_completed_session=lambda moment:'2026-10-16')
        self.expected_date=lambda:'2026-10-16'
    def getkv(self,key,default=None):return self.values.get(key,default)
    def putkv(self,key,value):self.values[key]=value
    @contextlib.contextmanager
    def connect(self):
        con=sqlite3.connect(self.database)
        try:
            with con:yield con
        finally:con.close()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def current_cache(self, core):
        core.forecast=types.SimpleNamespace(MODEL_VERSION='ridge-cycle-v2',VALIDATION_PROTOCOL='purged-expanding-3x60-v1')
        core.strategy=types.SimpleNamespace(CHASE_POLICY_VERSION='anti-chase-v1')
        core.database=self.root/'current.sqlite3';make_db(core.database)
        receipt={'target_date':'2026-10-16','finished_at':'2026-10-19T08:12:00+08:00',
            'status':'success','ok':100,'failed':0,'total':100,'model_version':'ridge-cycle-v2',
            'chase_policy_version':'anti-chase-v1'}
        core.putkv('last_completed',receipt)
        core.putkv('last_success',dict(receipt))
        with core.connect() as con:
            for index in range(100):
                symbol=f'sh{600000+index}'
                signal={'symbol':symbol,'date':'2026-10-16','stale':False,
                    'prediction':{'model_version':'ridge-cycle-v2','validation_protocol':'purged-expanding-3x60-v1','as_of_date':'2026-10-16'},
                    'chase_risk':{'version':'anti-chase-v1','as_of_date':'2026-10-16'}}
                con.execute('INSERT INTO securities VALUES(?,?,1)',(symbol,symbol))
                con.execute('INSERT INTO signals VALUES(?,?,?)',('2026-10-16',symbol,json.dumps(signal)))
        return receipt

    def edit_signal(self, core, edit):
        with core.connect() as con:
            raw=con.execute("SELECT payload FROM signals WHERE symbol='sh600000'").fetchone()[0]
            signal=json.loads(raw);edit(signal)
            con.execute("UPDATE signals SET payload=? WHERE symbol='sh600000'",(json.dumps(signal),))

    def closing_core(self):
        core=FakeCore(dt.datetime(2026,10,19,20,58,tzinfo=runner.TZ))
        core.md.last_completed_session=lambda moment: '2026-10-19' if moment.hour>=16 else '2026-10-16'
        core.expected_date=lambda:'2026-10-19'
        return core

    def closing_cache(self, core):
        receipt=dict(core.getkv('last_completed') or self.current_cache(core))
        receipt.update(target_date='2026-10-19',finished_at='2026-10-19T16:27:00+08:00')
        core.putkv('last_completed',dict(receipt));core.putkv('last_success',dict(receipt))
        with core.connect() as con:
            for symbol,raw in con.execute('SELECT symbol,payload FROM signals').fetchall():
                signal=json.loads(raw);signal['date']='2026-10-19'
                signal['prediction']['as_of_date']='2026-10-19'
                signal['chase_risk']['as_of_date']='2026-10-19'
                con.execute('UPDATE signals SET report_date=?,payload=? WHERE symbol=?',('2026-10-19',json.dumps(signal),symbol))
            con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)',('2026-10-19','sh000001',json.dumps({'date':'2026-10-19','stale':False})))
        return receipt

    def test_public_cache_contains_no_accounts_settings_or_deleted_private_bytes(self):
        source=self.root/'source.sqlite3';make_db(source)
        secret='UNIQUE_PRIVATE_ACCOUNT_MARKER_9384756'
        with contextlib.closing(sqlite3.connect(source)) as con, con:
            con.execute('INSERT INTO orders VALUES(1,?)',(secret,))
            con.execute('INSERT INTO lots VALUES(1,?)',(secret,))
            con.execute('INSERT INTO trades VALUES(1,?)',(secret,))
            con.execute('INSERT INTO kv VALUES(?,?)',('config',json.dumps({'password':secret})))
            con.execute('INSERT INTO kv VALUES(?,?)',('cash',json.dumps(secret)))
            con.execute('INSERT INTO kv VALUES(?,?)',('last_success',json.dumps({'target_date':'2026-10-16','finished_at':'2026-10-19T08:20:00+08:00','watchlist':secret})))
            con.execute('INSERT INTO kv VALUES(?,?)',('daily_plan:2026-10-16',json.dumps({'date':'2026-10-16','buy':None})))
            con.execute('INSERT INTO securities VALUES(?,?,1)',('sh600298','安琪酵母'))
        destination=self.root/'public.sqlite3';runner.public_snapshot(source,destination)
        with contextlib.closing(sqlite3.connect(destination)) as con, con:
            tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse(tables & {'orders','lots','trades'})
            self.assertEqual(con.execute('SELECT COUNT(*) FROM securities').fetchone()[0],1)
            kv=dict(con.execute('SELECT key,value FROM kv'))
            self.assertNotIn('config',kv);self.assertNotIn('cash',kv)
            self.assertNotIn('watchlist',json.loads(kv['last_success']))
            self.assertIn('daily_plan:2026-10-16',kv)
        self.assertNotIn(secret.encode(),destination.read_bytes())

    def test_restore_cache_checks_sha256_and_sanitizes_before_use(self):
        cache=self.root/'cache';state=self.root/'state';cache.mkdir();state.mkdir()
        source=cache/'research.sqlite3';make_db(source)
        metadata={'schema_version':1,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'calendars':{}}
        (cache/'cache-meta.json').write_text(json.dumps(metadata),encoding='utf-8')
        self.assertTrue(runner.restore_cache(cache,state))
        with contextlib.closing(sqlite3.connect(state/'market.sqlite3')) as con, con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='orders'").fetchone())
        with source.open('ab') as output:output.write(b'tampered')
        with self.assertRaises(ValueError):runner.restore_cache(cache,state)

    def test_seed_restores_public_payloads_but_rejects_private_tables_or_keys(self):
        database=self.root/'db.sqlite3';make_db(database);core=FakeCore(database=database)
        seed={'schema_version':1,'tables':{'signals':[{'report_date':'2026-10-16','symbol':'sh600298','payload':{'eligible':False}}]},'kv':{'daily_plan:2026-10-16':{'buy':None}}}
        path=self.root/'research.json.gz'
        with gzip.open(path,'wt',encoding='utf-8') as output:json.dump(seed,output)
        self.assertTrue(runner.restore_seed(path,core,self.root))
        with contextlib.closing(sqlite3.connect(database)) as con, con:self.assertFalse(json.loads(con.execute('SELECT payload FROM signals').fetchone()[0])['eligible'])
        seed['tables']['orders']=[]
        with gzip.open(path,'wt',encoding='utf-8') as output:json.dump(seed,output)
        with self.assertRaises(ValueError):runner.restore_seed(path,core,self.root)
        del seed['tables']['orders'];seed['kv']['cash']=100000
        with gzip.open(path,'wt',encoding='utf-8') as output:json.dump(seed,output)
        with self.assertRaises(ValueError):runner.restore_seed(path,core,self.root)

    def test_sse_holiday_neither_collects_nor_publishes(self):
        core=FakeCore();core.md.is_trading_day=lambda date:False;service=Mock()
        with patch.object(runner,'collect_news') as news:
            self.assertEqual(runner.run_stage('collect',core,service)['status'],'closed')
            self.assertEqual(runner.run_stage('publish',core,service)['status'],'closed')
            news.assert_not_called();service.refresh.assert_not_called()

    def test_runner_reads_ai_review_from_original_repository_before_publish(self):
        core=FakeCore(dt.datetime(2026,10,19,9,tzinfo=runner.TZ));receipt={'status':'missing','valid':False}
        with patch.object(runner,'load_ai_review',return_value=receipt) as load,patch.object(runner,'collect_news'),patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=lambda:{'status':'pending'})):
            result=runner.run_stage('publish',core,Mock())
        load.assert_called_once_with(runner.ROOT,now=core.now(),signal_date='2026-10-16')
        self.assertEqual(core.getkv('ai_review'),receipt);self.assertEqual(result['status'],'pending')
        self.assertTrue(runner.public_key('ai_review'))

    def test_news_precedes_cached_market_research_and_preserves_real_completion_time(self):
        core=FakeCore();old={'target_date':'2026-10-16','finished_at':'2026-10-16T16:00:00+08:00'};core.putkv('last_success',old)
        order=[];service=Mock();service.status={'status':'partial','ok':2316,'failed':4,'total':2320}
        def refresh(force):
            self.assertFalse(force);self.assertEqual(core.getkv('last_success'),{});order.append('market')
        service.refresh.side_effect=refresh
        with patch.object(runner,'collect_news',side_effect=lambda core:order.append('news') or {'status':'verified'}),patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
            result=runner.run_stage('collect',core,service)
        self.assertEqual(order,['news','market']);self.assertEqual(result['status'],'partial')
        self.assertEqual(core.getkv('last_success'),old,'Partial research must not manufacture full success')

    def test_failed_research_does_not_destroy_previous_proven_success(self):
        core=FakeCore();old={'target_date':'2026-10-16','finished_at':'2026-10-16T16:00:00+08:00'};core.putkv('last_success',old)
        service=Mock();service.refresh.side_effect=RuntimeError('provider unavailable')
        with patch.object(runner,'collect_news',return_value={'status':'unavailable'}),patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
            with self.assertRaises(RuntimeError):runner.run_stage('collect',core,service)
        self.assertEqual(core.getkv('last_success'),old)

    def test_closing_collection_has_a_separate_receipt_and_preserves_morning_evidence(self):
        core=self.closing_core();self.current_cache(core);service=Mock()
        core.putkv('github_collect',{'finished_at':'2026-10-19T08:12:00+08:00'})
        core.putkv('morning_news',{'as_of':'2026-10-19T09:00:00+08:00'})
        core.putkv('ai_review',{'status':'late','review':{'signal_date':'2026-10-16'}})
        original=copy.deepcopy(core.values)
        service.status={'status':'partial','ok':99,'failed':1,'total':100}
        def refresh(force, *, allow_same_day_universe_cache=False):
            self.assertFalse(force);self.assertTrue(allow_same_day_universe_cache)
            self.closing_cache(core)
            receipt=dict(core.getkv('last_completed'),finished_at=core.now().isoformat(),status='partial',ok=99,failed=1)
            core.putkv('last_completed',receipt)
            self.edit_signal(core,lambda signal:signal.update(stale=True,date='2026-10-16'))
        service.refresh.side_effect=refresh
        with patch.object(runner,'collect_news') as news,patch.object(runner,'load_ai_review') as ai,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()) as short:
            result=runner.run_stage('collect',core,service)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['target_date'],'2026-10-19')
        self.assertEqual(result['collection_period'],'after_close');self.assertFalse(result['morning_collection'])
        self.assertEqual(core.getkv('github_close_collect'),result)
        self.assertTrue(runner.public_key('github_close_collect'))
        for key in ('github_collect','morning_news','ai_review'):
            self.assertEqual(core.getkv(key),original[key])
        news.assert_not_called();ai.assert_not_called();short.assert_called_once_with(core,'2026-10-19')
        self.assertIsNone(runner.current_morning_research(core,core.now(),'2026-10-19'))

    def test_closing_cache_is_reused_with_original_time_and_requires_fresh_benchmark(self):
        core=self.closing_core();receipt=self.closing_cache(core);service=Mock()
        universe={'mode':'shanghai','count':100,'fetched_at':'2026-10-19T10:04:25+08:00',
            'cache_reused':True,'last_live_attempt_at':'2026-10-19T16:20:03+08:00',
            'source_error':'query.sse.com.cn: HTTP 403','source_provenance':'legacy_sse_engine_path'}
        core.putkv('universe_info',dict(universe,private_settings='PRIVATE'))
        with patch.object(runner,'collect_news') as news,patch.object(runner,'short_term_refresh') as short:
            result=runner.run_stage('collect',core,service)
        self.assertEqual(result['status'],'cached');self.assertEqual(result['research'],receipt)
        self.assertEqual(result['universe_receipt'],universe)
        self.assertEqual(result['research_finished_at'],'2026-10-19T16:27:00+08:00')
        self.assertEqual(core.getkv('last_completed'),receipt)
        news.assert_not_called();short.assert_not_called();service.refresh.assert_not_called()
        for stamp in ('2026-10-19T08:27:00+08:00','2026-10-18T16:27:00+08:00',
                      '2026-10-19T22:00:00+08:00','2026-10-19T16:27:00'):
            with self.subTest(stamp=stamp):
                core.putkv('last_completed',dict(receipt,finished_at=stamp))
                self.assertIsNone(runner.current_close_research(core,core.now(),'2026-10-19'))
        core.putkv('last_completed',receipt)
        with core.connect() as con:con.execute("DELETE FROM signals WHERE symbol='sh000001'")
        self.assertIsNone(runner.current_close_research(core,core.now(),'2026-10-19'))

    def test_closing_universe_receipt_is_a_bounded_public_whitelist(self):
        core=self.closing_core()
        core.putkv('universe_info',{'mode':'shanghai','count':2318,
            'fetched_at':'2026-10-19T10:04:25.964452+08:00','cache_reused':True,
            'last_live_attempt_at':'2026-10-19T21:09:55+08:00',
            'source':'https://www.sse.com.cn/assortment/stock/list/share/',
            'source_api':'https://query.sse.com.cn/sseQuery/commonQuery.do',
            'source_error':'HTTP 403 '+('x'*400),'source_provenance':'legacy_sse_engine_path',
            'config':{'token':'PRIVATE'},'unexpected':'PRIVATE'})
        result=runner.public_universe_receipt(core)
        self.assertEqual(len(result['source_error']),300)
        self.assertEqual(result['fetched_at'],'2026-10-19T10:04:25.964452+08:00')
        self.assertEqual(result['source_api'],'https://query.sse.com.cn/sseQuery/commonQuery.do')
        self.assertNotIn('PRIVATE',json.dumps(result))
        core.putkv('universe_info',{'mode':'PRIVATE','count':True,'cache_reused':'true',
            'fetched_at':'x'*100,'source':'https://evil.example/private',
            'source_api':'https://query.sse.com.cn/commonQuery.do?token=PRIVATE',
            'source_provenance':'PRIVATE'})
        self.assertEqual(runner.public_universe_receipt(core),{})

    def test_closing_holiday_cannot_refresh_or_publish(self):
        core=self.closing_core();core.md.is_trading_day=lambda date:False;service=Mock()
        with patch.object(runner,'collect_news') as news,patch.object(runner,'collect_after_close') as closing:
            for stage in ('collect','publish'):
                self.assertEqual(runner.run_stage(stage,core,service)['status'],'closed')
        news.assert_not_called();closing.assert_not_called();service.refresh.assert_not_called()

    def test_closing_refresh_errors_or_unproved_coverage_cannot_be_hidden_by_publishing(self):
        for outcome in ('raises','running','partial'):
            with self.subTest(outcome=outcome):
                core=self.closing_core();old={'target_date':'2026-10-16','finished_at':'2026-10-19T08:12:00+08:00'}
                core.putkv('last_success',dict(old));service=Mock();service.status={'status':outcome}
                if outcome=='raises':service.refresh.side_effect=RuntimeError('provider failed')
                publish=Mock(return_value={'status':'published'})
                with patch.object(runner,'load_ai_review'),patch.object(runner,'collect_news') as news,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()),patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=publish)):
                    result=runner.run_stage('publish',core,service)
                self.assertEqual(result['status'],'error');self.assertEqual(result['close_research']['status'],'error')
                self.assertEqual(core.getkv('github_close_collect')['status'],'error')
                self.assertEqual(core.getkv('last_success'),old)
                publish.assert_not_called();news.assert_not_called()

    def test_evening_push_refreshes_before_returning_the_immutable_original_plan(self):
        core=self.closing_core();service=Mock();order=[]
        original={'status':'published','published_at':'2026-10-19T09:53:39+08:00','morning_ready':False,'late':True,'buy':None,'sell':[]}
        core.putkv('daily_plan:2026-10-19',copy.deepcopy(original))
        publish=Mock(side_effect=lambda:order.append('original-plan') or core.getkv('daily_plan:2026-10-19'))
        receipt={'status':'late','valid':True}
        with patch.object(runner,'load_ai_review',return_value=receipt) as ai,patch.object(runner,'collect_after_close',side_effect=lambda *args:order.append('closing-bars') or {'status':'partial','target_date':'2026-10-19'}) as closing,patch.object(runner,'collect_news') as news,patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=publish)):
            result=runner.run_stage('publish',core,service)
        self.assertEqual(order,['closing-bars','original-plan'])
        self.assertEqual(result['target_date'],'2026-10-16')
        self.assertEqual(result['close_research']['target_date'],'2026-10-19')
        self.assertEqual(result['published_at'],original['published_at'])
        self.assertEqual(core.getkv('daily_plan:2026-10-19'),original)
        ai.assert_called_once_with(runner.ROOT,now=core.now(),signal_date='2026-10-16')
        news.assert_not_called()

    def test_closing_errors_expose_only_bounded_public_market_data_messages(self):
        class PublicMarketError(RuntimeError):pass
        public='query.sse.com.cn: request failed after 3 attempts (HTTP 403) '+('x'*350)
        private='PRIVATE_TOKEN_AND_LOCAL_PATH_C:/private/settings.json'
        for error,detail in ((PublicMarketError(public),public[:300]),(RuntimeError(private),None)):
            with self.subTest(error=type(error).__name__):
                core=self.closing_core();core.md.MarketDataError=PublicMarketError
                service=Mock();service.status={'status':'error','error':private}
                service.refresh.side_effect=error
                with patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
                    result=runner.collect_after_close(core,service,core.now())
                self.assertEqual(result['status'],'error')
                self.assertEqual(result.get('error_detail'),detail)
                self.assertNotIn(private,json.dumps(result))

    def test_evening_actual_collection_skips_recomputing_the_obsolete_report_first(self):
        core=self.closing_core()
        self.assertFalse(runner.needs_cached_recalculation('collect',core,core.now()))
        self.assertFalse(runner.needs_cached_recalculation('publish',core,core.now()))
        self.closing_cache(core)
        self.assertTrue(runner.needs_cached_recalculation('publish',core,core.now()))
        core.now=lambda:dt.datetime(2026,10,19,9,7,tzinfo=runner.TZ)
        self.assertTrue(runner.needs_cached_recalculation('publish',core,core.now()))

    def test_explicit_collection_failure_makes_main_exit_nonzero_with_an_honest_status(self):
        engine=self.root/'engine';engine.mkdir();(engine/'core.py').touch()
        core=self.closing_core();core.init_db=Mock();core.Service=Mock(return_value=Mock())
        output=self.root/'public'
        exporter=types.SimpleNamespace(export_site=Mock())
        imported=[]
        def module(name):
            imported.append(name)
            return {'core':core,'export_static':exporter}[name]
        failure={'stage':'publish','status':'error','close_research':{'status':'error','target_date':'2026-10-19'}}
        with patch.object(runner,'restore_cache',return_value=True),patch.object(runner,'restore_plans',return_value=0),patch.object(runner,'save_plans',return_value=0),patch.object(runner,'save_cache'),patch.object(runner,'needs_cached_recalculation',return_value=False),patch.object(runner,'run_stage',return_value=failure),patch.object(runner.importlib,'import_module',side_effect=module),patch.dict(os.environ,{'GITHUB_OUTPUT':''}):
            code=runner.main(['publish','--engine-dir',str(engine),'--state-dir',str(self.root/'state'),'--output-dir',str(output)])
        self.assertEqual(code,1);self.assertNotIn('model_refresh',imported)
        self.assertEqual(json.loads((output/'run-status.json').read_text(encoding='utf-8'))['status'],'error')
        self.assertEqual(core.getkv('github_run_status')['close_research']['status'],'error')

    def test_collection_starts_market_work_only_between_eight_and_nine(self):
        for hour,minute,second,expected in ((7,59,59,'skipped'),(8,0,0,'partial'),
                (8,59,59,'partial'),(9,0,0,'skipped'),(9,29,59,'skipped'),
                (9,30,0,'late_skipped'),(15,59,59,'late_skipped')):
            with self.subTest(time=(hour,minute,second)):
                core=FakeCore(dt.datetime(2026,10,19,hour,minute,second,tzinfo=runner.TZ))
                previous={'target_date':'2026-10-16','finished_at':'2026-10-16T16:00:00+08:00'}
                core.putkv('last_completed',dict(previous));core.putkv('last_success',dict(previous))
                service=Mock();service.status={'status':'partial'}
                with patch.object(runner,'collect_news',return_value={'status':'verified'}) as news,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
                    result=runner.run_stage('collect',core,service)
                self.assertEqual(result['status'],expected)
                self.assertEqual(service.refresh.call_count,int(expected=='partial'))
                self.assertEqual(news.call_count,int(expected=='partial'))
                self.assertEqual(core.getkv('last_completed'),previous)
                self.assertEqual(core.getkv('last_success'),previous)

    def test_news_crossing_nine_does_not_start_a_long_market_refresh(self):
        for finished,expected in ((dt.datetime(2026,10,19,9,tzinfo=runner.TZ),'skipped'),
                                 (dt.datetime(2026,10,19,9,30,tzinfo=runner.TZ),'late_skipped')):
            core=FakeCore();core.now=Mock(side_effect=[dt.datetime(2026,10,19,8,59,tzinfo=runner.TZ),finished])
            service=Mock();previous={'finished_at':'2026-10-16T16:00:00+08:00'}
            core.putkv('last_completed',previous)
            with patch.object(runner,'collect_news',return_value={'status':'verified'}) as news,patch.object(runner,'short_term_refresh') as short:
                result=runner.run_stage('collect',core,service)
            self.assertEqual(result['status'],expected);self.assertEqual(result['finished_at'],finished.isoformat())
            service.refresh.assert_not_called();short.assert_not_called();news.assert_called_once()
            self.assertEqual(core.getkv('last_completed'),previous)

    def test_recovery_reuses_current_morning_models_without_rewriting_research_time(self):
        core=FakeCore();receipt=self.current_cache(core);original=copy.deepcopy(core.values);service=Mock()
        with patch.object(runner,'collect_news') as news,patch.object(runner,'short_term_refresh') as short:
            first=runner.run_stage('collect',core,service)
            second=runner.run_stage('collect',core,service)
        self.assertEqual(first['status'],'cached');self.assertEqual(second['status'],'cached')
        self.assertEqual(first['research_finished_at'],'2026-10-19T08:12:00+08:00')
        self.assertEqual(first['research'],receipt)
        self.assertEqual(first['finished_at'],'2026-10-19T08:30:00+08:00')
        self.assertEqual(core.getkv('last_completed'),original['last_completed'])
        self.assertEqual(core.getkv('last_success'),original['last_success'])
        news.assert_not_called();short.assert_not_called();service.refresh.assert_not_called()

    def test_old_incomplete_or_future_receipts_cannot_be_relabelled_as_morning_cache(self):
        core=FakeCore();original=self.current_cache(core)
        mutations=[{'finished_at':'2026-10-16T08:12:00+08:00'},
            {'finished_at':'2026-10-19T07:59:59+08:00'},
            {'finished_at':'2026-10-19T08:31:00+08:00'},
            {'finished_at':'2026-10-19T08:12:00'},
            {'target_date':'2026-10-15'},{'model_version':'ridge-cycle-v1'},
            {'chase_policy_version':None},{'chase_policy_version':'old-policy'},
            {'ok':98,'failed':2},{'ok':99,'failed':0},{'total':float('inf')},
            {'status':'running'}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                core.putkv('last_completed',dict(original,**mutation))
                self.assertIsNone(runner.current_morning_research(core,core.now(),'2026-10-16'))

    def test_any_fresh_signal_with_an_old_policy_or_prediction_forces_recalculation(self):
        core=FakeCore();self.current_cache(core)
        with core.connect() as con:
            original=con.execute("SELECT payload FROM signals WHERE symbol='sh600000'").fetchone()[0]
        edits=[lambda s:s['chase_risk'].update(version='old-policy'),
            lambda s:s.pop('chase_risk'),
            lambda s:s['chase_risk'].update(as_of_date='2026-10-15'),
            lambda s:s['prediction'].update(model_version='ridge-cycle-v1'),
            lambda s:s['prediction'].update(as_of_date='2026-10-15'),
            lambda s:s.update(recompute_status='error')]
        for index,edit in enumerate(edits):
            with self.subTest(edit=index):
                with core.connect() as con:con.execute("UPDATE signals SET payload=? WHERE symbol='sh600000'",(original,))
                self.edit_signal(core,edit)
                self.assertIsNone(runner.current_morning_research(core,core.now(),'2026-10-16'))

    def test_partial_report_at_exact_ninety_nine_percent_can_be_reused(self):
        core=FakeCore();receipt=self.current_cache(core)
        core.putkv('last_completed',dict(receipt,status='partial',ok=99,failed=1))
        self.edit_signal(core,lambda signal:signal.update(stale=True,date='2026-10-15'))
        self.assertIsNotNone(runner.current_morning_research(core,core.now(),'2026-10-16'))
        with core.connect() as con:con.execute("DELETE FROM signals WHERE symbol='sh600001'")
        self.assertIsNone(runner.current_morning_research(core,core.now(),'2026-10-16'))

    def test_old_policy_receipt_recomputes_once_even_when_bars_and_model_version_match(self):
        core=FakeCore();receipt=self.current_cache(core);receipt.pop('chase_policy_version')
        service=Mock();service.status={'status':'success'}
        def refresh(force):
            core.putkv('last_completed',dict(receipt,chase_policy_version='anti-chase-v1'))
            core.putkv('last_success',dict(receipt,chase_policy_version='anti-chase-v1'))
        service.refresh.side_effect=refresh
        with patch.object(runner,'collect_news',return_value={'status':'verified'}) as news,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
            self.assertEqual(runner.run_stage('collect',core,service)['status'],'success')
            self.assertEqual(runner.run_stage('collect',core,service)['status'],'cached')
        service.refresh.assert_called_once_with(force=False);news.assert_called_once()

    def test_slow_market_completion_keeps_its_actual_late_time(self):
        core=FakeCore(dt.datetime(2026,10,19,8,59,tzinfo=runner.TZ));service=Mock();service.status={'status':'partial'}
        late=dt.datetime(2026,10,19,9,45,tzinfo=runner.TZ)
        def refresh(force):core.now=lambda:late
        service.refresh.side_effect=refresh
        with patch.object(runner,'collect_news',return_value={'status':'verified'}),patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
            result=runner.run_stage('collect',core,service)
        self.assertEqual(result['finished_at'],late.isoformat())
        self.assertNotIn('research_cached',result)

    def test_public_completion_receipt_retains_policy_without_private_settings(self):
        value={'target_date':'2026-10-16','chase_policy_version':'anti-chase-v1','watchlist':'PRIVATE'}
        self.assertEqual(runner.clean_value('last_completed',value),
            {'target_date':'2026-10-16','chase_policy_version':'anti-chase-v1'})

    def test_publish_rechecks_current_news_before_selection_but_late_recovery_does_not(self):
        for hour,minute,recheck in ((9,0,True),(9,29,True),(9,30,False),(15,59,False)):
            core=FakeCore(dt.datetime(2026,10,19,hour,minute,tzinfo=runner.TZ));order=[];service=Mock()
            publish=Mock(side_effect=lambda:order.append('publish') or {'status':'published'})
            with patch.object(runner,'collect_news',side_effect=lambda core:order.append('news')) as news,patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=publish)):
                runner.run_stage('publish',core,service)
            self.assertEqual(order,['news','publish'] if recheck else ['publish'])
            self.assertEqual(news.call_count,int(recheck))
            service.refresh.assert_not_called()

    def test_news_window_receives_the_official_completed_session(self):
        core=FakeCore();collect=Mock(return_value={'status':'verified','as_of':'2026-10-19T08:30:00+08:00'})
        risk=types.SimpleNamespace(collect=Mock(return_value={'status':'complete','risk_exclusions':[]}))
        with patch.object(runner.importlib,'import_module',side_effect=lambda name:types.SimpleNamespace(collect=collect) if name=='news_collector' else risk):
            result=runner.collect_news(core)
        collect.assert_called_once_with(coverage_start='2026-10-16')
        self.assertEqual(core.getkv('morning_news'),result)

    def test_same_day_complete_financial_snapshot_reuses_reports_and_never_republishes_plan(self):
        core=FakeCore();core.putkv('longterm',{'as_of':'2026-10-16','coverage':{'valuation_qualified':836,'financial_complete':836,'not_researched':0}})
        with patch.object(runner,'fast_valuations',return_value={'received':2320}) as valuations,patch.object(runner.importlib,'import_module') as imports:
            result=runner.run_stage('fundamentals',core,Mock())
        self.assertEqual(result['status'],'cached');valuations.assert_called_once();imports.assert_not_called()
        self.assertNotIn('github_publish',core.values)

    def test_new_financial_date_runs_full_research_without_daily_publication(self):
        core=FakeCore();core.putkv('longterm',{'as_of':'2026-10-15','coverage':{'valuation_qualified':836,'financial_complete':836}})
        refresh=Mock(return_value={'as_of':'2026-10-16','coverage':{'valuation_qualified':836,'financial_complete':800}})
        with patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(refresh_fundamentals=refresh)) as imports:
            result=runner.run_stage('fundamentals',core,Mock())
        self.assertEqual(result['status'],'partial');refresh.assert_called_once()
        imports.assert_called_once_with('research_refresh');self.assertNotIn('github_publish',core.values)

    def advice(self,date='2026-10-16',symbol='sh600298',sell_date='2026-10-19'):
        return {'date':date,'status':'published','published_at':date+'T09:00:00+08:00',
            'buy':{'symbol':symbol,'name':'原始研究建议','recommended_date':date,'sell_date':sell_date,
                   'entry_low':10,'entry_high':10.05,'take_profit':11,'stop_loss':9},'sell':[],'news':[]}

    def archive(self,plans,name='archive.json'):
        path=self.root/name
        path.write_text(runner.canonical({'schema_version':1,'plans':plans,
            'sha256':hashlib.sha256(runner.canonical(plans).encode()).hexdigest()}),encoding='utf-8')
        return path

    def test_friday_archive_survives_monday_seed_fallback_and_produces_original_d2_sell(self):
        database=self.root/'monday.sqlite3';make_db(database);core=FakeCore(database=database)
        seed=self.root/'seed.json.gz'
        with gzip.open(seed,'wt',encoding='utf-8') as output:json.dump({'schema_version':1,'tables':{},'kv':{}},output)
        runner.restore_seed(seed,core,self.root)
        original=self.advice();archive=self.archive({'2026-10-16':original})
        self.assertEqual(runner.restore_plans(archive,core),1)
        self.assertEqual(runner.restore_plans(archive,core),0)
        with core.connect() as con:
            saved=json.loads(con.execute("SELECT value FROM kv WHERE key='daily_plan:2026-10-16'").fetchone()[0])
        self.assertEqual(saved,original)
        def getkv(key,default=None):
            with core.connect() as con:row=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default
        def putkv(key,value):
            with core.connect() as con:con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(key,json.dumps(value)))
        core.getkv=getkv;core.putkv=putkv;core.TZ=runner.TZ
        core.now=lambda:dt.datetime(2026,10,19,9,tzinfo=runner.TZ)
        core.Service=lambda:types.SimpleNamespace(dashboard=lambda target:{'stocks':[{'symbol':'sh600298','date':'2026-10-16','raw_close':10.4,'eligible':False}],
            'meta':{'coverage':{'total':100,'ok':100}}})
        engine=runner.ROOT/'engine'
        if not (engine/'daily_plan.py').is_file():engine=runner.ROOT.parent/'ashare-platform'
        spec=importlib.util.spec_from_file_location('daily_plan_archive_integration',engine/'daily_plan.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'core':core,'market_data':core.md}):spec.loader.exec_module(module)
        monday=module.publish()
        self.assertIsNone(monday['buy']);self.assertEqual(len(monday['sell']),1)
        self.assertEqual(monday['sell'][0]['recommended_date'],'2026-10-16')
        self.assertEqual(monday['sell'][0]['take_profit'],11)
        self.assertEqual(monday['sell'][0]['sell_low'],round(10.4*1.015*.9975,2))
        self.assertEqual(getkv('daily_plan:2026-10-16'),original)

    def test_conflicting_published_archive_aborts_atomically_without_replacing_original_advice(self):
        database=self.root/'conflict.sqlite3';make_db(database);core=FakeCore(database=database)
        original=self.advice()
        with core.connect() as con:con.execute('INSERT INTO kv VALUES(?,?)',('daily_plan:2026-10-16',json.dumps(original)))
        archive=self.archive({'2026-10-15':self.advice('2026-10-15',sell_date='2026-10-16'),
                              '2026-10-16':self.advice(symbol='sh600312')})
        with self.assertRaisesRegex(ValueError,'冲突'):runner.restore_plans(archive,core)
        with core.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM kv WHERE key='daily_plan:2026-10-15'").fetchone())
            self.assertEqual(json.loads(con.execute("SELECT value FROM kv WHERE key='daily_plan:2026-10-16'").fetchone()[0]),original)

    def test_plan_archive_is_deterministic_and_contains_only_full_public_advice_history(self):
        database=self.root/'archive.sqlite3';make_db(database);core=FakeCore(database=database)
        original=self.advice()
        with core.connect() as con:
            con.execute('INSERT INTO kv VALUES(?,?)',('daily_plan:2026-10-16',json.dumps(original)))
            con.execute('INSERT INTO kv VALUES(?,?)',('cash',json.dumps('PRIVATE_BALANCE_MARKER')))
        repository=self.root/'research/daily-plans.json';output=self.root/'plans-export'
        self.assertEqual(runner.save_plans(core,repository,output),1)
        first=repository.read_bytes();runner.save_plans(core,repository,output)
        self.assertEqual(first,repository.read_bytes());self.assertEqual(first,(output/'daily-plans.json').read_bytes())
        self.assertNotIn(b'PRIVATE_BALANCE_MARKER',first)
        archive=json.loads(first);self.assertEqual(archive['plans'],{'2026-10-16':original})
        archive['plans']['2026-10-16']['buy']['entry_low']=1
        repository.write_text(json.dumps(archive),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'完整性'):runner.restore_plans(repository,core)


if __name__=='__main__':unittest.main()
