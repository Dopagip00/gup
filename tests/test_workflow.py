"""Check workflow authorization and automatic triggers without calling GitHub."""
from fnmatch import fnmatchcase
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


WORKFLOW = (Path(__file__).resolve().parents[1]/'.github/workflows/research.yml').read_text(encoding='utf-8')


class WorkflowBranchTests(unittest.TestCase):
    def job(self, name):
        return re.search(r'(?ms)^  '+re.escape(name)+r':\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)', WORKFLOW).group(1)

    def evaluate(self, condition, *, event='schedule', ref='refs/heads/main', research_flag='',
                 deploy_flag='', repository='Dopagip00/gup', default_branch='main',
                 site_ready='true', stage='', schedule='0 1 * * 1-5', deploy_result='success'):
        expression=condition.replace('&&',' and ').replace('||',' or ')
        context={
            'vars':SimpleNamespace(ENABLE_SCHEDULED_RESEARCH=research_flag, ENABLE_PAGES_DEPLOY=deploy_flag),
            'github':SimpleNamespace(repository=repository, event_name=event, ref=ref,
                event=SimpleNamespace(repository=SimpleNamespace(default_branch=default_branch),
                    inputs=SimpleNamespace(stage=stage), schedule=schedule)),
            'needs':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                fundamentals=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                deploy=SimpleNamespace(result=deploy_result)),
            'steps':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready))),
            'format':lambda template,*values:template.format(*values),
        }
        return eval(expression,{'__builtins__':{}},context)

    def enabled(self, job='research', **context):
        condition=re.search(r'(?m)^    if: (.+)$',self.job(job)).group(1)
        return bool(self.evaluate(condition, **context))

    def test_authorized_repository_runs_without_initial_variables(self):
        for event in ('schedule','push','workflow_dispatch'):
            with self.subTest(event=event):
                self.assertTrue(self.enabled(event=event))
                self.assertTrue(self.enabled('deploy',event=event))
                self.assertTrue(self.enabled('deploy-longterm',event=event))

    def test_explicit_false_pauses_automatic_research_but_allows_manual_recovery(self):
        for event in ('schedule','push'):
            self.assertFalse(self.enabled(event=event,research_flag='false'))
        self.assertTrue(self.enabled(event='workflow_dispatch',research_flag='false'))
        for job in ('deploy','deploy-longterm'):
            for event in ('schedule','push','workflow_dispatch'):
                with self.subTest(job=job,event=event):
                    self.assertFalse(self.enabled(job,event=event,deploy_flag='false'))

    def test_other_repositories_and_nondefault_refs_cannot_research_or_publish(self):
        for job in ('research','deploy','deploy-longterm'):
            for event in ('schedule','push','workflow_dispatch'):
                for repository in ('another-owner/gup','Dopagip00/another-repository'):
                    with self.subTest(job=job,event=event,repository=repository):
                        self.assertFalse(self.enabled(job,event=event,repository=repository))
                for ref in ('refs/heads/experimental-model','refs/tags/main','refs/pull/1/merge'):
                    with self.subTest(job=job,event=event,ref=ref):
                        self.assertFalse(self.enabled(job,event=event,ref=ref))

    def test_current_default_branch_is_used_instead_of_hardcoding_main(self):
        for job in ('research','deploy','deploy-longterm'):
            self.assertTrue(self.enabled(job,ref='refs/heads/release/research',default_branch='release/research'))
            self.assertFalse(self.enabled(job,ref='refs/heads/main',default_branch='release/research'))

    def test_pages_requires_a_generated_site(self):
        for job in ('deploy','deploy-longterm'):
            self.assertFalse(self.enabled(job,site_ready='false'))
            self.assertTrue(self.enabled(job,deploy_flag='true'))

    def test_only_the_two_authorized_beijing_clocks_remain(self):
        self.assertEqual(re.findall(r"(?m)^    - cron: '([^']+)'$",WORKFLOW),
            ['0 0 * * 1-5','0 1 * * 1-5'])

    def test_source_push_triggers_but_advice_archive_commits_do_not(self):
        push=WORKFLOW.split('\n  push:\n',1)[1].split('\n  schedule:\n',1)[0]
        self.assertIn("branches: ['**']",push)
        patterns=re.findall(r"(?m)^      - '([^']+)'$",push)
        for source in ('.github/workflows/research.yml','runner.py','pages.js','engine/core.py',
                       'engine/static/app.js','seed/research.json.gz.part0000','tests/test_workflow.py'):
            with self.subTest(source=source):
                self.assertTrue(any(fnmatchcase(source,pattern) for pattern in patterns))
        for unrelated in ('research/daily-plans.json','README.md','DEPLOYMENT_STATUS.md'):
            with self.subTest(unrelated=unrelated):
                self.assertFalse(any(fnmatchcase(unrelated,pattern) for pattern in patterns))

    def test_source_push_publishes_and_preserves_advice_and_longterm_chain(self):
        research=self.job('research')
        stage=re.search(r'(?m)^          RESEARCH_STAGE: \$\{\{ (.+) \}\}$',research).group(1)
        # Push uses the real-time publisher, with the engine's existing time gates.
        self.assertEqual(self.evaluate(stage,event='push',schedule=''),'publish')
        self.assertEqual(self.evaluate(stage,schedule='0 0 * * 1-5'),'collect')
        self.assertEqual(self.evaluate(stage,event='workflow_dispatch',stage='fundamentals',schedule=''),'fundamentals')
        advice=research.split('      - name: Commit changed public advice archive',1)[1]
        condition=re.search(r'(?m)^        if: (.+)$',advice).group(1)
        self.assertTrue(self.evaluate(condition,event='push',schedule=''))
        self.assertFalse(self.evaluate(condition,event='schedule',schedule='0 0 * * 1-5'))
        self.assertTrue(self.enabled('fundamentals',event='push',schedule=''))
        self.assertFalse(self.enabled('fundamentals',event='push',schedule='',deploy_result='failure'))

    def test_hidden_research_artifacts_upload_only_the_explicit_public_whitelist(self):
        expected={
            'huyan-research-cache':['.cache/export/research.sqlite3','.cache/export/cache-meta.json'],
            'huyan-daily-plans':['.cache/plans-export/daily-plans.json'],
            'huyan-research-cache-financial':['.cache/financial-export/research.sqlite3','.cache/financial-export/cache-meta.json'],
            'huyan-daily-plans-financial':['.cache/financial-plans-export/daily-plans.json'],
        }
        uploads={}
        for step in re.findall(r'(?ms)^      - name: [^\n]+\n(.*?)(?=^      - name:|^  [a-z]|\Z)',WORKFLOW):
            if 'uses: actions/upload-artifact@v4' not in step:
                continue
            name=re.search(r'(?m)^          name: (.+)$',step).group(1)
            with self.subTest(artifact=name):
                # Files beneath .cache are hidden to upload-artifact by default.
                self.assertRegex(step,r'(?m)^          include-hidden-files: true$')
                path=re.search(r'(?m)^          path: (.+)$',step).group(1)
                uploads[name]=re.findall(r'(?m)^            (\S+)$',step) if path=='|' else [path]
        # Exact files prevent the hidden-file opt-in from exposing all of .cache.
        self.assertEqual(uploads,expected)


if __name__=='__main__':unittest.main()
