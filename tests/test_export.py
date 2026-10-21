import importlib.util
import copy
import contextlib
import datetime as dt
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('export_static',Path(__file__).resolve().parents[1]/'export_static.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PublicationTests(unittest.TestCase):
    def test_after_close_keeps_today_primary_and_prepares_next_session_separately(self):
        core=SimpleNamespace(md=SimpleNamespace(is_trading_day=lambda day:dt.date.fromisoformat(day).weekday()<5))
        tz=dt.timezone(dt.timedelta(hours=8))
        self.assertEqual(module.watchlist_export_dates(core,dt.datetime(2026,10,23,17,tzinfo=tz)),('2026-10-23','2026-10-26'))
        self.assertEqual(module.watchlist_export_dates(core,dt.datetime(2026,10,23,9,tzinfo=tz)),('2026-10-23',None))
        self.assertEqual(module.watchlist_export_dates(core,dt.datetime(2026,10,24,10,tzinfo=tz)),('2026-10-26',None))
    def test_observation_export_is_compact_non_actionable_and_preserves_real_preparation_time(self):
        row={'symbol':'sh600001','name':'观察','asof':'2026-10-19','score':53,
             'components':{'technical':80,'financial':None,'weights':{'technical':.6,'financial':.4},'private':'PRIVATE'},
             'verification':{'chase_passed':True,'private':'PRIVATE'},'reasons':['原因'*1000]*50,
             'risk_notes':['风险']*20,'actionable':True,'entry_low':99,'prediction':{'private':'PRIVATE'}}
        source={'protocol':'daily-observation-v1','date':'2026-10-20','data_date':'2026-10-19',
                'prepared_at':'2026-10-20T08:25:00+08:00','release_at':'2026-10-20T09:00:00+08:00',
                'published_at':None,'items':[row]*10,'top_pick':row,'private':'PRIVATE',
                'verification':{'ai_status':'partial','private':'PRIVATE'}}
        result=module.public_watchlist(source)
        self.assertEqual(len(result['items']),5);self.assertEqual(result['top_pick'],result['items'][0])
        self.assertIsNone(result['published_at']);self.assertEqual(result['prepared_at'],source['prepared_at'])
        self.assertFalse(result['actionable']);self.assertFalse(result['items'][0]['model_validated'])
        self.assertIsNone(result['items'][0]['entry_low']);self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertLessEqual(len(result['items'][0]['reasons']),5)
        self.assertTrue(source['items'][0]['actionable'])

    def late_quote_observation(self):
        evidence={'quote_basis':'next_session_previous_close','quote_date':'2026-10-21',
            'price_reference_date':'2026-10-20','quote_consistent':True,'quote_source':'https://qt.gtimg.cn/',
            'quote_datetime':'2026-10-21T14:00:00+08:00','quote_fetched_at':'2026-10-21T14:00:30+08:00',
            'previous_close':10,'private':'PRIVATE_QUOTE_PAYLOAD'}
        row={'symbol':'sh600001','name':'真实迟到观察','asof':'2026-10-20','score':48,
            'components':{'technical':80,'financial':None,'weights':{'technical':.6,'financial':.4}},
            'verification':dict(evidence,financial_complete=False),'reasons':['仅比较此前收盘技术规则'],
            'risk_notes':['当日报价的昨收只佐证此前收盘，不是此前同日估值']}
        current=dict(evidence,quote_fetched_at='2026-10-21T14:01:30+08:00')
        return {'protocol':'daily-observation-v1','date':'2026-10-21','data_date':'2026-10-20',
            'signal_date':'2026-10-20','generated_at':'2026-10-21T14:01:00+08:00',
            'release_at':'2026-10-21T09:00:00+08:00','published_at':'2026-10-21T14:01:00+08:00',
            'publication_timing':'late','late':True,'items':[row],'top_pick':row,
            'review_at':'2026-10-21T14:02:00+08:00',
            'current_risk_verification':{'evidence_cutoff':'2026-10-21T14:02:00+08:00',
                'quotes':{'sh600001':current,'sh600002':{'private':'PRIVATE_FOREIGN_STOCK'}}}}

    def test_late_quote_projection_preserves_original_and_current_evidence_without_relabelling_dates(self):
        source=self.late_quote_observation();before=copy.deepcopy(source)
        result=module.public_watchlist(source)
        evidence=result['items'][0]['verification'];current=result['current_risk_verification']['quotes']['sh600001']
        self.assertEqual({key:evidence[key] for key in module.QUOTE_EVIDENCE_KEYS},
                         {key:source['items'][0]['verification'][key] for key in module.QUOTE_EVIDENCE_KEYS})
        self.assertEqual(evidence['quote_date'],'2026-10-21')
        self.assertEqual(evidence['price_reference_date'],'2026-10-20')
        self.assertEqual(evidence['quote_basis'],'next_session_previous_close')
        self.assertEqual(current['quote_fetched_at'],'2026-10-21T14:01:30+08:00')
        self.assertEqual(set(current),module.QUOTE_EVIDENCE_KEYS)
        self.assertEqual(set(result['current_risk_verification']['quotes']),{'sh600001'})
        self.assertFalse(evidence['financial_complete']);self.assertIsNone(result['items'][0]['components']['financial'])
        self.assertFalse(result['items'][0]['actionable']);self.assertIsNone(result['items'][0]['entry_low'])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(source,before)

    def test_quote_projection_rejects_forged_sources_dates_times_and_unbounded_review_maps(self):
        for change in ({'quote_source':'https://user:secret@qt.gtimg.cn/'},
                       {'quote_source':'https://example.com/'},
                       {'quote_source':'https://qt.gtimg.cn/?token=PRIVATE'},
                       {'quote_basis':'invented'}, {'price_reference_date':'2026-10-21'},
                       {'quote_date':'2026-10-20'}, {'quote_consistent':False},
                       {'quote_datetime':'2026-10-21T14:00:00'},
                       {'quote_fetched_at':'2026-10-21T14:03:00+08:00'},
                       {'previous_close':float('nan')}, {'previous_close':True}):
            with self.subTest(change=change):
                value=self.late_quote_observation();value['items'][0]['verification'].update(change)
                with self.assertRaises(ValueError):module.public_watchlist(value)
        value=self.late_quote_observation()
        value['current_risk_verification']['quotes']['sh600001']['quote_fetched_at']='2026-10-21T14:03:00+08:00'
        with self.assertRaises(ValueError):module.public_watchlist(value)
        value=self.late_quote_observation();value['current_risk_verification']['quotes']={f'sh60000{i}':{} for i in range(6)}
        with self.assertRaises(ValueError):module.public_watchlist(value)

    def test_same_session_quote_projection_keeps_explicitly_unknown_legacy_source(self):
        value=self.late_quote_observation()
        evidence=value['items'][0]['verification']
        evidence.update(quote_basis='same_session_raw_close',quote_date='2026-10-20',quote_source=None,
            quote_datetime='2026-10-20T16:00:00+08:00',quote_fetched_at='2026-10-20T16:01:00+08:00',previous_close=None)
        result=module.public_watchlist(value)
        self.assertIsNone(result['items'][0]['verification']['quote_source'])
        self.assertIsNone(result['items'][0]['verification']['previous_close'])
        self.assertEqual(result['items'][0]['verification']['quote_basis'],'same_session_raw_close')

    def test_system_json_exposes_bounded_ai_state_without_copying_the_raw_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);engine=root/'engine';static=engine/'static';static.mkdir(parents=True)
            (engine/'version.json').write_text(json.dumps({'version':'2.4.0','data_schema_version':1}),encoding='utf-8')
            (engine/'MIGRATION.md').write_text('公开迁移说明',encoding='utf-8')
            (static/'index.html').write_text('<html></html>',encoding='utf-8');(static/'app.js').write_text('',encoding='utf-8')
            receipt={'protocol':'cloud-ai-risk-review-v1','status':'missing','valid':False,'morning_qualified':False,
                'errors':['当日复核待完成'],'review':{'summary':'公开摘要','scope':'官方目录标题',
                    'run_url':'https://chatgpt.com/c/task','run_url_scope':'task_chat',
                    'reviewed_symbols':[],'risk_vetoes':[],'raw_private':'PRIVATE_MARKER'}}
            fake=SimpleNamespace(BASE=engine,now=lambda:dt.datetime(2026,10,19,9,tzinfo=dt.timezone(dt.timedelta(hours=8))),
                md=SimpleNamespace(get_calendar=lambda year:{},is_trading_day=lambda day:True,CalendarUnavailable=RuntimeError),
                getkv=lambda key,default=None:receipt if key=='ai_review' else default)
            @contextlib.contextmanager
            def connection():
                con=sqlite3.connect(':memory:');con.execute('CREATE TABLE kv(key TEXT,value TEXT)')
                try:yield con
                finally:con.close()
            fake.connect=connection
            service=Mock();service.dashboard.return_value={'meta':{'data_date':'2026-10-16'},'index':None,'stocks':[]}
            service.longterm.return_value={};service.reports.return_value=[]
            with patch.dict(sys.modules,{'core':fake,'daily_plan':SimpleNamespace(read=lambda:{}),
                                        'daily_watchlist':SimpleNamespace(read=lambda **kwargs:{'protocol':'daily-observation-v1','date':'2026-10-19','items':[]}),
                                        'forecast_audit':SimpleNamespace(evaluate=lambda core:{})}):
                module.export_site(root/'public',service,engine_dir=engine)
            result=json.loads((root/'public/data/system.json').read_text(encoding='utf-8'))
            self.assertEqual(result['ai_review']['status'],'missing')
            self.assertEqual(result['ai_review']['errors'],['当日复核待完成'])
            self.assertEqual(result['ai_review']['reviewed_count'],0)
            self.assertNotIn('PRIVATE_MARKER',json.dumps(result));self.assertNotIn('review',result['ai_review'])
            watch=json.loads((root/'public/data/daily-watchlist.json').read_text(encoding='utf-8'))
            self.assertEqual(watch['protocol'],'daily-observation-v1');self.assertFalse(watch['actionable'])
            self.assertEqual(json.loads((root/'public/data/watchlists.json').read_text(encoding='utf-8')),[])

    def test_never_publish_account_or_csrf_or_host_path(self):
        data={'meta':{'now':'x','csrf_token':'secret','project_path':'private','config':{'token':'private'}},
              'index':None,'stocks':[],'paper':{'positions':[{'symbol':'private'}],'cash':123},
              'runs':[{'target_date':'2026-10-16','error':'private path','ok':1}]}
        result=module.public_dashboard(data)
        self.assertEqual(set(result),{'meta','index','stocks','runs'})
        serialized=str(result)
        self.assertNotIn('private',serialized)
        self.assertNotIn('secret',serialized)
        self.assertEqual(data['meta']['csrf_token'],'secret')

    def test_decision_dashboard_preserves_summary_without_full_observation_payload(self):
        decision={'protocol':'fixed-nonoverlap-positive-net-v1','eligible':False,
                  'failures':['最近段未胜过基准'],'cost_stress_pct':.30,
                  'minimum_trades':30,'minimum_latest_trades':10,
                  'aggregate':{'samples':90,'trades':0,'net_mean_selected_pct':None},
                  'latest_fold':{'fold':3,'samples':30,'worst_trade_pct':None},
                  'opportunities':[{'feature_date':'2026-10-16'}],
                  'folds':[{'fold':1}], 'grid_method':'repeated explanation',
                  'comparison_basis':'repeated explanation','tail_basis':'repeated explanation',
                  'execution_note':'repeated explanation','unexpected':'private'}
        data={'meta':{},'index':None,'stocks':[{'symbol':'sh600241',
              'prediction':{'reason':'未通过决策验证','validation_decision':decision}}]}
        prediction=module.public_dashboard(data)['stocks'][0]['prediction']
        self.assertEqual(prediction['reason'],'未通过决策验证')
        self.assertNotIn('validation_decision',prediction)
        self.assertIn('opportunities',decision)
        self.assertNotIn('private',str(prediction))

    def test_table_risk_evidence_and_reasons_are_bounded_and_source_is_unchanged(self):
        risk={'version':'anti-chase-v1','as_of_date':'2026-10-19','status':'blocked','blocked':True,
              'gate_passed':False,'metrics':{'large':'x'*10000},'thresholds':{'long':'x'*10000},
              'scope_note':'x'*10000,'reasons':[{'code':str(i),'severity':'block','detail':'风险'*1000,
              'private':'private'} for i in range(5)]}
        data={'meta':{},'stocks':[{'symbol':'sh600241','eligible':False,'chase_risk':risk,
            'prediction':{'eligible':False,'reason':'第一项；第二项；第三项'+('很长'*500),
            'validation_nonoverlapping':{'large':'x'*10000},
            'validation_aggregate':{'samples':180,'mae_pct':2.1,'large':'x'*10000}}}]}
        stock=module.public_dashboard(data)['stocks'][0];prediction=stock['prediction']
        self.assertIsNot(stock.get('eligible'),True);self.assertIsNot(stock['chase_risk'].get('gate_passed'),True)
        self.assertEqual(len(stock['chase_risk']['reasons']),2)
        self.assertTrue(all(len(reason['detail'])<=140 for reason in stock['chase_risk']['reasons']))
        self.assertNotIn('metrics',stock['chase_risk']);self.assertNotIn('scope_note',stock['chase_risk'])
        self.assertNotIn('validation_nonoverlapping',prediction)
        self.assertEqual(prediction['validation_aggregate'],{'samples':180,'mae_pct':2.1})
        self.assertLessEqual(len(prediction['reason']),300);self.assertIn('更多见详情',prediction['reason'])
        self.assertEqual(len(risk['reasons']),5);self.assertIn('metrics',risk)

    def test_qualified_rows_keep_every_ui_gate_and_unavailable_rows_do_not_reveal_old_estimates(self):
        prediction={'available':True,'eligible':True,'model_version':'ridge-cycle-v3',
            'validation_protocol':'purged-expanding-3x60-decision-v1',
            'validation_decision':{'protocol':'fixed-nonoverlap-positive-net-v1','eligible':True,'large':'x'*10000},
            'rank_value':1,'expected_return_pct':2}
        risk={'version':'anti-chase-v1','gate_passed':True,'blocked':False,'reasons':[]}
        data={'meta':{},'stocks':[{'symbol':'sh600000','eligible':True,'prediction':prediction,'chase_risk':risk},
              {'symbol':'sh600001','eligible':False,'prediction':{'available':False,'expected_return_pct':99}}]}
        rows=module.public_dashboard(data)['stocks']
        self.assertTrue(rows[0]['eligible']);self.assertTrue(rows[0]['prediction']['available'])
        self.assertTrue(rows[0]['prediction']['eligible']);self.assertTrue(rows[0]['chase_risk']['gate_passed'])
        self.assertEqual(rows[0]['prediction']['validation_decision'],{'protocol':'fixed-nonoverlap-positive-net-v1','eligible':True})
        self.assertFalse(rows[1]['prediction']['available']);self.assertIsNot(rows[1].get('eligible'),True)

if __name__=='__main__':unittest.main()
