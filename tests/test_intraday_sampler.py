import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from intraday_sampler import (cohort, inspect_rows, TZ, PROTOCOL, previous_session,
                              read_archives, frozen_cohort, research_state, collect)

class EvidenceTests(unittest.TestCase):
    def test_possible_open_interval_is_quarantined_not_used(self):
        rows=[['202610291000','10','10.1','10.2','9.9','100'],['202610291005','10','10.1','10.2','9.9','100']]
        payload={'code':0,'data':{'sh000001':{'m5':rows}}}
        now=dt.datetime(2026,10,29,10,1,57,tzinfo=TZ)
        result=inspect_rows(payload,'sh000001','m5',now,quarantine_pending=True)
        self.assertEqual(result['rows'],rows[:1]);self.assertEqual(result['pending_rows'],rows[1:])
        self.assertFalse(result['usable_for_signals']);self.assertEqual(result['count'],1)
        with self.assertRaises(ValueError):inspect_rows(payload,'sh000001','m5',now)

    def test_future_quarantine_is_bounded_and_not_a_validation_bypass(self):
        past=['202610291000','10','10.1','10.2','9.9','100']
        now=dt.datetime(2026,10,29,10,1,57,tzinfo=TZ)
        for future in ([['202610291010',*past[1:]]], [['202610291003',*past[1:]],['202610291005',*past[1:]]], [['202610291005','NaN',*past[2:]]]):
            payload={'code':0,'data':{'sh000001':{'m5':[past,*future]}}}
            with self.assertRaises(ValueError):inspect_rows(payload,'sh000001','m5',now,quarantine_pending=True)

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

class ArchiveTests(unittest.TestCase):
    def entry(self,day='2026-10-29',clock='09:20',symbols=('sh600000',)):
        record={'protocol':'daily-observation-v1','date':day,'status':'watch_only' if symbols else 'cash',
                'published_at':day+'T09:12:00+08:00','items':[{'symbol':s} for s in symbols]}
        return cohort(record,dt.datetime.fromisoformat(day+'T'+clock+':00+08:00'))

    def write_record(self,root,entry,name='record.json'):
        day=entry['date'];folder=root/day;folder.mkdir(exist_ok=True)
        value={'protocol':PROTOCOL,'date':day,'started_at':day+'T09:19:00+08:00',
               'finished_at':day+'T09:21:00+08:00','cohort':entry}
        path=folder/name;path.write_text(json.dumps(value),encoding='utf-8')
        return path

    def test_official_calendar_retains_session_across_long_holiday(self):
        day=dt.date(2026,10,9)
        with patch('intraday_sampler.md.is_trading_day',side_effect=lambda d:d==dt.date(2026,9,30)):
            self.assertEqual(previous_session(day),dt.date(2026,9,30))
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);self.write_record(root,self.entry(day='2026-09-30'))
                records,errors=read_archives(root,day,dt.datetime(2026,10,9,10,tzinfo=TZ))
                self.assertEqual(records[0]['symbols'],['sh600000']);self.assertEqual(errors,[])

    def test_bad_archive_does_not_crash_or_trust_saved_flags(self):
        now=dt.datetime(2026,10,29,10,tzinfo=TZ)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);entry=self.entry(clock='09:35');entry['preopen_visibility_verified']=True
            path=self.write_record(root,entry)
            data=json.loads(path.read_text());data['finished_at']='2026-10-29T09:36:00+08:00'
            path.write_text(json.dumps(data))
            (path.parent/'bad.json').write_text('[]')
            records,errors=read_archives(root,now.date(),now)
            self.assertFalse(records[0]['preopen_visibility_verified'])
            self.assertEqual(len(errors),1)

    def test_future_archive_or_cohort_time_is_rejected(self):
        now=dt.datetime(2026,10,29,9,20,tzinfo=TZ)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.write_record(root,self.entry())
            records,errors=read_archives(root,now.date(),now)
            self.assertEqual(records,[]);self.assertEqual(len(errors),1)

    def test_first_actual_preopen_evidence_is_frozen(self):
        early=self.entry();late=self.entry(clock='09:55')
        result=frozen_cohort([early],late,dt.datetime(2026,10,29,9,55,tzinfo=TZ))
        self.assertEqual(result['status'],'frozen')
        self.assertEqual(result['cohort']['observed_at'],early['observed_at'])
        self.assertEqual(frozen_cohort([],late,dt.datetime(2026,10,29,9,55,tzinfo=TZ))['status'],'missing')

    def test_order_changes_conflict_but_added_risk_alerts_do_not(self):
        early=self.entry(symbols=('sh600000','sh600001'))
        later=self.entry(clock='09:55',symbols=('sh600001','sh600000'))
        now=dt.datetime(2026,10,29,9,55,tzinfo=TZ)
        result=frozen_cohort([early],later,now)
        self.assertTrue(result['conflict']);self.assertEqual(result['cohort']['symbols'],early['symbols'])
        later=self.entry(clock='09:55',symbols=('sh600000','sh600001'))
        later['record']['alerts']=[{'symbol':'sh600000','reason':'new risk'}]
        self.assertFalse(frozen_cohort([early],later,now)['conflict'])

    def test_open_boundary_and_missing_receipt_never_backdate(self):
        boundary=self.entry(clock='09:30')
        self.assertFalse(boundary['preopen_visibility_verified'])
        now=dt.datetime(2026,10,29,9,45,tzinfo=TZ)
        report={'status':'raw_collected','errors':[], 'frozen_cohort':frozen_cohort([],boundary,now)}
        self.assertEqual(research_state(report,now),'missing_preopen_evidence')

    def test_confirmation_window_does_not_grant_eligibility(self):
        early=self.entry();now=dt.datetime(2026,10,29,9,45,tzinfo=TZ)
        report={'status':'raw_collected','errors':[],'frozen_cohort':frozen_cohort([early],None,now)}
        self.assertEqual(research_state(report,now),'minute_semantics_unverified')
        self.assertEqual(research_state(report,now.replace(hour=10,minute=0,second=1)),'confirmation_window_closed')
        report['errors']=['source_missing']
        self.assertEqual(research_state(report,now),'data_error')

    def test_cash_state_and_calendar_failure_are_archived(self):
        now=dt.datetime(2026,10,29,9,55,tzinfo=TZ);entry=self.entry(symbols=())
        report={'status':'raw_collected','errors':[],'frozen_cohort':frozen_cohort([entry],None,now)}
        self.assertEqual(research_state(report,now),'cash')
        with tempfile.TemporaryDirectory() as folder, patch('intraday_sampler.md.get_calendar',side_effect=ValueError('calendar unavailable')):
            result=collect(Path(folder))
            self.assertEqual(result['research_state'],'data_error')
            self.assertFalse(result['eligible']);self.assertEqual(result['valid_evaluation_samples'],0)
            self.assertEqual(len(list(Path(folder).glob('*/*.json'))),1)

    def test_collection_keeps_previous_candidates_on_current_cash_day(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,29,9,55,tzinfo=TZ)
        current=self.entry(symbols=())['record']
        def source(url):
            if 'watchlists/' in url:return current,'fixture'
            symbol,period=url.split('param=')[1].split(',')[:2]
            rows=[['202610290950','10','10.1','10.2','9.9','100']]
            return {'code':0,'data':{symbol:{period:rows}}},'fixture'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.write_record(root,self.entry(day='2026-10-28'))
            self.write_record(root,self.entry(symbols=()))
            with patch('intraday_sampler.dt.datetime',Clock),patch('intraday_sampler.fetch',side_effect=source),patch('intraday_sampler.time.sleep'):
                result=collect(root)
            self.assertEqual(result['status'],'raw_collected')
            self.assertEqual(result['research_state'],'cash')
            self.assertEqual({s['symbol'] for s in result['sources']},{'sh000001','sh600000'})
            self.assertEqual(result['frozen_cohort']['cohort']['symbols'],[])
            self.assertFalse(result['eligible']);self.assertEqual(result['valid_evaluation_samples'],0)
            self.assertEqual(len(list(root.glob('*/*.json'))),3)

if __name__=='__main__':unittest.main()
