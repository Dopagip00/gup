import contextlib
import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
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

    def test_afternoon_start_cannot_fabricate_morning_collection(self):
        core=FakeCore(dt.datetime(2026,10,19,16,tzinfo=runner.TZ));service=Mock()
        with patch.object(runner,'collect_news') as news:
            self.assertEqual(runner.run_stage('collect',core,service)['status'],'late_skipped')
            news.assert_not_called();service.refresh.assert_not_called()

    def test_collection_starts_market_work_only_between_eight_and_nine(self):
        for hour,minute,second,expected in ((7,59,59,'skipped'),(8,0,0,'partial'),
                (8,59,59,'partial'),(9,0,0,'skipped'),(9,29,59,'skipped'),
                (9,30,0,'late_skipped'),(16,0,0,'late_skipped')):
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
        for hour,minute,recheck in ((9,0,True),(9,29,True),(9,30,False),(16,0,False)):
            core=FakeCore(dt.datetime(2026,10,19,hour,minute,tzinfo=runner.TZ));order=[]
            publish=Mock(side_effect=lambda:order.append('publish') or {'status':'published'})
            with patch.object(runner,'collect_news',side_effect=lambda core:order.append('news')) as news,patch.object(runner.importlib,'import_module',return_value=types.SimpleNamespace(publish=publish)):
                runner.run_stage('publish',core,Mock())
            self.assertEqual(order,['news','publish'] if recheck else ['publish'])
            self.assertEqual(news.call_count,int(recheck))

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
