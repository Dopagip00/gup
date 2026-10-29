import copy
import datetime as dt
import unittest
import intraday_selection as s


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,29,9,45,tzinfo=s.TZ)
        self.symbols=['sh600001','sh600002','sh600003']

    def evidence(self,symbol,now=None):
        stamp=(now or self.now).isoformat()
        return {'protocol':'intraday-confirmation-evidence-v1','symbol':symbol,
                'cohort_id':'frozen','confirmed_at':stamp,'observed_at':stamp,
                'checks':{name:True for name in s.CHECKS}}

    def run_selection(self,previous=None,**changes):
        args=dict(now=self.now,cohort_id='frozen',symbols=self.symbols,
                  assessments={sym:self.evidence(sym) for sym in self.symbols},
                  vetoes={},ready=True)
        args.update(changes)
        return s.advance(previous,**args)

    def test_lower_rank_can_confirm_first_and_original_order_breaks_ties(self):
        result=self.run_selection(assessments={sym:self.evidence(sym) for sym in self.symbols[1:]})
        self.assertEqual(result['selected']['symbol'],'sh600002')
        self.assertEqual(result['selected']['rank'],2)
        self.assertFalse(result['eligible']);self.assertFalse(result['actionable'])
        self.assertIsNone(result['published_at']);self.assertEqual(result['valid_evaluation_samples'],0)

    def test_later_better_rank_cannot_replace_and_inputs_not_mutated(self):
        first=self.run_selection(assessments={'sh600002':self.evidence('sh600002')})
        saved=copy.deepcopy(first)
        later=self.run_selection(first,now=self.now+dt.timedelta(minutes=5))
        self.assertEqual(later['selected'],saved['selected']);self.assertEqual(first,saved)

    def test_withdrawal_is_permanent_even_when_risk_disappears(self):
        first=self.run_selection()
        revoked=self.run_selection(first,now=self.now+dt.timedelta(minutes=1),vetoes={'sh600001':'new risk'})
        after=self.run_selection(revoked,now=self.now+dt.timedelta(minutes=2))
        self.assertEqual(after['status'],'revoked');self.assertEqual(after['revocation'],revoked['revocation'])
        self.assertEqual(after['selected'],first['selected'])

    def test_late_risk_still_revokes_after_selection_window(self):
        first=self.run_selection()
        result=self.run_selection(first,now=self.now.replace(hour=14),vetoes={'sh600001':'risk'})
        self.assertEqual(result['status'],'revoked')

    def test_no_new_selection_before_or_after_window(self):
        self.assertIsNone(self.run_selection(now=self.now.replace(minute=44))['selected'])
        result=self.run_selection(now=self.now.replace(hour=10,minute=0,second=1))
        self.assertEqual(result['status'],'expired');self.assertIsNone(result['selected'])

    def test_every_required_check_is_mandatory(self):
        for key in s.CHECKS:
            evidence=self.evidence('sh600001');del evidence['checks'][key]
            self.assertIsNone(self.run_selection(assessments={'sh600001':evidence})['selected'])

    def test_stale_future_and_other_cohort_evidence_are_rejected(self):
        for mutation in ({'confirmed_at':(self.now+dt.timedelta(seconds=1)).isoformat()},
                         {'observed_at':(self.now+dt.timedelta(seconds=1)).isoformat()},
                         {'cohort_id':'different'}):
            evidence=self.evidence('sh600001');evidence.update(mutation)
            self.assertIsNone(self.run_selection(assessments={'sh600001':evidence})['selected'])
        self.assertIsNone(self.run_selection(now=self.now+dt.timedelta(seconds=91))['selected'])

    def test_missing_data_suspends_without_forgetting_choice(self):
        first=self.run_selection()
        paused=self.run_selection(first,ready=False,assessments={})
        self.assertEqual(paused['status'],'suspended');self.assertEqual(paused['selected'],first['selected'])
        self.assertEqual(self.run_selection(paused)['selected'],first['selected'])

    def test_known_veto_and_outside_cohort_never_selected(self):
        self.assertEqual(self.run_selection(vetoes={'sh600001':'risk'})['selected']['rank'],2)
        self.assertIsNone(self.run_selection(assessments={'sh600004':self.evidence('sh600004')})['selected'])

    def test_journal_restores_selection_and_rejects_replacement_or_erasure(self):
        first=self.run_selection()
        revoked=self.run_selection(first,now=self.now+dt.timedelta(minutes=1),vetoes={'sh600001':'risk'})
        self.assertEqual(s.restore([revoked,first],first['date'],self.now+dt.timedelta(minutes=2)),revoked)
        replaced=copy.deepcopy(revoked);replaced['selected']['symbol']='sh600002'
        with self.assertRaises(ValueError):s.restore([first,replaced],first['date'],self.now+dt.timedelta(minutes=2))
        erased=copy.deepcopy(revoked);erased['checked_at']=(self.now+dt.timedelta(minutes=2)).isoformat();erased['revocation']=None
        with self.assertRaises(ValueError):s.restore([revoked,erased],first['date'],self.now+dt.timedelta(minutes=2))

    def test_previous_day_and_backward_clock_cannot_reset_lock(self):
        first=self.run_selection()
        for now in (self.now-dt.timedelta(seconds=1),self.now+dt.timedelta(days=1)):
            with self.assertRaises(ValueError):self.run_selection(first,now=now)


if __name__=='__main__':unittest.main()
