"""Check workflow authorization and automatic triggers without calling GitHub."""
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
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

    def test_queued_source_checkout_reads_current_business_archives_before_restore(self):
        research=self.job('research')
        read=research.index('Read latest default-branch business archives before restoring state')
        restore=research.index("Restore the previous stage's public research state")
        run=research.index('Collect or publish using actual Beijing time')
        self.assertLess(read,restore);self.assertLess(restore,run)
        self.assertIn('PLANS_BASE_SHA: ${{ steps.archives.outputs.plans_base_sha }}',research)
        self.assertIn('WATCHLISTS_BASE_SHA: ${{ steps.archives.outputs.watchlists_base_sha }}',research)
        locator=research.split('      - name: Locate the latest successful persistent research artifact',1)[1].split('      - name:',1)[0]
        self.assertNotIn('readFileSync',locator)
        self.assertNotIn('base_sha',locator)
        self.assertIn("['huyan-research-cache','huyan-research-cache-financial']",locator)
        self.assertIn('!a.expired',locator)

    def run_archive_step(self,scenario):
        step=self.job('research').split('      - name: Read latest default-branch business archives before restoring state',1)[1].split('      - name:',1)[0]
        script=textwrap.dedent(step.split('          script: |\n',1)[1])
        harness=r'''
        const actualRequire=require,crypto=require('node:crypto');
        const scenario=SCENARIO,files={'research/daily-plans.json':'old pinned plans','research/daily-watchlists.json':'old pinned watchlist'};
        const outputs={},requests=[],blobs={};
        function file(path,text) {
          const bytes=Buffer.from(text),sha=crypto.createHash('sha1').update(Buffer.from('blob '+bytes.length+'\0')).update(bytes).digest('hex');
          return {type:'file',path,size:bytes.length,sha,encoding:'base64',content:bytes.toString('base64')};
        }
        const current={
          'research/daily-plans.json':file('research/daily-plans.json','{"plans":{"21":"current preserved advice"}}'),
          'research/daily-watchlists.json':file('research/daily-watchlists.json','{"watchlists":{"21":"current reviewed observation"}}')
        };
        const expected=Object.fromEntries(Object.entries(current).map(([path,data])=>[path,{sha:data.sha,text:Buffer.from(data.content,'base64').toString()}]));
        const fs={mkdirSync(){},writeFileSync(path,bytes){files[path]=bytes.toString();},rmSync(path){delete files[path];},
          readFileSync(){throw Error('must not trust pinned checkout bytes');},existsSync(){throw Error('must not inspect pinned archive lease');}};
        const github={rest:{repos:{async getContent(args){
          requests.push(args);
          if(scenario==='404')throw Object.assign(Error('not found'),{status:404});
          if(scenario==='403')throw Object.assign(Error('forbidden'),{status:403});
          let data={...current[args.path]};
          if(scenario==='sha')data.sha='0'.repeat(40);
          if(scenario==='base64')data.content='%%%';
          if(scenario==='size')data.size=64*1024*1024+1;
          if(scenario==='path')data.path='research/unexpected.json';
          if(scenario==='type')data.type='dir';
          if(scenario==='blob'){
            blobs[data.sha]={...data};data.encoding='none';data.content='';
          }
          return {data};
        }},git:{async getBlob(args){requests.push(args);return {data:blobs[args.file_sha]};}}}};
        const context={repo:{owner:'Dopagip00',repo:'gup'},payload:{repository:{default_branch:'release/research'}}};
        const core={setOutput(key,value){outputs[key]=value;}};
        (async()=>{
          let error=null;
          try{await new Function('github','context','core','require','return (async()=>{\n'+SCRIPT+'\n})();')(
            github,context,core,name=>name==='node:fs'?fs:actualRequire(name));}
          catch(exc){error=exc.message;}
          process.stdout.write(JSON.stringify({error,files,outputs,requests,expected}));
        })().catch(error=>{console.error(error);process.exitCode=1;});
        '''.replace('SCENARIO',json.dumps(scenario)).replace('SCRIPT',json.dumps(script))
        result=subprocess.run(['node','-e',harness],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which('node'),'Node executes the workflow GitHub script fixture')
    def test_current_branch_archives_replace_queued_pinned_bytes_and_capture_real_leases(self):
        for scenario in ('normal','blob'):
            with self.subTest(scenario=scenario):
                result=self.run_archive_step(scenario)
                self.assertIsNone(result['error'])
                for path,expected in result['expected'].items():
                    self.assertEqual(result['files'][path],expected['text'])
                    key='plans_base_sha' if path.endswith('daily-plans.json') else 'watchlists_base_sha'
                    self.assertEqual(result['outputs'][key],expected['sha'])
                for request in result['requests']:
                    if 'path' in request:self.assertEqual(request['ref'],'release/research')

    @unittest.skipUnless(shutil.which('node'),'Node executes the workflow GitHub script fixture')
    def test_only_real_404_removes_stale_checkout_archive_and_sets_missing_lease(self):
        missing=self.run_archive_step('404')
        self.assertIsNone(missing['error']);self.assertEqual(missing['files'],{})
        self.assertEqual(missing['outputs'],{'plans_base_sha':'missing','watchlists_base_sha':'missing'})
        forbidden=self.run_archive_step('403')
        self.assertEqual(forbidden['error'],'forbidden');self.assertEqual(forbidden['outputs'],{})
        self.assertEqual(forbidden['files']['research/daily-plans.json'],'old pinned plans')

    @unittest.skipUnless(shutil.which('node'),'Node executes the workflow GitHub script fixture')
    def test_malformed_current_archive_never_claims_old_checkout_is_current(self):
        for scenario in ('sha','base64','size','path','type'):
            with self.subTest(scenario=scenario):
                result=self.run_archive_step(scenario)
                self.assertIsNotNone(result['error']);self.assertEqual(result['outputs'],{})
                self.assertEqual(result['files']['research/daily-plans.json'],'old pinned plans')

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
