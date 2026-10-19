import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import core,daily_plan,market_data as md

class DailyPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.override=patch.object(core,'DATA',Path(self.temp.name));self.override.start();core.init_db()
    def tearDown(self):self.override.stop();self.temp.cleanup()
    def stock(self,symbol='sh600298',eligible=True,date='2026-10-16',close=10.4):
        folds=[]
        for number,lag in enumerate((180,90,0),1):
            end=dt.date.fromisoformat(date)-dt.timedelta(days=lag)
            first=end-dt.timedelta(days=82)
            folds.append({'fold':number,'training_label_end':(first-dt.timedelta(days=10)).isoformat(),
                'feature_start':first.isoformat(),'feature_end':(end-dt.timedelta(days=2)).isoformat(),
                'label_end':end.isoformat(),'samples':60,'training_samples':120,'embargo_samples':5,
                'mae_pct':1,'baseline_mae_pct':1.2,'zero_baseline_mae_pct':1.3,'beats_both_baselines':True,
                'direction_accuracy_pct':60,'baseline_direction_accuracy_pct':55})
        stock={'symbol':symbol,'name':symbol,'date':date,'eligible':eligible,'excluded':False,'stale':False,
            'chase_risk':{'version':'anti-chase-v1','as_of_date':date,'blocked':False,'gate_passed':True,
                'entry_ceiling':{'price':10.1,'reference_date':date,'price_basis':'latest_raw_close_scaled_ma20','requires_live_quote':True}},
            'prediction':{'available':True,'eligible':eligible,'rank_value':1.5,'raw_rank_value':1.5,'expected_return_pct':3,
                'cost_stress_return_pct':2.7,'as_of_date':date,'horizon_days':2,
                'target_kind':'next_open_to_following_close','session_alignment':'benchmark_sessions',
                'latest_feature_window_aligned':True,'model_version':core.forecast.MODEL_VERSION,
                'validation_protocol':core.forecast.VALIDATION_PROTOCOL,'validation_protocol_complete':True,
                'round_trip_cost_stress_pct':.3,'validation_folds':folds,
                'validation_feature_start':folds[-1]['feature_start'],'validation_feature_end':folds[-1]['feature_end'],
                'validation_label_end':date,'validation_aggregate':{'samples':180,'mae_pct':1,'baseline_mae_pct':1.2,'zero_baseline_mae_pct':1.3},
                'validation_nonoverlapping':{'samples':90,'direction_accuracy_pct':60,'baseline_direction_accuracy_pct':55}},
            'entry_low':10,'entry_high':10.05,'take_profit':11,'stop_loss':9,'raw_close':close}
        opportunities=[]
        for fold in folds:
            first=dt.date.fromisoformat(fold['feature_start'])
            for i in range(30):
                feature=first+dt.timedelta(days=i*2)
                selected=i%2==0
                gross=2.0 if selected else -2.0
                opportunities.append({'fold':fold['fold'],'feature_date':feature.isoformat(),
                    'entry_date':(feature+dt.timedelta(days=1)).isoformat(),
                    'exit_date':(feature+dt.timedelta(days=2)).isoformat(),
                    'estimate_pct':1.0 if selected else -1.0,'gross_return_pct':gross,
                    'selected':selected,'net_return_pct':gross-.3 if selected else 0,
                    'passive_net_return_pct':gross-.3})
        summary=[dict(fold=fold['fold'],**core.forecast._decision_metrics(
            [item for item in opportunities if item['fold']==fold['fold']])) for fold in folds]
        stock['prediction']['current_feature_max_abs_z']=1.0
        stock['prediction']['validation_decision']={'protocol':core.forecast.DECISION_PROTOCOL,
            'selection_rule':'fold_out_of_fit_estimate_gt_cost','cost_stress_pct':.3,
            'minimum_trades':core.forecast.MIN_DECISION_TRADES,
            'minimum_latest_trades':core.forecast.MIN_LATEST_DECISION_TRADES,
            'eligible':True,'failures':[],'opportunities':opportunities,
            'aggregate':core.forecast._decision_metrics(opportunities),'latest_fold':summary[-1],'folds':summary}
        stock['prediction']['validation_nonoverlapping'].update(
            feature_start=opportunities[0]['feature_date'],feature_end=opportunities[-1]['feature_date'],
            label_end=opportunities[-1]['exit_date'])
        return stock
    def prepare_morning(self,date):
        moment=dt.datetime.fromisoformat(date+'T09:00:00+08:00')
        target=md.last_completed_session(moment)
        core.putkv('last_success',{'target_date':target,'finished_at':date+'T08:20:00+08:00','model_version':core.forecast.MODEL_VERSION,'chase_policy_version':'anti-chase-v1'})
        window=min(dt.date.fromisoformat(target),moment.date()-dt.timedelta(days=4)).isoformat()
        risk_window=(moment.date()-dt.timedelta(days=31)).isoformat()
        core.putkv('morning_news',{'date':date,'status':'verified','as_of':date+'T08:30:00+08:00',
            'verification_protocol':daily_plan.NEWS_VERIFICATION_PROTOCOL,
            'collection_started_at':date+'T08:00:00+08:00','window_start':window,'window_end':date,
            'risk_monitoring':{'protocol':daily_plan.RISK_PROTOCOL,'status':'complete',
                'source_url':daily_plan.ANNOUNCEMENT_SOURCE,'window_start':risk_window,'window_end':date,
                'lookback_days':31,'observed_at':date+'T08:30:00+08:00','semantics':daily_plan.RISK_SEMANTICS,
                'items_scanned':0,'risk_exclusions':[],
                'queries':[{'keyword':word,'title_query':word,'status':'complete',
                    'source_url':daily_plan.ANNOUNCEMENT_SOURCE,'url':daily_plan.ANNOUNCEMENT_SOURCE,
                    'window_start':risk_window,'window_end':date,'observed_at':date+'T08:30:00+08:00',
                    'groups_total':0,'groups_scanned':0,'pages':1} for word in daily_plan.RISK_QUERIES]},
            'session_risks':{'status':'complete','session_date':date,'source_url':daily_plan.SESSION_RISK_SOURCE,
                'as_of':date+'T08:30:00+08:00','scope':{'source_scope_complete':True},
                'records':[{'productCode':'110001'}],'risk_exclusions':[]},
            'sources':[{'source':name,'url':url,'status':'complete','window_start':window,'window_end':date,
                        'observed_at':date+'T08:30:00+08:00'} for url,(name,_) in daily_plan.NEWS_SOURCES.items()],
            'items':[{'title':'已解析来源与日期的晨间市场资料','url':'https://www.sse.com.cn/disclosure/announcement/general/',
                      'published_at':date+'T08:00:00+08:00','source':'上海证券交易所·上市公司公告',
                      'source_url':'https://www.sse.com.cn/disclosure/listedinfo/announcement/',
                      'observed_at':date+'T08:30:00+08:00'}]})
        self.prepare_ai(date,target)
    def prepare_ai(self,date,target):
        review={'protocol':daily_plan.AI_PROTOCOL,'date':date,'signal_date':target,'status':'complete',
            'started_at':date+'T08:25:00+08:00','completed_at':date+'T08:28:00+08:00',
            'reviewed_symbols':['sh600298','sh600312','sh600241'],'risk_vetoes':[],
            'sources':[{'id':'sse-1','url':'https://www.sse.com.cn/disclosure/example.pdf','published_date':target,
                'observed_at':date+'T08:26:00+08:00','scope':'official_directory_title_only'}],
            'summary':'公开来源人工式风险复核。','scope':'公告目录标题，未核验PDF正文。',
            'run_url':'https://chatgpt.com/c/task-fixture','run_url_scope':'task_chat'}
        core.putkv('ai_review',{'protocol':daily_plan.AI_PROTOCOL,'date':date,'signal_date':target,
            'status':'complete','valid':True,'morning_qualified':True,'checked_at':date+'T08:40:00+08:00',
            'errors':[],'review':review})
    def run_day(self,date,stocks,minute=0,hour=9,prepare=True,coverage=None):
        now=dt.datetime.fromisoformat(date+'T'+str(hour).zfill(2)+':'+str(minute).zfill(2)).replace(tzinfo=core.TZ)
        if prepare:self.prepare_morning(date)
        with patch.object(core,'now',return_value=now),patch.object(core,'Service') as service:
            service.return_value.dashboard.return_value={'stocks':stocks,'meta':{'coverage':coverage or {'total':100,'ok':100}}}
            already_published=(core.getkv('daily_plan:'+date,{}) or {}).get('status')=='published'
            result=daily_plan.publish()
            if result['status']=='published' and not already_published:
                service.return_value.dashboard.assert_called_once_with(md.last_completed_session(now.replace(hour=9,minute=0)))
            return result
    def test_weekend_next_session_not_calendar_day(self):
        self.assertEqual(daily_plan.session_after('2026-10-16'),'2026-10-19')
    def test_independent_daily_buy_and_prior_sell_even_when_old_model_fails(self):
        first=self.run_day('2026-10-19',[self.stock()]);self.assertEqual(first['buy']['sell_date'],'2026-10-20')
        nextday=self.run_day('2026-10-20',[self.stock('sh600312',date='2026-10-19'),self.stock(eligible=False,date='2026-10-19')])
        self.assertEqual(nextday['buy']['symbol'],'sh600312');self.assertEqual(nextday['sell'][0]['symbol'],'sh600298')
        self.assertEqual(nextday['sell'][0]['take_profit'],11);self.assertIsNotNone(nextday['sell'][0]['sell_low'])
    def test_repeat_publication_cannot_replace_original_advice(self):
        a=self.run_day('2026-10-19',[self.stock()])
        with patch.object(core,'now',return_value=dt.datetime(2026,10,19,16,tzinfo=core.TZ)),patch.object(core,'Service') as service:
            b=daily_plan.publish()
            service.assert_not_called()
        self.assertEqual(a,b)
    def test_no_forced_buy_for_failed_validation(self):
        result=self.run_day('2026-10-19',[self.stock(eligible=False)]);self.assertIsNone(result['buy'])

    def test_cloud_ai_must_be_current_complete_sourced_and_cover_all_base_candidates(self):
        date='2026-10-19'
        for mutation in ('missing','partial','wrong_signal','wrong_date','late','future_source','wrong_source','partial_coverage'):
            with self.subTest(mutation=mutation):
                self.prepare_morning(date);receipt=core.getkv('ai_review');review=receipt['review']
                if mutation=='missing':receipt={}
                elif mutation=='partial':review['status']='partial'
                elif mutation=='wrong_signal':review['signal_date']='2026-10-15'
                elif mutation=='wrong_date':review['date']='2026-10-16'
                elif mutation=='late':review['completed_at']=date+'T09:30:00+08:00'
                elif mutation=='future_source':review['sources'][0]['observed_at']=date+'T09:01:00+08:00'
                elif mutation=='wrong_source':review['sources'][0]['url']='https://evil.test/a'
                elif mutation=='partial_coverage':review['reviewed_symbols']=['sh600312']
                core.putkv('ai_review',receipt);core.putkv('daily_plan:'+date,{})
                plan=self.run_day(date,[self.stock()],prepare=False)
                self.assertIsNone(plan['buy']);self.assertEqual(plan['status'],'pending');self.assertFalse(plan['ai_review_ready'])
    def test_cloud_ai_only_vetoes_and_never_upgrades_a_failed_model(self):
        date='2026-10-19';self.prepare_morning(date);receipt=core.getkv('ai_review')
        receipt['review']['risk_vetoes']=[{'symbol':'sh600298','reason':'官方目录风险待核验','source_ids':['sse-1']}]
        core.putkv('ai_review',receipt)
        plan=self.run_day(date,[self.stock(),self.stock('sh600312')],prepare=False)
        self.assertEqual(plan['buy']['symbol'],'sh600312')
        core.putkv('daily_plan:'+date,{})
        plan=self.run_day(date,[self.stock(),self.stock('sh600312',eligible=False)],prepare=False)
        self.assertIsNone(plan['buy']);self.assertTrue(plan['ai_review_ready'])
    def test_new_ai_review_cannot_rewrite_an_existing_published_plan(self):
        date='2026-10-19';original=self.run_day(date,[self.stock()])
        core.putkv('ai_review',{})
        self.assertEqual(self.run_day(date,[self.stock('sh600312')],prepare=False,minute=17),original)

    def test_saved_model_flag_cannot_bypass_current_time_and_validation_evidence(self):
        for change in ({'model_version':'obsolete'},{'validation_protocol_complete':False},
                       {'validation_folds':[]},{'validation_aggregate':{'samples':180}},
                       {'as_of_date':'2026-10-15'},{'horizon_days':1},
                       {'latest_feature_window_aligned':False},{'rank_value':float('nan')},
                       {'expected_return_pct':float('inf')},{'cost_stress_return_pct':0}):
            with self.subTest(change=change):
                stock=self.stock();stock['prediction'].update(change)
                core.putkv('daily_plan:2026-10-19',{})
                self.assertIsNone(self.run_day('2026-10-19',[stock])['buy'])

    def test_bad_entry_geometry_or_infinite_coverage_cannot_authorize_buy(self):
        for changes in ({'entry_high':9.5},{'stop_loss':10.1},{'take_profit':9},
                        {'entry_low':float('nan')},{'raw_close':0}):
            stock=self.stock();stock.update(changes);core.putkv('daily_plan:2026-10-19',{})
            self.assertIsNone(self.run_day('2026-10-19',[stock])['buy'])
        for coverage in ({'total':100,'ok':101},{'total':float('inf'),'ok':100},{'total':1,'ok':True}):
            core.putkv('daily_plan:2026-10-19',{})
            self.assertIsNone(self.run_day('2026-10-19',[self.stock()],coverage=coverage)['buy'])

    def test_session_risk_receipt_must_be_complete_current_and_observed_before_publication(self):
        date='2026-10-19'
        for mutation in ('missing','empty','failed','wrong_date','future','bad_scope'):
            with self.subTest(mutation=mutation):
                self.prepare_morning(date);news=core.getkv('morning_news')
                if mutation=='missing':news.pop('session_risks')
                elif mutation=='empty':news['session_risks']['records']=[]
                elif mutation=='failed':news['session_risks']['status']='unavailable'
                elif mutation=='wrong_date':news['session_risks']['session_date']='2026-10-16'
                elif mutation=='future':news['session_risks']['as_of']=date+'T09:01:00+08:00'
                elif mutation=='bad_scope':news['session_risks']['scope']='complete'
                core.putkv('morning_news',news)
                result=self.run_day(date,[self.stock()],prepare=False)
                self.assertIsNone(result['buy']);self.assertEqual(result['research_forecasts'],[])
                core.putkv('daily_plan:'+date,{})

    def test_official_halt_exclusion_blocks_candidate_even_without_flattened_news_list(self):
        date='2026-10-19';self.prepare_morning(date);news=core.getkv('morning_news')
        news['session_risks']['risk_exclusions']=[{'symbol':self.stock()['symbol'],'session_date':date}]
        core.putkv('morning_news',news)
        result=self.run_day(date,[self.stock()],prepare=False)
        self.assertIsNone(result['buy'])
        self.assertTrue(result['research_forecasts'])
        self.assertFalse(result['research_forecasts'][0]['recommendation_qualified'])

    def test_risk_window_protocol_and_all_queries_must_be_complete_before_a_new_buy(self):
        date='2026-10-19'
        for mutation in ('legacy_protocol','missing','short_window','failed','missing_query','duplicate_query',
                         'partial_pages','truncated_count','wrong_source','future','wrong_semantics'):
            with self.subTest(mutation=mutation):
                self.prepare_morning(date);news=core.getkv('morning_news');risk=news['risk_monitoring']
                if mutation=='legacy_protocol':news.pop('verification_protocol')
                elif mutation=='missing':news.pop('risk_monitoring')
                elif mutation=='short_window':risk['window_start']='2026-10-15'
                elif mutation=='failed':risk['status']='failed'
                elif mutation=='missing_query':risk['queries'].pop()
                elif mutation=='duplicate_query':risk['queries'][-1]=risk['queries'][0]
                elif mutation=='partial_pages':risk['queries'][0]['pages']=2
                elif mutation=='truncated_count':risk['queries'][0].update(groups_total=101,groups_scanned=100,pages=2)
                elif mutation=='wrong_source':risk['source_url']='https://example.com/'
                elif mutation=='future':risk['queries'][0]['observed_at']=date+'T09:01:00+08:00'
                elif mutation=='wrong_semantics':risk['semantics']='risk_resolved_after_31_days'
                core.putkv('morning_news',news);core.putkv('daily_plan:'+date,{})
                plan=self.run_day(date,[self.stock()],prepare=False)
                self.assertEqual(plan['status'],'pending');self.assertIsNone(plan['buy'])

    def test_old_official_risk_observation_blocks_candidate_even_if_flat_list_is_missing(self):
        date='2026-10-19';self.prepare_morning(date);news=core.getkv('morning_news');risk=news['risk_monitoring']
        event={'symbol':'sh600241','title':'辽宁时代万恒股份有限公司股票交易风险提示公告',
            'published_at':'2026-10-10','published_precision':'date','source':'上海证券交易所·上市公司公告',
            'source_url':daily_plan.ANNOUNCEMENT_SOURCE,'url':'https://www.sse.com.cn/disclosure/test.pdf',
            'observed_at':date+'T08:30:00+08:00','query_keyword':'股票交易',
            'observation_status':'research_window_veto','resolution_status':'not_determined',
            'observation_window_start':risk['window_start'],'observation_window_end':date}
        risk['items_scanned']=1;risk['risk_exclusions']=[event];risk['queries'][0].update(groups_total=1,groups_scanned=1)
        core.putkv('morning_news',news)
        plan=self.run_day(date,[self.stock('sh600241')],prepare=False)
        self.assertTrue(plan['morning_ready']);self.assertEqual(plan['status'],'published');self.assertIsNone(plan['buy'])
        self.assertEqual(plan['news_verification']['risk_monitoring']['risk_exclusions'][0]['published_at'],'2026-10-10')
        self.assertFalse(plan['research_forecasts'][0]['recommendation_qualified'])

    def test_explicit_date_only_official_publication_is_valid_without_faking_a_timestamp(self):
        date='2026-10-19';self.prepare_morning(date);news=core.getkv('morning_news')
        news['items'][0].update(published_at=date,published_precision='date');core.putkv('morning_news',news)
        plan=self.run_day(date,[self.stock()],prepare=False)
        self.assertIsNotNone(plan['buy']);self.assertEqual(plan['news'][0]['published_at'],date)
        for changes in ({'published_precision':None},{'published_at':'2026-10-20','published_precision':'date'},
                        {'published_at':'2026-10-19T08:00:00','published_precision':'date'}):
            self.prepare_morning(date);news=core.getkv('morning_news');news['items'][0].update({'published_at':date,**changes})
            core.putkv('morning_news',news);core.putkv('daily_plan:'+date,{})
            self.assertIsNone(self.run_day(date,[self.stock()],prepare=False)['buy'])

    def test_unrelated_source_or_missing_collection_receipt_cannot_verify_news(self):
        date='2026-10-19'
        for mutation in ('missing_source','mixed_source','no_timezone','old_news','after_observed','short_holiday_window'):
            self.prepare_morning(date);news=core.getkv('morning_news')
            if mutation=='missing_source':news['sources'].pop()
            elif mutation=='mixed_source':news['items'][0]['url']='https://finance.cctv.com/2026/10/19/ARTIx.shtml'
            elif mutation=='no_timezone':news['items'][0]['published_at']=date+'T08:00:00'
            elif mutation=='old_news':news['items'][0]['published_at']='2026-01-01T08:00:00+08:00'
            elif mutation=='after_observed':news['items'][0]['published_at']=date+'T08:31:00+08:00'
            elif mutation=='short_holiday_window':news['window_start']=date
            core.putkv('morning_news',news);core.putkv('daily_plan:'+date,{})
            self.assertIsNone(self.run_day(date,[self.stock()],prepare=False)['buy'])
    def test_cannot_publish_early_or_retroactively(self):
        with patch.object(core,'now',return_value=dt.datetime(2026,10,19,8,59,tzinfo=core.TZ)):
            with self.assertRaises(ValueError):daily_plan.publish()
            with self.assertRaises(ValueError):daily_plan.publish('2026-10-16')

    def test_pending_premarket_can_retry_without_rewriting_a_published_plan(self):
        date='2026-10-19'
        first=self.run_day(date,[self.stock()],prepare=False)
        self.assertEqual(first['status'],'pending');self.assertIsNone(first['published_at'])
        self.assertIsNone(first['buy']);self.assertEqual(first['research_forecasts'],[])
        second=self.run_day(date,[self.stock()],minute=7)
        self.assertEqual(second['status'],'published');self.assertIsNotNone(second['buy'])
        third=self.run_day(date,[self.stock('sh600312')],minute=17)
        self.assertEqual(third,second)

    def test_chase_risk_and_entry_ceiling_are_independent_hard_gates(self):
        date='2026-10-19'
        for change in ({'blocked':True},{'gate_passed':False},{'version':'old'},{'as_of_date':'2026-10-15'},
                       {'entry_ceiling':{'price':9.9,'reference_date':'2026-10-16','price_basis':'latest_raw_close_scaled_ma20','requires_live_quote':True}}):
            core.putkv('daily_plan:'+date,{})
            stock=self.stock();stock['chase_risk'].update(change)
            self.assertIsNone(self.run_day(date,[stock])['buy'])

    def test_slow_generation_crossing_0930_cannot_keep_an_earlier_buy_or_forecast_time(self):
        date='2026-10-19';self.prepare_morning(date)
        start=dt.datetime.fromisoformat(date+'T09:29:59+08:00')
        finish=dt.datetime.fromisoformat(date+'T09:30:01+08:00')
        with patch.object(core,'now',side_effect=[start,finish]),patch.object(core,'Service') as service:
            service.return_value.dashboard.return_value={'stocks':[self.stock()], 'meta':{'coverage':{'total':100,'ok':100}}}
            result=daily_plan.publish()
        self.assertTrue(result['late']);self.assertIsNone(result['buy'])
        self.assertEqual(result['research_forecasts'],[])
        self.assertEqual(result['published_at'],finish.isoformat())
        self.assertEqual(result['attempted_at'],start.isoformat())

    def test_publication_appearing_during_dashboard_read_is_never_overwritten(self):
        date='2026-10-19';self.prepare_morning(date)
        original={'date':date,'status':'published','published_at':date+'T09:00:01+08:00','buy':{'symbol':'sh600312'},'sell':[]}
        def dashboard(target):
            core.putkv('daily_plan:'+date,original)
            return {'stocks':[self.stock()], 'meta':{'coverage':{'total':100,'ok':100}}}
        with patch.object(core,'now',return_value=dt.datetime.fromisoformat(date+'T09:01:00+08:00')),patch.object(core,'Service') as service:
            service.return_value.dashboard.side_effect=dashboard
            self.assertEqual(daily_plan.publish(),original)
        self.assertEqual(core.getkv('daily_plan:'+date),original)

    def test_window_closes_exactly_at_0930(self):
        a=self.run_day('2026-10-19',[self.stock()],29)
        b=self.run_day('2026-10-20',[self.stock('sh600312',date='2026-10-19')],30)
        self.assertIsNotNone(a['buy']);self.assertFalse(a['late'])
        self.assertIsNone(b['buy']);self.assertTrue(b['late']);self.assertEqual(len(b['sell']),1)

    def test_late_recovery_uses_previous_close_and_does_not_create_buy(self):
        self.run_day('2026-10-19',[self.stock()])
        old=self.stock(eligible=False,date='2026-10-19');old['stale']=True
        today=self.stock('sh600312',date='2026-10-20')
        result=self.run_day('2026-10-20',[old,today],hour=16)
        self.assertIsNone(result['buy']);self.assertEqual(result['data_date'],'2026-10-19')
        self.assertEqual(result['published_at'],'2026-10-20T16:00:00+08:00')
        self.assertEqual(result['sell'][0]['sell_low'],round(10.4*1.015*.9975,2))

    def test_research_must_complete_this_morning_for_exact_signal_date(self):
        date='2026-10-19'
        for research in [{}, {'target_date':'2026-10-15','finished_at':date+'T08:20:00+08:00'},
                         {'target_date':'2026-10-16','finished_at':'2026-10-18T08:20:00+08:00'},
                         {'target_date':'2026-10-16','finished_at':date+'T07:59:00+08:00'},
                         {'target_date':'2026-10-16','finished_at':date+'T09:01:00+08:00'},
                         {'target_date':'2026-10-16','finished_at':date+'T08:20:00'}]:
            with self.subTest(research=research):
                self.prepare_morning(date);core.putkv('last_success',research);core.putkv('daily_plan:'+date,{})
                result=self.run_day(date,[self.stock()],prepare=False)
                self.assertIsNone(result['buy']);self.assertFalse(result['morning_ready'])

    def test_news_must_be_verified_current_sourced_and_not_from_the_future(self):
        date='2026-10-19'
        for change in [{'date':'2026-10-18'},{'status':'unavailable'},{'items':[]},
                       {'as_of':date+'T07:59:00+08:00'},{'as_of':date+'T09:01:00+08:00'},
                       {'items':[{'title':'无来源','published_at':date}]},
                       {'items':[{'title':'不安全来源','url':'http://example.com/','published_at':date}]},
                       {'items':[{'title':'未来新闻','url':'https://www.sse.com.cn/','published_at':date+'T10:00:00+08:00'}]}]:
            with self.subTest(change=change):
                self.prepare_morning(date);news=core.getkv('morning_news');news.update(change)
                core.putkv('morning_news',news);core.putkv('daily_plan:'+date,{})
                result=self.run_day(date,[self.stock()],prepare=False)
                self.assertIsNone(result['buy']);self.assertFalse(result['morning_ready'])

    def test_verified_fetched_at_is_supported_without_as_of(self):
        date='2026-10-19';self.prepare_morning(date)
        news=core.getkv('morning_news');news['fetched_at']=news.pop('as_of');core.putkv('morning_news',news)
        self.assertIsNotNone(self.run_day(date,[self.stock()],prepare=False)['buy'])

    def test_missing_news_does_not_drop_yesterdays_sell_reference(self):
        self.run_day('2026-10-19',[self.stock()]);core.putkv('morning_news',{})
        result=self.run_day('2026-10-20',[self.stock(eligible=False,date='2026-10-19')],prepare=False)
        self.assertIsNone(result['buy']);self.assertEqual(result['sell'][0]['status'],'reference')
        self.assertIsNotNone(result['sell'][0]['sell_high'])

    def test_signal_date_must_equal_morning_completed_session(self):
        result=self.run_day('2026-10-19',[self.stock(date='2026-10-19')])
        self.assertIsNone(result['buy']);self.assertEqual(result['data_date'],'2026-10-16')

    def test_low_coverage_or_news_risk_exclusion_prevents_new_buy(self):
        result=self.run_day('2026-10-19',[self.stock()],coverage={'total':100,'ok':98})
        self.assertIsNone(result['buy'])
        date='2026-10-20';self.prepare_morning(date)
        news=core.getkv('morning_news');news['risk_exclusions']=[{'symbol':'sh600298'}];core.putkv('morning_news',news)
        result=self.run_day(date,[self.stock(date='2026-10-19')],prepare=False)
        self.assertIsNone(result['buy'])

    def test_sell_below_original_stop_anchors_to_close_and_preserves_original_levels(self):
        self.run_day('2026-10-19',[self.stock()])
        latest=self.stock(eligible=False,date='2026-10-19');latest['raw_close']=8.5
        result=self.run_day('2026-10-20',[latest])['sell'][0]
        self.assertEqual(result['sell_low'],round(8.5*.9975,2));self.assertEqual(result['sell_high'],round(8.5*1.0025,2))
        self.assertEqual(result['take_profit'],11);self.assertEqual(result['stop_loss'],9)
        self.assertTrue(any('风险' in r for r in result['reasons']))

    def test_missing_or_stale_previous_close_never_reuses_original_target_as_current_sell_price(self):
        self.run_day('2026-10-19',[self.stock()])
        result=self.run_day('2026-10-20',[self.stock(eligible=False)])['sell'][0]
        self.assertEqual(result['status'],'data_unavailable');self.assertIsNone(result['sell_low']);self.assertIsNone(result['sell_high'])
        self.assertEqual(result['take_profit'],11)

    def test_honest_partial_99_percent_completion_can_prepare_morning_without_relabelling_success(self):
        date='2026-10-19';self.prepare_morning(date);core.putkv('last_success',{})
        receipt={'target_date':'2026-10-16','finished_at':date+'T08:40:00+08:00','status':'partial','ok':99,'failed':1,'total':100,'model_version':core.forecast.MODEL_VERSION,'chase_policy_version':'anti-chase-v1'}
        core.putkv('last_completed',receipt)
        result=self.run_day(date,[self.stock()],prepare=False)
        self.assertIsNotNone(result['buy']);self.assertTrue(result['morning_ready']);self.assertEqual(result['research_status'],'partial')
        self.assertEqual(core.getkv('last_completed'),receipt);self.assertEqual(core.getkv('last_success'),{})

    def test_incomplete_or_unqualified_completion_receipts_fail_closed(self):
        date='2026-10-19'
        cases=[{'ok':98,'total':100,'status':'partial'},{'ok':0,'total':0,'status':'partial'},
               {'ok':100,'total':100},{'ok':100,'total':100,'status':'running'},
               {'ok':101,'total':100,'status':'partial'},{'ok':True,'total':1,'status':'partial'},
               {'ok':99,'total':100,'status':'partial','finished_at':date+'T07:59:00+08:00'},
               {'ok':99,'total':100,'status':'partial','target_date':'2026-10-15'}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.prepare_morning(date);core.putkv('last_success',{});core.putkv('daily_plan:'+date,{})
                receipt={'target_date':'2026-10-16','finished_at':date+'T08:40:00+08:00',**changes}
                core.putkv('last_completed',receipt)
                result=self.run_day(date,[self.stock()],prepare=False)
                self.assertIsNone(result['buy']);self.assertFalse(result['morning_ready'])
