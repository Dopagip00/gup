import copy
import datetime as dt
import unittest
import runner
from engine import main_wave


class CashArchiveTests(unittest.TestCase):
    def test_cash_archive_and_immutable_merge_require_recomputed_evidence(self):
        start=dt.date(2026,6,1)
        dates=[(start+dt.timedelta(days=i)).isoformat() for i in range(65)]
        rows=[dict(date=d,open=20-i*.1,high=20-i*.1,low=20-i*.1,close=20-i*.1,volume=100) for i,d in enumerate(dates)]
        target=dates[-1];day=(dt.date.fromisoformat(target)+dt.timedelta(days=1)).isoformat()
        record=dict(protocol='daily-observation-v1',date=day,data_date=target,signal_date=target,
            generated_at=day+'T09:02:00+08:00',published_at=day+'T09:02:00+08:00',release_at=day+'T09:00:00+08:00',
            status='cash',ranking_policy=main_wave.POLICY,model_validated=False,eligible=False,actionable=False,
            items=[],top_pick=None,main_wave_market=main_wave.market(rows,target))
        self.assertEqual(runner.checked_watchlists({day:record})[day],record)
        merged=runner.merge_watchlist_risks(record,record,dt.datetime.fromisoformat(day+'T10:00:00+08:00'))
        self.assertEqual(merged['items'],[]);self.assertIsNone(merged['top_pick'])
        changed=copy.deepcopy(record);changed['main_wave_market']['ma20']=999
        with self.assertRaises(ValueError):runner.checked_watchlists({day:changed})
        changed=copy.deepcopy(record);changed['items']=[{'symbol':'sh600001'}]
        with self.assertRaises(ValueError):runner.checked_watchlists({day:changed})
