import importlib.util
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('export_static',Path(__file__).resolve().parents[1]/'export_static.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PublicationTests(unittest.TestCase):
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
                md=SimpleNamespace(get_calendar=lambda year:{},CalendarUnavailable=RuntimeError),
                getkv=lambda key,default=None:receipt if key=='ai_review' else default)
            service=Mock();service.dashboard.return_value={'meta':{'data_date':'2026-10-16'},'index':None,'stocks':[]}
            service.longterm.return_value={};service.reports.return_value=[]
            with patch.dict(sys.modules,{'core':fake,'daily_plan':SimpleNamespace(read=lambda:{}),
                                        'forecast_audit':SimpleNamespace(evaluate=lambda core:{})}):
                module.export_site(root/'public',service,engine_dir=engine)
            result=json.loads((root/'public/data/system.json').read_text(encoding='utf-8'))
            self.assertEqual(result['ai_review']['status'],'missing')
            self.assertEqual(result['ai_review']['errors'],['当日复核待完成'])
            self.assertEqual(result['ai_review']['reviewed_count'],0)
            self.assertNotIn('PRIVATE_MARKER',json.dumps(result));self.assertNotIn('review',result['ai_review'])

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
