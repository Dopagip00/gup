import datetime as dt
import unittest
from intraday_sampler import cohort, inspect_rows, TZ

class EvidenceTests(unittest.TestCase):
    def test_bad_source_shapes_are_explicit_errors(self):
        now=dt.datetime(2026,10,28,9,45,tzinfo=TZ)
        for payload in (None,[],{'data':[]},{'data':{'sh600000':None}}):
            with self.assertRaises(ValueError):inspect_rows(payload,'sh600000','m5',now)
        with self.assertRaises(ValueError):cohort([],now)
    def test_late_observation_is_not_backdated_visibility(self):
        record={'protocol':'daily-observation-v1','date':'2026-10-28','status':'watch_only',
                'published_at':'2026-10-28T09:12:00+08:00','items':[{'symbol':'sh600000'}]}
        before=dt.datetime(2026,10,28,9,20,tzinfo=TZ)
        self.assertTrue(cohort(record,before)['preopen_visibility_verified'])
        self.assertFalse(cohort(record,before.replace(hour=10))['preopen_visibility_verified'])
        record['date']='2026-10-27'
        with self.assertRaises(ValueError):cohort(record,before)

    def test_invalid_cohort_and_future_publication_rejected(self):
        now=dt.datetime(2026,10,28,9,20,tzinfo=TZ)
        record={'protocol':'daily-observation-v1','date':'2026-10-28','status':'cash','published_at':'2026-10-28T09:30:00+08:00','items':[]}
        with self.assertRaises(ValueError):cohort(record,now)
        record.update(published_at='2026-10-28T09:12:00+08:00',items=[{'symbol':'../config'}])
        with self.assertRaises(ValueError):cohort(record,now)

    def test_raw_rows_never_grant_signal_eligibility(self):
        row=['202610280935','10','10.1','10.2','9.9','100']
        payload={'code':0,'data':{'sh600000':{'m5':[row]}}}
        now=dt.datetime(2026,10,28,9,45,tzinfo=TZ)
        result=inspect_rows(payload,'sh600000','m5',now)
        self.assertFalse(result['semantics_verified']);self.assertFalse(result['usable_for_signals'])
        for rows in ([row,row],[[*row[:1],'NaN',*row[2:]]],[['202610280950',*row[1:]]]):
            payload['data']['sh600000']['m5']=rows
            with self.assertRaises(ValueError):inspect_rows(payload,'sh600000','m5',now)

if __name__=='__main__':unittest.main()
