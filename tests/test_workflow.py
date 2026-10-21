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
                 resolved_stage='publish',health_result='success',data_ready='true'):
        expression=condition.replace('&&',' and ').replace('||',' or ')
        context={
            'vars':SimpleNamespace(ENABLE_SCHEDULED_RESEARCH=research_flag, ENABLE_PAGES_DEPLOY=deploy_flag),
            'github':SimpleNamespace(repository=repository, event_name=event, ref=ref,
                event=SimpleNamespace(repository=SimpleNamespace(default_branch=default_branch),
                    inputs=SimpleNamespace(stage=stage), schedule=schedule)),
            'needs':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                fundamentals=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                deploy=SimpleNamespace(result=deploy_result),health=SimpleNamespace(result=health_result,outputs=SimpleNamespace(data_ready=data_ready))),
            'steps':SimpleNamespace(research=SimpleNamespace(outputs=SimpleNamespace(site_ready=site_ready)),
                stage=SimpleNamespace(outputs=SimpleNamespace(stage=resolved_stage))),
            'format':lambda template,*values:template.format(*values),
            'always':lambda:True,
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

    def test_independent_official_source_is_current_run_only_and_keeps_authorization(self):
        source=self.job('official-universe');research=self.job('research')
        self.assertIn('runs-on: windows-latest',source)
        self.assertIn('python universe_receipt.py collect --output-dir .cache/universe',source)
        self.assertIn("if: steps.collect.outputs.receipt_ready == 'true'",source)
        self.assertIn('needs: official-universe',research)
        self.assertIn('--universe-dir .cache/universe',research)
        for event in ('schedule','push','workflow_dispatch'):
            self.assertTrue(self.enabled('official-universe',event=event))
            self.assertFalse(self.enabled('official-universe',event=event,repository='other/gup'))
            self.assertFalse(self.enabled('official-universe',event=event,ref='refs/heads/test'))

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
            ['50 23 * * 0-4','0 0 * * 1-5','20 0 * * 1-5','50 0 * * 1-5',
             '3 1 * * 1-5','11 1 * * 1-5','19 1 * * 1-5',
             '20 8 * * 1-5','10 9 * * 1-5'])
        # All fallback clocks remain before the engine's 09:30 new-buy cutoff.
        for minute,hour in re.findall(r"(?m)^    - cron: '(\d+) (\d+) \* \* 1-5'$",WORKFLOW):
            beijing_minutes=(int(hour)+8)*60+int(minute)
            if int(hour)>=8:
                self.assertIn(beijing_minutes,(16*60+20,17*60+10))
                continue
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
        for unrelated in ('research/daily-plans.json','research/daily-watchlists.json','README.md','DEPLOYMENT_STATUS.md'):
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
            ['0 0 * * 1-5','20 0 * * 1-5','50 0 * * 1-5','20 8 * * 1-5','10 9 * * 1-5'])
        self.assertEqual([schedule for schedule in configured if mapping[schedule]=='publish'],
            ['50 23 * * 0-4','3 1 * * 1-5','11 1 * * 1-5','19 1 * * 1-5'])
        self.assertIn("*) echo '::error::Unrecognized research schedule'; exit 1",resolver)
        self.assertIn('collect|publish|fundamentals|recover) stage="$REQUESTED_STAGE"',resolver)
        self.assertIn('push) stage=publish',resolver)
        self.assertIn("*) echo '::error::Unrecognized research event'; exit 1",resolver)

    def test_prepared_observation_archives_commit_after_all_stages_without_source_loops(self):
        research=self.job('research')
        step=research.split('      - name: Commit independent observation snapshots without rewriting published rankings',1)[1].split('      - name:',1)[0]
        condition=re.search(r'(?m)^        if: (.+)$',step).group(1)
        for stage in ('collect','publish','fundamentals'):
            self.assertTrue(self.evaluate(condition,resolved_stage=stage))
        self.assertFalse(self.evaluate(condition,site_ready='false'))
        self.assertIn('WATCHLISTS_BASE_SHA',step)
        self.assertIn("research/daily-watchlists.json",step)
        self.assertIn("Observation archive changed remotely; refusing replacement.",step)

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
        for schedule in ('3 1 * * 1-5','11 1 * * 1-5','19 1 * * 1-5'):
            self.assertTrue(self.evaluate(condition,schedule=schedule))
            self.assertEqual(self.enabled('fundamentals',schedule=schedule),schedule=='19 1 * * 1-5')
        self.assertFalse(self.enabled('fundamentals',event='push',schedule=''))
        self.assertFalse(self.enabled('fundamentals',event='push',schedule='',deploy_result='failure'))
        self.assertFalse(self.enabled('fundamentals',schedule='19 1 * * 1-5',deploy_result='failure'))
        for schedule in ('19 1 * * 1-5','20 8 * * 1-5','10 9 * * 1-5'):
            self.assertTrue(self.enabled('fundamentals',schedule=schedule,health_result='failure',data_ready='true'))
            self.assertFalse(self.enabled('fundamentals',schedule=schedule,data_ready='false'))

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
        self.assertIn('run: python watchlist_health.py',health)
        diagnostic=health.split('      - name: Diagnose formal morning qualification',1)[1]
        self.assertIn('continue-on-error: true',diagnostic)
        self.assertFalse(self.enabled('fundamentals',schedule='19 1 * * 1-5',health_result='failure',data_ready='false'))
        self.assertIn("data_ready: ${{ steps.snapshot.outcome == 'success' && 'true' || 'false' }}",health)

    def test_financial_preparation_uses_the_latest_archive_and_a_lease(self):
        finance=self.job('fundamentals')
        read=finance.index('Read the latest observation archive after the research stage commit')
        run=finance.index('Update long-term fundamentals without publishing a new daily plan')
        commit=finance.index("Commit the financial stage's prepared observation archive safely")
        self.assertLess(read,run);self.assertLess(run,commit)
        self.assertIn("core.setOutput('base_sha',current.sha)",finance)
        self.assertIn("WATCHLISTS_BASE_SHA: ${{ steps.financial-watchlists.outputs.base_sha }}",finance)
        self.assertIn("Financial observation archive changed remotely; refusing replacement.",finance)
        self.assertIn('contents: write',finance)

    def test_hidden_research_artifacts_upload_only_the_explicit_public_whitelist(self):
        expected={
            'huyan-research-cache':['.cache/export/research.sqlite3','.cache/export/cache-meta.json'],
            'huyan-daily-plans':['.cache/plans-export/daily-plans.json'],
            'huyan-research-cache-financial':['.cache/financial-export/research.sqlite3','.cache/financial-export/cache-meta.json'],
            'huyan-daily-plans-financial':['.cache/financial-plans-export/daily-plans.json'],
            'huyan-current-official-universe':['.cache/universe/*.json'],
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
