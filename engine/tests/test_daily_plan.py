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
        return {'symbol':symbol,'name':symbol,'date':date,'eligible':eligible,'excluded':False,'stale':False,
            'prediction':{'eligible':eligible,'rank_value':1.5},'entry_low':10,'entry_high':10.05,'take_profit':11,'stop_loss':9,'raw_close':close}
    def prepare_morning(self,date):
        moment=dt.datetime.fromisoformat(date+'T09:00:00+08:00')
        target=md.last_completed_session(moment)
        core.putkv('last_success',{'target_date':target,'finished_at':date+'T08:20:00+08:00'})
        core.putkv('morning_news',{'date':date,'status':'verified','as_of':date+'T08:30:00+08:00',
            'items':[{'title':'已核验晨间市场资料','url':'https://www.sse.com.cn/disclosure/announcement/general/',
                      'published_at':date+'T08:00:00+08:00','source':'上交所'}]})
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
        receipt={'target_date':'2026-10-16','finished_at':date+'T08:40:00+08:00','status':'partial','ok':99,'failed':1,'total':100}
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
