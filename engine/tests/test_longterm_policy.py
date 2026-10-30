import copy
import datetime as dt
import unittest
import longterm
import longterm_policy as p
import market_data as md
from test_longterm import STOCK,QUOTE,reports


class PolicyTests(unittest.TestCase):
    def rows(self):
        rows=reports()
        for year in (2024,2023):
            rows.append(dict(rows[1],REPORT_DATE=f'{year}-12-31',NOTICE_DATE=f'{year+1}-03-20',UPDATE_DATE=f'{year+1}-03-20'))
        return rows

    def assess(self,rows=None):
        rows=self.rows() if rows is None else rows
        item=longterm._analyze(STOCK,QUOTE,'2026-10-16',rows)
        item['long_horizon']=p.assess(item,longterm._published(rows,STOCK['symbol'],'2026-10-16'))
        return item

    def data(self,count=6):
        stocks=[dict(self.assess(),symbol=f'sh60000{i}',industry='same') for i in range(count)]
        return {'policy':p.POLICY,'as_of':'2026-10-16','stocks':stocks,
                'coverage':{'valuation_qualified':count,'financial_complete':count,'not_researched':0}}

    def board(self,data,previous=None,**kwargs):
        args=dict(quotes={},vetoes={},moment=dt.datetime(2026,10,16,17,tzinfo=dt.timezone(dt.timedelta(hours=8))),expected='2026-10-16')
        args.update(kwargs)
        return p.board(data,previous,**args)

    def test_three_years_pass_and_legacy_score_unchanged(self):
        row=self.assess();self.assertEqual(row['long_horizon']['status'],'pass')
        self.assertEqual(row['score'],longterm._analyze(STOCK,QUOTE,'2026-10-16',self.rows())['score'])
        self.assertEqual(len(row['long_horizon']['years']),3)

    def test_missing_annual_and_future_revision_are_unknown(self):
        self.assertEqual(self.assess(reports())['long_horizon']['status'],'unknown')
        rows=self.rows();rows[-1]['UPDATE_DATE']='2026-10-17'
        self.assertEqual(self.assess(rows)['long_horizon']['status'],'unknown')

    def test_growth_cannot_offset_negative_annual_cash_or_profit(self):
        for key in ('PARENTNETPROFIT','NETCASH_OPERATE_PK','KCFJCXSYJLR'):
            rows=self.rows();rows[-1][key]=-1;rows[0]['PARENTNETPROFITTZ']=1000
            self.assertEqual(self.assess(rows)['long_horizon']['status'],'fail')

    def test_valuation_and_debt_thresholds(self):
        item=self.assess();periods=longterm._published(self.rows(),STOCK['symbol'],'2026-10-16')
        for cap in (22*4.99,22*30.001):
            self.assertEqual(p.assess(dict(item,market_cap_100m=cap),periods)['status'],'fail')
        self.assertEqual(p.assess(dict(item,debt_ratio=65),periods)['status'],'fail')

    def test_no_count_or_industry_quota(self):
        b=self.board(self.data());self.assertEqual(len(b['original']),6)
        self.assertEqual(sum(s['eligible'] for s in b['stocks']),6)

    def test_rank_frozen_and_new_member_waits_until_next_month(self):
        data=self.data();first=self.board(data);frozen=copy.deepcopy(first['original'])
        data['stocks'][-1]['long_horizon']['score']=100
        data['stocks'].append(dict(self.assess(),symbol='sh600099'))
        later=self.board(data,first)
        self.assertEqual(later['original'],frozen)
        self.assertFalse(later['stocks'][-1]['eligible'])
        fresh=self.board(data,moment=dt.datetime(2026,11,2,17,tzinfo=dt.timezone(dt.timedelta(hours=8))))
        self.assertEqual(len(fresh['original']),7)

    def test_withdrawal_persists_when_condition_recovers(self):
        data=self.data();first=self.board(data)
        revoked=self.board(data,first,vetoes={'sh600000':{'reason':'governance risk'}})
        recovered=self.board(data,revoked)
        self.assertEqual(recovered['events'],revoked['events'])
        self.assertTrue(recovered['stocks'][0]['withdrawn']);self.assertFalse(recovered['stocks'][0]['eligible'])
        self.assertEqual(len(recovered['original']),6)

    def test_missing_data_suspends_without_permanent_risk(self):
        data=self.data();first=self.board(data)
        data['stocks']=[]
        paused=self.board(data,first)
        self.assertEqual(paused['events'],[]);self.assertTrue(all(not r['eligible'] for r in paused['stocks']))
        self.assertEqual(len(self.board(self.data(),paused)['original']),6)

    def test_incomplete_refresh_does_not_freeze_partial_universe(self):
        data=self.data();data['coverage']['not_researched']=1
        result=self.board(data);self.assertIsNone(result['created_at'])
        self.assertEqual(result['original'],[])

    def test_out_of_prefilter_member_still_withdrawn_using_quote(self):
        data=self.data();first=self.board(data);data['stocks']=data['stocks'][1:]
        result=self.board(data,first,quotes={'sh600000':{'date':'2026-10-16','pe':90,'pb':2}})
        self.assertTrue(result['stocks'][0]['withdrawn'])

    def test_previous_month_cohort_continues_to_receive_risks(self):
        first=self.board(self.data())
        later=self.board(self.data(),first,moment=dt.datetime(2026,11,2,17,tzinfo=dt.timezone(dt.timedelta(hours=8))),vetoes={'sh600000':{'reason':'risk'}})
        self.assertEqual(later['month'],'2026-10');self.assertEqual(len(later['events']),1)

    def test_unverified_total_return_is_not_zero_performance(self):
        result=p.evaluate_year([],[])
        self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['max_drawdown_pct'])

    def test_year_drawdown_is_not_confused_with_entry_loss_or_positive_return(self):
        start,end=dt.date(2025,10,16),dt.date(2026,10,16)
        dates=[(start+dt.timedelta(days=i)).isoformat() for i in range((end-start).days+1)
               if md.is_trading_day(start+dt.timedelta(days=i))]
        curve=[{'date':day,'wealth':100} for day in dates]
        curve[1]['wealth']=200;curve[2]['wealth']=80;curve[-1]['wealth']=120
        result=p.evaluate_year(curve,dates,total_return_verified=True)
        self.assertAlmostEqual(result['max_drawdown_pct'],60)
        self.assertAlmostEqual(result['max_adverse_pct'],20)
        self.assertAlmostEqual(result['total_return_pct'],20)
        self.assertAlmostEqual(result['net_return_pct'],19.7)
        missing=curve[:2]+curve[3:]
        self.assertEqual(p.evaluate_year(missing,dates,total_return_verified=True)['status'],'unavailable')
        self.assertEqual(p.evaluate_year([curve[0],curve[-1]],[dates[0],dates[-1]],total_return_verified=True)['status'],'unavailable')
        result=p.compare_year(curve,missing,dates,total_return_verified=True)
        self.assertIsNone(result['lower_drawdown'])

    def test_ttm_quality_does_not_round_failure_into_a_pass(self):
        rows=self.rows()
        rows[0]['NETCASH_OPERATE_PK']=22e8*0.4996-24e8+12e8
        # The legacy display rounds to .500; the new gate uses original values.
        item=self.assess(rows)
        self.assertEqual(item['operating_cash_to_profit'],0.5)
        self.assertEqual(item['long_horizon']['status'],'fail')


if __name__=='__main__':unittest.main()
