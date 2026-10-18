import contextlib
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
