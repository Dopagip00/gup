import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
import ai_review as ai

NOW=dt.datetime(2026,10,19,9,tzinfo=ai.TZ)

def record():
    return {'protocol':ai.PROTOCOL,'date':'2026-10-19','signal_date':'2026-10-16','status':'complete',
        'started_at':'2026-10-19T08:25:00+08:00','completed_at':'2026-10-19T08:28:00+08:00',
        'reviewed_symbols':['sh600298'],'risk_vetoes':[],
        'sources':[{'id':'sse-1','url':'https://www.sse.com.cn/disclosure/example.pdf',
            'published_date':'2026-10-16','observed_at':'2026-10-19T08:26:00+08:00','scope':'official_directory_title_only'}],
        'summary':'公开来源风险复核，无收益预测。','scope':'仅核验官方公告目录标题，未读取PDF正文。',
        'run_url':'https://chatgpt.com/c/public-task-identifier','run_url_scope':'task_chat'}

class AIReviewTests(unittest.TestCase):
    def check(self,value,**kwargs):return ai.validate_review(value,now=kwargs.get('now',NOW),signal_date='2026-10-16')
    def test_complete_public_record_and_empty_candidate_review(self):
        original=record();before=copy.deepcopy(original)
        result=self.check(original)
        self.assertTrue(result['valid']);self.assertTrue(result['morning_qualified']);self.assertEqual(original,before)
        original.update(reviewed_symbols=[],sources=[])
        self.assertTrue(self.check(original)['morning_qualified'])
    def test_negative_veto_requires_reviewed_symbol_and_valid_source_reference(self):
        original=record();original['risk_vetoes']=[{'symbol':'sh600298','reason':'待人工核验风险提示','source_ids':['sse-1']}]
        self.assertTrue(self.check(original)['valid'])
        for changes in ({'symbol':'sh600241'},{'source_ids':['missing']},{'source_ids':[]},{'boost':1}):
            value=copy.deepcopy(original);value['risk_vetoes'][0].update(changes)
            self.assertFalse(self.check(value)['valid'])
    def test_missing_extra_and_oversized_fields_fail_closed(self):
        for mutate in (lambda r:r.pop('protocol'),lambda r:r.update(account='private'),
                       lambda r:r.update(summary='x'*(ai.MAX_BYTES+1)),
                       lambda r:r.update(reviewed_symbols=['sh600298']*2401)):
            value=record();mutate(value);self.assertFalse(self.check(value)['valid'])
    def test_old_wrong_signal_and_future_timestamps_fail_closed(self):
        for changes in ({'date':'2026-10-16'},{'signal_date':'2026-10-15'},
                        {'completed_at':'2026-10-19T09:01:00+08:00'},
                        {'started_at':'2026-10-19T08:25:00'},
                        {'started_at':'2026-10-19T08:29:00+08:00'}):
            self.assertFalse(self.check(dict(record(),**changes))['valid'])
    def test_only_real_premarket_complete_can_qualify(self):
        late=record();late.update(started_at='2026-10-19T15:00:00+08:00',completed_at='2026-10-19T15:01:00+08:00',
            reviewed_symbols=[],sources=[])
        current=NOW.replace(hour=16)
        self.assertFalse(self.check(late,now=current)['valid'])
        late['status']='late';result=self.check(late,now=current)
        self.assertTrue(result['valid']);self.assertFalse(result['morning_qualified'])
        for status in ('partial','unavailable'):
            result=self.check(dict(record(),status=status));self.assertTrue(result['valid']);self.assertFalse(result['morning_qualified'])
    def test_source_origin_time_and_scope_are_not_ai_assertions_of_pdf_facts(self):
        for changes in ({'url':'https://example.com/a'},{'url':'http://www.sse.com.cn/a'},
                        {'url':'https://user:password@www.sse.com.cn/a'},
                        {'url':'https://www.sse.com.cn/a?token=secret'},
                        {'published_date':'2026-10-20'},{'observed_at':'2026-10-19T08:29:00+08:00'},
                        {'scope':'pdf_body_verified'}):
            value=record();value['sources'][0].update(changes);self.assertFalse(self.check(value)['valid'])
    def test_run_reference_must_be_chatgpt_task_or_chat_scope(self):
        for changes in ({'run_url':'https://evil.test/c/x'},{'run_url_scope':'unknown'},
                        {'run_url':'https://chatgpt.com/'},{'run_url':'https://chatgpt.com/c/x?token=secret'}):
            self.assertFalse(self.check(dict(record(),**changes))['valid'])
    def test_reader_uses_only_today_fixed_path_and_limits_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'research/ai-reviews';folder.mkdir(parents=True)
            (folder/'2026-10-18.json').write_text(json.dumps(record()),encoding='utf-8')
            self.assertEqual(ai.load_review(root,now=NOW,signal_date='2026-10-16')['status'],'missing')
            target=folder/'2026-10-19.json';target.write_text(json.dumps(record()),encoding='utf-8')
            self.assertTrue(ai.load_review(root,now=NOW,signal_date='2026-10-16')['morning_qualified'])
            target.write_bytes(b'x'*(ai.MAX_BYTES+1))
            self.assertEqual(ai.load_review(root,now=NOW,signal_date='2026-10-16')['status'],'invalid')
            target.write_text('{"value":NaN}',encoding='utf-8')
            self.assertEqual(ai.load_review(root,now=NOW,signal_date='2026-10-16')['status'],'invalid')
    def test_public_status_is_small_and_does_not_copy_records(self):
        result=ai.public_status(self.check(record()))
        self.assertEqual(result['reviewed_count'],1);self.assertEqual(result['veto_count'],0)
        self.assertNotIn('review',result);self.assertNotIn('sources',result)

if __name__=='__main__':unittest.main()
