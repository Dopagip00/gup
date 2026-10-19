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
                 site_ready='true', stage='', schedule='0 1 * * 1-5', deploy_result='success',
                 resolved_stage='publish',health_result='success'):
        expression=condition.replace('&&',' and ').replace('||',' or ')
        context={
            'vars':SimpleNamespace(ENABLE_SCHEDULED_RESEARCH=research_flag, ENABLE_PAGES_DEPLOY=deploy_flag),
            'github':SimpleNamespace(repository=repository, event_name=event, ref=ref,
                event=SimpleNamespace(repository=SimpleNamespace(default_branch=default_branch),
                    inputs=SimpleNamespace(stage=stage), schedule=schedule)),
            'needs':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                fundamentals=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                deploy=SimpleNamespace(result=deploy_result),health=SimpleNamespace(result=health_result)),
            'steps':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                stage=SimpleNamespace(outputs=SimpleNamespace(stage=resolved_stage))),
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

    def test_targets_and_bounded_pre_market_recovery_schedules(self):
        self.assertEqual(re.findall(r"(?m)^    - cron: '([^']+)'$",WORKFLOW),
            ['0 0 * * 1-5','17 0 * * 1-5','43 0 * * 1-5',
             '0 1 * * 1-5','7 1 * * 1-5','17 1 * * 1-5'])
        # All fallback clocks remain before the engine's 09:30 new-buy cutoff.
        for minute,hour in re.findall(r"(?m)^    - cron: '(\d+) (\d+) \* \* 1-5'$",WORKFLOW):
            beijing_minutes=(int(hour)+8)*60+int(minute)
            self.assertGreaterEqual(beijing_minutes,8*60)
            self.assertLess(beijing_minutes,9*60+30)

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

    def test_schedules_are_explicitly_mapped_without_unknown_schedule_fallback(self):
        resolver=self.job('research').split('      - name: Resolve the authorized research stage explicitly',1)[1]
        resolver=resolver.split('      - name:',1)[0]
        mapping={}
        for choices,stage in re.findall(r"(?m)^\s+((?:'[^']+'\|?)+)\) stage=(collect|publish) ;;$",resolver):
            for schedule in re.findall(r"'([^']+)'",choices):
                self.assertNotIn(schedule,mapping)
                mapping[schedule]=stage
        configured=re.findall(r"(?m)^    - cron: '([^']+)'$",WORKFLOW)
        self.assertEqual(set(mapping),set(configured))
        self.assertEqual([schedule for schedule in configured if mapping[schedule]=='collect'],
            ['0 0 * * 1-5','17 0 * * 1-5','43 0 * * 1-5'])
        self.assertEqual([schedule for schedule in configured if mapping[schedule]=='publish'],
            ['0 1 * * 1-5','7 1 * * 1-5','17 1 * * 1-5'])
        self.assertIn("*) echo '::error::Unrecognized research schedule'; exit 1",resolver)
        self.assertIn('collect|publish|fundamentals) stage="$REQUESTED_STAGE"',resolver)
        self.assertIn('push) stage=publish',resolver)
        self.assertIn("*) echo '::error::Unrecognized research event'; exit 1",resolver)

    def test_publish_retries_preserve_archive_and_do_not_queue_long_financial_jobs(self):
        research=self.job('research')
        stage=re.search(r'(?m)^          RESEARCH_STAGE: \$\{\{ (.+) \}\}$',research).group(1)
        self.assertEqual(self.evaluate(stage,resolved_stage='publish'),'publish')
        self.assertEqual(self.evaluate(stage,resolved_stage='collect'),'collect')
        self.assertEqual(self.evaluate(stage,resolved_stage='fundamentals'),'fundamentals')
        advice=research.split('      - name: Commit changed public advice archive',1)[1]
        condition=re.search(r'(?m)^        if: (.+)$',advice).group(1)
        self.assertTrue(self.evaluate(condition,event='push',schedule=''))
        self.assertFalse(self.evaluate(condition,resolved_stage='collect'))
        self.assertFalse(self.evaluate(condition,resolved_stage='fundamentals'))
        for schedule in ('0 1 * * 1-5','7 1 * * 1-5','17 1 * * 1-5'):
            self.assertTrue(self.evaluate(condition,schedule=schedule))
            self.assertEqual(self.enabled('fundamentals',schedule=schedule),schedule=='17 1 * * 1-5')
        self.assertFalse(self.enabled('fundamentals',event='push',schedule=''))
        self.assertFalse(self.enabled('fundamentals',event='push',schedule='',deploy_result='failure'))
        self.assertFalse(self.enabled('fundamentals',schedule='17 1 * * 1-5',deploy_result='failure'))

    def test_cache_storage_is_bounded_and_public_failure_is_visible(self):
        self.assertIn('concurrency:\n  group: huyan-research-state\n  cancel-in-progress: false',WORKFLOW)
        # Collect, publish and financial artifacts remain serialized; a later
        # financial completion must not race a fresh morning snapshot.
        for job in ('research','fundamentals'):
            text=self.job(job)
            upload=text.split('uses: actions/upload-artifact@v4',1)[1]
            cache_step=upload.split('      - name:',1)[0]
            self.assertRegex(cache_step,r'(?m)^          retention-days: 1$')
        summary=self.job('cloud-status')
        self.assertIn('if: always()',summary)
        self.assertIn('needs: [research, deploy, health, fundamentals, deploy-longterm]',summary)
        self.assertIn("['failure', 'cancelled'].includes(result)",summary)
        self.assertIn('core.setFailed(',summary)
        self.assertIn('GitHub schedules can queue or be delayed.',summary)

    def test_public_site_check_finishes_before_long_financial_work_and_failure_stops_it(self):
        health=self.job('health')
        self.assertIn('needs: [research, deploy]',health)
        self.assertNotIn('needs: fundamentals',health)
        self.assertIn('python cloud_health.py --require-morning',health)
        self.assertFalse(self.enabled('fundamentals',schedule='17 1 * * 1-5',health_result='failure'))

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
