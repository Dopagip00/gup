"""Evaluate the workflow's simple access condition without calling GitHub."""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


class WorkflowBranchTests(unittest.TestCase):
    def enabled(self, event, ref, flag='false', default_branch='main'):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/research.yml').read_text(encoding='utf-8')
        research=workflow.split('\n  research:\n',1)[1]
        condition=re.search(r'(?m)^    if: (.+)$',research).group(1)
        expression=condition.replace('&&',' and ').replace('||',' or ')
        context={
            'vars':SimpleNamespace(ENABLE_SCHEDULED_RESEARCH=flag),
            'github':SimpleNamespace(event_name=event,ref=ref,event=SimpleNamespace(repository=SimpleNamespace(default_branch=default_branch))),
            'format':lambda template,*values:template.format(*values),
        }
        return bool(eval(expression,{'__builtins__':{}},context))

    def test_default_branch_manual_run_remains_available_before_schedules_are_enabled(self):
        self.assertTrue(self.enabled('workflow_dispatch','refs/heads/main'))
        self.assertTrue(self.enabled('workflow_dispatch','refs/heads/release/research',default_branch='release/research'))

    def test_manual_feature_branch_or_tag_cannot_commit_advice_or_deploy_pages(self):
        for flag in ('true','false'):
            for ref in ('refs/heads/experimental-model','refs/tags/main','refs/pull/1/merge'):
                with self.subTest(flag=flag,ref=ref):
                    self.assertFalse(self.enabled('workflow_dispatch',ref,flag))

    def test_scheduled_runs_still_require_the_explicit_enable_flag_and_default_branch(self):
        self.assertTrue(self.enabled('schedule','refs/heads/main','true'))
        self.assertFalse(self.enabled('schedule','refs/heads/main','false'))
        self.assertFalse(self.enabled('schedule','refs/heads/experimental-model','true'))


if __name__=='__main__':unittest.main()
