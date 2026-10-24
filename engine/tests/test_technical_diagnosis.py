import copy
import datetime as dt
import math
import unittest
import market_data as md
import technical_diagnosis as tech

NOW=dt.datetime(2026,10,24,11,tzinfo=tech.TZ)
CAL={str(y):md.get_calendar(y) for y in (2025,2026)}


def bars():
    start=dt.date(2025,7,1);end=dt.date(2026,10,23);rows=[]
    for i in range((end-start).days+1):
        day=start+dt.timedelta(days=i)
        if day.weekday()<5 and day.isoformat() not in CAL[str(day.year)]['holidays']:
            p=100+len(rows)*.1
            rows.append(dict(date=day.isoformat(),open=p,high=p+1,low=p-1,close=p,volume=100))
    return rows


class DiagnosisTests(unittest.TestCase):
    def setUp(self):self.rows=bars();self.target=self.rows[-1]['date']
    def diagnose(self,rows=None,**kw):return tech.diagnose(self.rows if rows is None else rows,'sh600001',self.target,calendar=CAL,now=NOW,**kw)

    def test_closed_form_indicators_after_single_jump(self):
        rows=[dict(r,open=100,high=100,low=100,close=100) for r in self.rows]
        flat=tech.indicators(rows)
        self.assertEqual(flat['macd']['dif'],0);self.assertEqual(flat['rsi14'],50)
        self.assertEqual(flat['kdj'],dict(k=50,d=50,j=50))
        self.assertEqual(flat['boll']['width_pct'],0)
        rows[-1].update(open=100,high=110,low=100,close=110)
        report=tech.indicators(rows);dif=10*(2/13-2/27)
        self.assertAlmostEqual(report['macd']['dif'],dif)
        self.assertAlmostEqual(report['macd']['dea'],dif*.2)
        self.assertEqual(report['macd']['cross'],'golden');self.assertEqual(report['rsi14'],100)
        self.assertAlmostEqual(report['boll']['upper'],100.5+2*math.sqrt(4.75))
        self.assertAlmostEqual(report['kdj']['k'],200/3)
        self.assertAlmostEqual(report['kdj']['d'],(100+200/3)/3)

    def test_future_data_does_not_change_diagnosis_or_inputs(self):
        original=copy.deepcopy(self.rows);future=dict(self.rows[-1],date='2099-01-01',close=9999)
        self.assertEqual(self.diagnose(),self.diagnose(self.rows+[future]));self.assertEqual(original,self.rows)
        self.assertFalse(self.diagnose()['ranking_applied'])

    def test_weekly_uses_only_completed_sessions_and_holiday_short_week(self):
        thursday=tech.weekly_trend(self.rows,'2026-10-22',CAL)
        self.assertEqual(thursday['as_of'],'2026-10-16')
        friday=tech.weekly_trend(self.rows,self.target,CAL)
        self.assertEqual(friday['as_of'],'2026-10-23')
        holiday=tech.weekly_trend(self.rows,'2026-09-30',CAL)
        self.assertEqual(holiday['as_of'],'2026-09-30')
        self.assertEqual(tech.weekly_trend(self.rows,self.target,{})['regime'],'unknown')

    def test_missing_session_and_corrupt_data_cannot_produce_complete_report(self):
        for rows in (self.rows[:-5]+self.rows[-4:],self.rows+[self.rows[-1]],self.rows[-119:]):
            self.assertEqual(self.diagnose(rows)['status'],'unavailable')
        rows=copy.deepcopy(self.rows);rows[-1]['volume']=float('nan')
        self.assertEqual(self.diagnose(rows)['status'],'unavailable')

    def test_cap_is_float_not_total_and_mid_caps_have_no_invented_turnover_threshold(self):
        quote=dict(symbol='sh600001',date=self.target,datetime=self.target+'T16:00:00+08:00',
                   fetched_at=self.target+'T16:01:00+08:00',source='https://qt.gtimg.cn/',
                   circulating_cap_100m=100,market_cap_100m=5000,turnover_ratio=8)
        self.assertEqual(self.diagnose(quote=quote)['cap']['size'],'small')
        self.assertEqual(self.diagnose(quote=quote)['cap']['activity'],'active')
        self.assertEqual(self.diagnose(quote=dict(quote,circulating_cap_100m=500))['cap']['activity'],'unclassified')
        self.assertEqual(self.diagnose(quote=dict(quote,circulating_cap_100m=1500))['cap']['activity'],'very_active')
        for change in ({'circulating_cap_100m':None},{'date':'2026-10-22'},{'fetched_at':'2099-01-01T16:01:00+08:00'}):
            self.assertEqual(self.diagnose(quote=dict(quote,**change))['cap']['size'],'unknown')

    def test_supports_are_below_price_even_after_breakdown(self):
        rows=copy.deepcopy(self.rows);rows[-1].update(open=1,high=1,low=1,close=1)
        result=self.diagnose(rows)
        for level in result['levels']:
            if level['role']=='support':self.assertLess(level['price'],1)
            if level['role']=='resistance':self.assertGreater(level['price'],1)
        ma20=next(x for x in result['levels'] if x['label']=='ma20')
        self.assertEqual(ma20['role'],'resistance')

    def test_weekly_decline_is_a_research_veto_not_a_buy_or_rewritten_score(self):
        rows=[dict(r,open=400-r['open'],close=400-r['close'],high=400-r['low'],low=400-r['high']) for r in self.rows]
        result=self.diagnose(rows)
        self.assertEqual(result['weekly']['regime'],'down')
        self.assertFalse(result['shadow_weekly_filter']['would_pass'])
        self.assertFalse(result['ranking_applied']);self.assertNotIn('score',result)


if __name__=='__main__':unittest.main()
