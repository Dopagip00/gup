import datetime as dt
import unittest
from unittest.mock import patch
import agent_runner,core
class ManualRunnerTests(unittest.TestCase):
    def test_old_stages_are_rejected(self):
        for stage in ('07:00','09:30','11:00','13:00','14:10','14:50'):
            with self.assertRaises(ValueError):agent_runner.prepare(stage)
    def test_no_broker_action_is_permitted(self):
        with self.assertRaises(ValueError):agent_runner.record_result('09:00',{'external_orders':[{'symbol':'sh600298'}]})
    def test_not_before_nine(self):
        moment=dt.datetime(2026,10,19,8,59,tzinfo=core.TZ)
        self.assertEqual(agent_runner.prepare('09:00',moment)['status'],'skipped')
    def test_weekend_skip(self):
        moment=dt.datetime(2026,10,18,9,tzinfo=core.TZ)
        self.assertEqual(agent_runner.prepare('09:00',moment)['status'],'skipped')
    def test_manual_only(self):
        moment=dt.datetime(2026,10,19,9,tzinfo=core.TZ)
        record=agent_runner.prepare('09:00',moment)
        self.assertEqual(record['mode'],'manual_research');self.assertEqual(record['external_orders'],[])
