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
        for lag in (180,90,0):
            end=dt.date.fromisoformat(date)-dt.timedelta(days=lag)
            first=end-dt.timedelta(days=82)
            folds.append({'training_label_end':(first-dt.timedelta(days=10)).isoformat(),
                'feature_start':first.isoformat(),'feature_end':(end-dt.timedelta(days=2)).isoformat(),
                'label_end':end.isoformat(),'samples':60,'training_samples':120,'embargo_samples':5,
                'mae_pct':1,'baseline_mae_pct':1.2,'zero_baseline_mae_pct':1.3,'beats_both_baselines':True,
                'direction_accuracy_pct':60,'baseline_direction_accuracy_pct':55})
        return {'symbol':symbol,'name':symbol,'date':date,'eligible':eligible,'excluded':False,'stale':False,
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
    def prepare_morning(self,date):
        moment=dt.datetime.fromisoformat(date+'T09:00:00+08:00')
        target=md.last_completed_session(moment)
        core.putkv('last_success',{'target_date':target,'finished_at':date+'T08:20:00+08:00','model_version':core.forecast.MODEL_VERSION})
        window=min(dt.date.fromisoformat(target),moment.date()-dt.timedelta(days=4)).isoformat()
        core.putkv('morning_news',{'date':date,'status':'verified','as_of':date+'T08:30:00+08:00',
            'collection_started_at':date+'T08:00:00+08:00','window_start':window,'window_end':date,
            'session_risks':{'status':'complete','session_date':date,'source_url':daily_plan.SESSION_RISK_SOURCE,
                'as_of':date+'T08:30:00+08:00','scope':{'source_scope_complete':True},
                'records':[{'productCode':'110001'}],'risk_exclusions':[]},
            'sources':[{'source':name,'url':url,'status':'complete','window_start':window,'window_end':date,
                        'observed_at':date+'T08:30:00+08:00'} for url,(name,_) in daily_plan.NEWS_SOURCES.items()],
            'items':[{'title':'已解析来源与日期的晨间市场资料','url':'https://www.sse.com.cn/disclosure/announcement/general/',
                      'published_at':date+'T08:00:00+08:00','source':'上海证券交易所·上市公司公告',
                      'source_url':'https://www.sse.com.cn/disclosure/listedinfo/announcement/',
                      'observed_at':date+'T08:30:00+08:00'}]})
    def run_day(self,date,stocks,minute=0,hour=9,prepare=True,coverage=None):
        now=dt.datetime.fromisoformat(date+'T'+str(hour).zfill(2)+':'+str(minute).zfill(2)).replace(tzinfo=core.TZ)
        if prepare:self.prepare_morning(date)
        with patch.object(core,'now',return_value=now),patch.object(core,'Service') as service:
            service.return_value.dashboard.return_value={'stocks':stocks,'meta':{'coverage':coverage or {'total':100,'ok':100}}}
            result=daily_plan.publish()
            if result['status']=='published':
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
        receipt={'target_date':'2026-10-16','finished_at':date+'T08:40:00+08:00','status':'partial','ok':99,'failed':1,'total':100,'model_version':core.forecast.MODEL_VERSION}
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
