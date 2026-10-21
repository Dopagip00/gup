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
        self.brief=patch.object(runner,'refresh_market_brief',return_value={'status':'unavailable'})
        self.brief.start();self.addCleanup(self.brief.stop)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.watch=patch.object(runner,'update_watchlist',return_value={'status':'watch_only','count':5})
        self.watch.start();self.addCleanup(self.watch.stop)
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

    def test_subscription_ai_push_before_nine_prepares_observation_without_formal_publication(self):
        core=FakeCore(dt.datetime(2026,10,19,8,25,tzinfo=runner.TZ));service=Mock()
        with patch.object(runner,'collect_news') as news,patch.object(runner,'load_ai_review',return_value={'status':'partial'}),patch.object(runner.importlib,'import_module') as imports:
            result=runner.run_stage('publish',core,service)
        self.assertEqual(result['status'],'prepared');self.assertEqual(result['watchlist']['count'],5)
        self.assertEqual(core.getkv('ai_review'),{'status':'partial'})
        imports.assert_not_called();news.assert_not_called();service.refresh.assert_not_called()

    def test_closing_prepares_the_next_official_session_with_real_current_time(self):
        self.watch.stop()
        moment=dt.datetime(2026,10,23,17,tzinfo=runner.TZ);core=FakeCore(moment)
        core.md.is_trading_day=lambda date:dt.date.fromisoformat(date).weekday()<5
        publish=Mock(return_value={'date':'2026-10-26','status':'watch_only','items':[{'symbol':'sh600001'}],'top_pick':{'symbol':'sh600001'}})
        with patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=publish)):
            result=runner.update_watchlist(core,Mock(),moment)
        self.assertEqual(publish.call_args.kwargs['date'],'2026-10-26')
        self.assertIs(publish.call_args.kwargs['now'],moment)
        self.assertEqual(result['count'],1);self.assertEqual(result['top_symbol'],'sh600001')

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

    def test_only_real_current_closing_evidence_can_enable_next_session_financial_preparation(self):
        core=self.closing_core()
        self.assertFalse(runner.closing_financial_ready('publish',core,core.now()))
        self.closing_cache(core)
        for stage in ('collect','publish','recover'):
            self.assertTrue(runner.closing_financial_ready(stage,core,core.now()))
        self.assertFalse(runner.closing_financial_ready('fundamentals',core,core.now()))
        self.assertFalse(runner.closing_financial_ready('publish',core,core.now(),failure=True))
        self.assertFalse(runner.closing_financial_ready('publish',core,core.now().replace(hour=15,minute=59)))
        self.assertFalse(runner.closing_financial_ready('publish',core,core.now()+dt.timedelta(days=1)))

    def test_preopen_missing_report_skips_old_model_recalculation_only_in_collection_window(self):
        core=FakeCore(dt.datetime(2026,10,21,8,25,tzinfo=runner.TZ))
        self.current_cache(core)  # Real old report exists, but the target report does not.
        core.md.last_completed_session=lambda moment:'2026-10-20'
        self.assertFalse(runner.needs_cached_recalculation('publish',core,core.now()))
        for hour,minute in ((7,50),(9,0),(9,30)):
            moment=core.now().replace(hour=hour,minute=minute)
            self.assertTrue(runner.needs_cached_recalculation('publish',core,moment))
        self.assertTrue(runner.needs_cached_recalculation('fundamentals',core,core.now()))
        core.md.is_trading_day=lambda date:False
        self.assertTrue(runner.needs_cached_recalculation('publish',core,core.now()))

    def test_preopen_existing_or_unreadable_report_does_not_skip_recalculation(self):
        core=FakeCore(dt.datetime(2026,10,19,8,25,tzinfo=runner.TZ))
        self.current_cache(core)
        self.assertTrue(runner.needs_cached_recalculation('publish',core,core.now()))
        core.connect=Mock(side_effect=sqlite3.OperationalError('cache unavailable'))
        self.assertTrue(runner.needs_cached_recalculation('publish',core,core.now()))

    def test_main_preopen_missing_report_collects_without_recalculating_old_models(self):
        engine=self.root/'engine';engine.mkdir();(engine/'core.py').touch()
        core=FakeCore(dt.datetime(2026,10,21,8,25,tzinfo=runner.TZ));self.current_cache(core)
        core.md.last_completed_session=lambda moment:'2026-10-20'
        core.init_db=Mock();service=Mock();service.reports.return_value=[]
        service.status={'status':'partial','ok':99,'failed':1,'total':100}
        core.Service=Mock(return_value=service)
        original={'date':'2026-10-20','status':'published','published_at':'2026-10-20T10:51:00+08:00','buy':None}
        core.putkv('daily_plan:2026-10-20',copy.deepcopy(original))
        output=self.root/'public';imported=[]
        def module(name):
            imported.append(name)
            return {'core':core,'export_static':types.SimpleNamespace(export_site=Mock())}[name]
        with patch.object(runner,'restore_cache',return_value=True),patch.object(runner,'restore_plans',return_value=0),patch.object(runner,'save_plans',return_value=0),patch.object(runner,'save_cache'),patch.object(runner.importlib,'import_module',side_effect=module),patch.object(runner,'restore_watchlists',return_value=0),patch.object(runner,'save_watchlists',return_value=0),patch.object(runner,'collect_news',return_value={'status':'verified'}) as news,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()),patch.object(runner,'load_ai_review',return_value={'status':'partial'}),patch.dict(os.environ,{'GITHUB_OUTPUT':''}):
            self.assertEqual(runner.main(['publish','--engine-dir',str(engine),'--state-dir',str(self.root/'state'),'--output-dir',str(output)]),0)
        self.assertNotIn('model_refresh',imported)
        service.refresh.assert_called_once_with(force=False);news.assert_called_once()
        result=json.loads((output/'run-status.json').read_text(encoding='utf-8'))
        self.assertEqual(result['stage'],'collect')
        self.assertEqual(result['trigger'],'preopen_missing_completed_report')
        self.assertEqual(result['target_date'],'2026-10-20')
        self.assertEqual(core.getkv('daily_plan:2026-10-20'),original)
        self.assertNotIn('github_publish',core.values)

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
            with patch.object(runner,'restore_watchlists',return_value=0),patch.object(runner,'save_watchlists',return_value=0):
                code=runner.main(['publish','--engine-dir',str(engine),'--state-dir',str(self.root/'state'),'--output-dir',str(output)])
        self.assertEqual(code,1);self.assertNotIn('model_refresh',imported)
        self.assertEqual(json.loads((output/'run-status.json').read_text(encoding='utf-8'))['status'],'error')
        self.assertEqual(core.getkv('github_run_status')['close_research']['status'],'error')

    def test_collection_starts_market_work_only_between_eight_and_nine(self):
        for hour,minute,second,expected in ((7,59,59,'prepared'),(8,0,0,'partial'),
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

    def test_manual_recovery_uses_real_completed_data_without_morning_or_advice_backfill(self):
        core=FakeCore(dt.datetime(2026,10,21,13,30,tzinfo=runner.TZ))
        core.md.last_completed_session=lambda moment:'2026-10-20'
        original={'published_at':'2026-10-20T10:51:00+08:00','buy':None}
        core.putkv('daily_plan:2026-10-20',original)
        core.putkv('github_collect',{'finished_at':'2026-10-19T08:10:00+08:00'})
        service=Mock();service.status={'status':'partial','ok':2313,'failed':5,'total':2318}
        with patch.object(runner,'collect_news') as news,patch.object(runner,'load_ai_review',return_value={'status':'partial'}) as ai,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()) as short:
            result=runner.run_stage('recover',core,service)
        service.refresh.assert_called_once_with(force=True)
        short.assert_called_once_with(core,'2026-10-20');news.assert_not_called()
        self.assertEqual(ai.call_args.kwargs['now'],core.now())
        self.assertEqual(ai.call_args.kwargs['signal_date'],'2026-10-20')
        self.assertEqual(result['target_date'],'2026-10-20')
        self.assertFalse(result['morning_collection']);self.assertIn('13:30',result['finished_at'])
        self.assertEqual(core.getkv('daily_plan:2026-10-20'),original)
        self.assertEqual(core.getkv('github_collect')['finished_at'],'2026-10-19T08:10:00+08:00')
        self.assertNotIn('github_publish',core.values);self.assertTrue(runner.public_key('github_recover'))

    def test_empty_preparation_is_pending_instead_of_a_false_prepared_receipt(self):
        core=FakeCore(dt.datetime(2026,10,21,8,25,tzinfo=runner.TZ))
        with patch.object(runner,'update_watchlist',return_value={'status':'insufficient_data','count':0}):
            result=runner.run_stage('publish',core,Mock())
        self.assertEqual(result['status'],'pending')
        self.assertIn('未完成预备',result['reason'])

    def test_timely_ai_push_can_collect_a_missing_completed_session_before_nine(self):
        core=FakeCore(dt.datetime(2026,10,21,8,25,tzinfo=runner.TZ));service=Mock()
        service.reports.return_value=[]
        service.status={'status':'partial','ok':99,'failed':1,'total':100}
        with patch.object(runner,'collect_news',return_value={'status':'verified'}) as news,patch.object(runner,'short_term_refresh',return_value=contextlib.nullcontext()):
            result=runner.run_stage('publish',core,service)
        service.refresh.assert_called_once_with(force=False);news.assert_called_once()
        self.assertEqual(result['stage'],'collect');self.assertEqual(result['requested_stage'],'publish')
        self.assertEqual(result['trigger'],'preopen_missing_completed_report')
        self.assertEqual(result['started_at'],core.now().isoformat())
        self.assertNotIn('github_publish',core.values)

    def test_no_missing_report_collection_is_started_before_eight_or_from_nine(self):
        for hour,minute in ((7,50),(9,0),(9,30)):
            core=FakeCore(dt.datetime(2026,10,21,hour,minute,tzinfo=runner.TZ));service=Mock()
            service.reports.return_value=[]
            with patch.object(runner,'collect_news'),patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=lambda:{'status':'pending'})):
                runner.run_stage('publish',core,service)
            service.refresh.assert_not_called()

    def test_export_created_cohort_is_in_the_final_archive_and_cache(self):
        engine=self.root/'engine';engine.mkdir();(engine/'core.py').touch()
        core=self.closing_core();core.init_db=Mock();core.Service=Mock(return_value=Mock())
        output=self.root/'public';captured={}
        cohort={'date':'2026-10-20','items':[{'symbol':'sh600001','score':70}],'published_at':None}
        def export(*args,**kwargs):
            core.putkv('daily_watchlist:2026-10-20',copy.deepcopy(cohort))
            (output/'data').mkdir(parents=True)
            (output/'data/manifest.json').write_text('{"generated_at":"2026-10-19T17:00:00+08:00"}')
            (output/'index.html').touch()
        def archive(*args):
            captured['archive']=copy.deepcopy(core.values);return 1
        def cache(*args):captured['cache']=copy.deepcopy(core.values)
        modules={'core':core,'export_static':types.SimpleNamespace(export_site=export)}
        with patch.object(runner,'restore_cache',return_value=True),patch.object(runner,'restore_plans',return_value=0),patch.object(runner,'save_plans',return_value=0),patch.object(runner,'save_cache',side_effect=cache),patch.object(runner,'needs_cached_recalculation',return_value=False),patch.object(runner,'run_stage',return_value={'status':'success','stage':'fundamentals'}),patch.object(runner.importlib,'import_module',side_effect=lambda name:modules[name]),patch.object(runner,'restore_watchlists',return_value=0),patch.object(runner,'save_watchlists',side_effect=archive),patch.dict(os.environ,{'GITHUB_OUTPUT':''}):
            self.assertEqual(runner.main(['fundamentals','--engine-dir',str(engine),'--state-dir',str(self.root/'state'),'--output-dir',str(output)]),0)
        for name in ('archive','cache'):
            self.assertEqual(captured[name]['daily_watchlist:2026-10-20'],cohort)
        manifest=json.loads((output/'data/manifest.json').read_text())
        self.assertEqual(manifest['run']['archived_watchlists'],1)
        self.assertEqual(json.loads((output/'data/run-status.json').read_text())['archived_watchlists'],1)
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
        self.assertEqual(news.call_count,2);short.assert_not_called();service.refresh.assert_not_called()

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
        service.refresh.assert_called_once_with(force=False);self.assertEqual(news.call_count,2)

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
        self.assertEqual(result['watchlist']['count'],5)

    def test_new_financial_date_runs_full_research_without_daily_publication(self):
        core=FakeCore();core.putkv('longterm',{'as_of':'2026-10-15','coverage':{'valuation_qualified':836,'financial_complete':836}})
        refresh=Mock(return_value={'as_of':'2026-10-16','coverage':{'valuation_qualified':836,'financial_complete':800}})
        with patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(refresh_fundamentals=refresh)) as imports:
            result=runner.run_stage('fundamentals',core,Mock())
        self.assertEqual(result['status'],'partial');refresh.assert_called_once()
        imports.assert_called_once_with('research_refresh');self.assertNotIn('github_publish',core.values)
        self.assertEqual(result['watchlist']['count'],5)

    def test_zero_or_unfinished_financial_coverage_never_counts_as_complete(self):
        for coverage in ({'valuation_qualified':0,'financial_complete':0},
                         {'valuation_qualified':10,'financial_complete':10,'not_researched':2}):
            core=FakeCore()
            refresh=Mock(return_value={'as_of':'2026-10-16','coverage':coverage})
            with patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(refresh_fundamentals=refresh)):
                result=runner.run_stage('fundamentals',core,Mock())
            self.assertEqual(result['status'],'partial');self.assertIn('watchlist',result)

    def advice(self,date='2026-10-16',symbol='sh600298',sell_date='2026-10-19'):
        return {'date':date,'status':'published','published_at':date+'T09:00:00+08:00',
            'buy':{'symbol':symbol,'name':'原始研究建议','recommended_date':date,'sell_date':sell_date,
                   'entry_low':10,'entry_high':10.05,'take_profit':11,'stop_loss':9},'sell':[],'news':[]}

    def archive(self,plans,name='archive.json'):
        path=self.root/name
        path.write_text(runner.canonical({'schema_version':1,'plans':plans,
            'sha256':hashlib.sha256(runner.canonical(plans).encode()).hexdigest()}),encoding='utf-8')
        return path

    def watchlist(self):
        item={'symbol':'sh600001','name':'观察样本','asof':'2026-10-16','score':55,
              'eligible':False,'actionable':False,'model_validated':False}
        return {'protocol':'daily-observation-v1','date':'2026-10-19','data_date':'2026-10-16',
                'status':'watch_only','model_validated':False,'actionable':False,'eligible':False,
                'generated_at':'2026-10-19T08:25:00+08:00','release_at':'2026-10-19T09:00:00+08:00',
                'published_at':None,'items':[item],'top_pick':item}

    def test_watchlist_archive_preserves_prepared_and_immutable_published_rankings(self):
        database=self.root/'watch.sqlite3';make_db(database);core=FakeCore(dt.datetime(2026,10,19,10,tzinfo=runner.TZ),database=database)
        value=self.watchlist();key='daily_watchlist:'+value['date'];path=self.root/'daily-watchlists.json'
        with core.connect() as con:con.execute('INSERT INTO kv VALUES(?,?)',(key,runner.encode(value)))
        self.assertTrue(runner.public_key(key));self.assertFalse(runner.public_key('daily_watchlist:private'))
        self.assertEqual(runner.save_watchlists(core,path),1)
        first=path.read_bytes();runner.save_watchlists(core,path);self.assertEqual(first,path.read_bytes())
        with core.connect() as con:con.execute('DELETE FROM kv')
        self.assertEqual(runner.restore_watchlists(path,core),1)
        published=dict(value,published_at='2026-10-19T09:03:00+08:00')
        with core.connect() as con:con.execute('UPDATE kv SET value=?',(runner.encode(published),))
        self.assertEqual(runner.restore_watchlists(path,core),0,'Prepared snapshots never replace released ranks')
        tampered=dict(published,items=[dict(value['items'][0],score=99)])
        tampered['top_pick']=tampered['items'][0]
        cohorts={value['date']:tampered}
        path.write_text(runner.canonical({'schema_version':1,'watchlists':cohorts,'sha256':hashlib.sha256(runner.canonical(cohorts).encode()).hexdigest()}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'冲突'):runner.restore_watchlists(path,core)
        with core.connect() as con:self.assertEqual(json.loads(con.execute('SELECT value FROM kv').fetchone()[0]),published)

    def test_watchlist_archive_rejects_actionable_or_forged_release_records(self):
        for mutate in (lambda x:x.update(published_at='2026-10-19T08:59:00+08:00'),
                       lambda x:x['items'][0].update(actionable=True),
                       lambda x:x.update(data_date='2026-10-19')):
            value=self.watchlist();mutate(value)
            with self.assertRaises(ValueError):runner.checked_watchlists({value['date']:value})

    def test_released_watchlist_merges_new_risks_and_neither_old_cache_nor_old_archive_erases_withdrawal(self):
        database=self.root/'merge-watch.sqlite3';make_db(database)
        core=FakeCore(dt.datetime(2026,10,19,10,tzinfo=runner.TZ),database=database)
        base=dict(self.watchlist(),published_at='2026-10-19T09:03:00+08:00',review_at='2026-10-19T09:04:00+08:00')
        key='daily_watchlist:'+base['date'];path=self.root/'watch-archive.json'
        def write(value):
            cohorts={value['date']:value}
            path.write_text(runner.canonical({'schema_version':1,'watchlists':cohorts,'sha256':hashlib.sha256(runner.canonical(cohorts).encode()).hexdigest()}),encoding='utf-8')
        def saved():
            with core.connect() as con:return json.loads(con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()[0])
        with core.connect() as con:con.execute('INSERT INTO kv VALUES(?,?)',(key,runner.encode(base)))
        fresh=dict(base,review_at='2026-10-19T09:11:00+08:00',warnings=['新增已知风险'],
            alerts=[{'symbol':'sh600001','source':'官方公告已知风险','reason':'风险提示','withdrawn':True,'observed_at':'2026-10-19T09:10:00+08:00'}],
            withdrawn_symbols=['sh600001'],rankings_immutable=True,
            current_risk_verification={'announcements_status':'verified','evidence_cutoff':'2026-10-19T09:11:00+08:00'})
        write(fresh);self.assertEqual(runner.restore_watchlists(path,core),1)
        self.assertEqual(saved()['items'],base['items']);self.assertEqual(saved()['published_at'],base['published_at'])
        write(base);self.assertEqual(runner.restore_watchlists(path,core),0)
        self.assertEqual(saved()['withdrawn_symbols'],['sh600001'])
        old_hash=json.loads(path.read_text(encoding='utf-8'))['sha256']
        runner.save_watchlists(core,path)
        archive=json.loads(path.read_text(encoding='utf-8'));self.assertNotEqual(archive['sha256'],old_hash)
        self.assertEqual(archive['watchlists'][base['date']]['withdrawn_symbols'],['sh600001'])
        with core.connect() as con:con.execute('UPDATE kv SET value=? WHERE key=?',(runner.encode(base),key))
        self.assertEqual(runner.restore_watchlists(path,core),1)
        self.assertEqual(saved()['withdrawn_symbols'],['sh600001'])

    def test_released_watchlist_rejects_any_original_ranking_or_timestamp_change(self):
        base=dict(self.watchlist(),published_at='2026-10-19T09:03:00+08:00')
        base['items'].append(dict(base['items'][0],symbol='sh600002',score=50))
        moment=dt.datetime(2026,10,19,10,tzinfo=runner.TZ)
        def reorder(value):value['items'].reverse();value['top_pick']=value['items'][0]
        def rescore(value):value['items'][0]['score']=99;value['top_pick']=value['items'][0]
        for mutate in (reorder,rescore,lambda value:value.update(generated_at='2026-10-19T08:26:00+08:00'),
                       lambda value:value.update(published_at='2026-10-19T09:04:00+08:00'),
                       lambda value:value.update(top_pick=value['items'][1])):
            incoming=copy.deepcopy(base);mutate(incoming)
            with self.assertRaisesRegex(ValueError,'冲突'):runner.merge_watchlist_risks(base,incoming,moment)

    def test_future_risk_review_or_observation_cannot_enter_a_released_cohort(self):
        base=dict(self.watchlist(),published_at='2026-10-19T09:03:00+08:00')
        fresh=dict(base,review_at='2026-10-19T09:11:00+08:00',
            alerts=[{'symbol':'sh600001','source':'官方公告已知风险','reason':'风险提示','withdrawn':True,'observed_at':'2026-10-19T09:10:00+08:00'}],
            withdrawn_symbols=['sh600001'],current_risk_verification={'evidence_cutoff':'2026-10-19T09:11:00+08:00'})
        moment=dt.datetime(2026,10,19,10,tzinfo=runner.TZ)
        for mutate in (lambda value:value.update(review_at='2026-10-19T11:00:00+08:00'),
                       lambda value:value['current_risk_verification'].update(evidence_cutoff='2026-10-19T11:00:00+08:00'),
                       lambda value:value['alerts'][0].update(observed_at='2026-10-19T11:00:00+08:00')):
            incoming=copy.deepcopy(fresh);mutate(incoming)
            with self.assertRaises(ValueError):runner.merge_watchlist_risks(base,incoming,moment)

    def test_real_current_quote_evidence_survives_archive_without_rewriting_the_cohort(self):
        database=self.root/'quote-watch.sqlite3';make_db(database)
        core=FakeCore(dt.datetime(2026,10,19,14,tzinfo=runner.TZ),database=database)
        base=dict(self.watchlist(),published_at='2026-10-19T13:40:00+08:00')
        evidence={'quote_basis':'next_session_previous_close','quote_date':'2026-10-19',
            'price_reference_date':'2026-10-16','quote_consistent':True,'quote_source':'https://qt.gtimg.cn/',
            'quote_datetime':'2026-10-19T13:45:00+08:00','quote_fetched_at':'2026-10-19T13:45:01+08:00',
            'previous_close':10.02}
        fresh=dict(base,review_at='2026-10-19T13:46:00+08:00',rankings_immutable=True,
            current_risk_verification={'evidence_cutoff':'2026-10-19T13:46:00+08:00','quotes':{'sh600001':evidence}})
        key='daily_watchlist:'+base['date'];path=self.root/'quote-archive.json'
        with core.connect() as con:con.execute('INSERT INTO kv VALUES(?,?)',(key,runner.encode(fresh)))
        self.assertEqual(runner.save_watchlists(core,path),1)
        with core.connect() as con:con.execute('UPDATE kv SET value=?',(runner.encode(base),))
        self.assertEqual(runner.restore_watchlists(path,core),1)
        with core.connect() as con:saved=json.loads(con.execute('SELECT value FROM kv').fetchone()[0])
        self.assertEqual(saved['items'],base['items']);self.assertEqual(saved['published_at'],base['published_at'])
        self.assertEqual(saved['current_risk_verification']['quotes']['sh600001'],evidence)
        for mutate in (lambda q:q.update(quote_fetched_at='2026-10-19T15:00:00+08:00'),
                       lambda q:q.update(quote_date='2026-10-20'),lambda q:q.update(previous_close=-1),
                       lambda q:q.update(quote_source='https://example.com/'),lambda q:q.update(actionable=True)):
            incoming=copy.deepcopy(fresh);mutate(incoming['current_risk_verification']['quotes']['sh600001'])
            with self.assertRaises(ValueError):runner.merge_watchlist_risks(base,incoming,core.now())

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


class BriefRunnerTests(unittest.TestCase):
    def test_late_collect_updates_only_brief_and_never_runs_market_or_formal_news(self):
        for hour,minute in ((9,30),(12,0)):
            core=FakeCore(dt.datetime(2026,10,19,hour,minute,tzinfo=runner.TZ));service=Mock()
            formal={'as_of':'2026-10-19T08:20:00+08:00','status':'verified'};core.putkv('morning_news',formal)
            with patch.object(runner,'refresh_market_brief') as brief,patch.object(runner,'collect_news') as news,patch.object(runner,'update_watchlist',return_value={'status':'watch_only','count':5}):
                result=runner.run_stage('collect',core,service)
            self.assertEqual(result['status'],'late_skipped');brief.assert_called_once_with(core)
            news.assert_not_called();service.refresh.assert_not_called();self.assertEqual(core.getkv('morning_news'),formal)

    def test_preopen_publish_with_current_report_refreshes_news_without_market(self):
        core=FakeCore();service=Mock();service.reports.return_value=[{'date':'2026-10-16'}]
        with patch.object(runner,'collect_news') as news,patch.object(runner,'load_ai_review'),patch.object(runner,'update_watchlist',return_value={'status':'watch_only','count':5}):
            self.assertEqual(runner.run_stage('publish',core,service)['status'],'prepared')
        news.assert_called_once_with(core);service.refresh.assert_not_called()

    def test_brief_cache_retains_original_time_and_never_refreshes_formal_risk_receipt(self):
        core=FakeCore(dt.datetime(2026,10,19,12,tzinfo=runner.TZ))
        old={'protocol':'market-brief-v1','date':'2026-10-19','status':'complete','generated_at':'2026-10-19T11:50:00+08:00'}
        formal={'as_of':'2026-10-19T08:20:00+08:00'};core.putkv('morning_news',formal);core.putkv('market_brief:2026-10-19',old)
        with patch.object(runner.importlib,'import_module') as imports:self.assertIs(runner.refresh_market_brief(core),old)
        imports.assert_not_called();self.assertEqual(core.getkv('morning_news'),formal)
        self.assertTrue(runner.public_key('market_brief:2026-10-19'));self.assertFalse(runner.public_key('market_brief:private'))

    def test_late_collection_failure_is_explicit_and_does_not_raise_or_destroy_formal_news(self):
        core=FakeCore(dt.datetime(2026,10,19,12,tzinfo=runner.TZ));formal={'status':'verified'};core.putkv('morning_news',formal)
        with patch.object(runner.importlib,'import_module',side_effect=RuntimeError('provider unavailable')):
            value=runner.refresh_market_brief(core)
        self.assertEqual(value['status'],'unavailable');self.assertEqual(value['date'],'2026-10-19')
        self.assertIsNone(value['generated_at']);self.assertEqual(core.getkv('morning_news'),formal)

    def test_five_minute_preopen_news_cache_cannot_impersonate_nine_oclock_recheck(self):
        core=FakeCore(dt.datetime(2026,10,19,8,59,tzinfo=runner.TZ))
        old={'date':'2026-10-19','as_of':'2026-10-19T08:58:00+08:00','status':'verified',
            'verification_protocol':'official-sources-v3-risk31-v1','session_risks':{'status':'complete'},'market_brief':{'status':'complete'}}
        core.putkv('morning_news',old)
        with patch.object(runner.importlib,'import_module') as imports:self.assertIs(runner.collect_news(core),old)
        imports.assert_not_called()
        core.now=lambda:dt.datetime(2026,10,19,9,tzinfo=runner.TZ)
        news=Mock(return_value={'date':'2026-10-19','status':'verified','items':[],'risk_exclusions':[]})
        modules={'news_collector':types.SimpleNamespace(collect=news),'session_risks':types.SimpleNamespace(collect=lambda date:{'status':'complete'})}
        with patch.object(runner.importlib,'import_module',side_effect=lambda name:modules[name]):runner.collect_news(core)
        news.assert_called_once();self.assertEqual(core.getkv('morning_news')['as_of'],'2026-10-19T09:00:00+08:00')


if __name__=='__main__':unittest.main()
