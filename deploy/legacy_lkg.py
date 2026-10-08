"""One strictly pinned V1.4 baseline adapter; never guess a legacy healthy HEAD."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
from deploy_helpers import DeployError
import release_preflight as pref
import release_state as store


def shared_runner(tools):
    sys.path.insert(0,str(pref.shared_scripts(tools)))
    import release_checks
    return release_checks


class LegacyVerification(pref.Preflight):
    def source_check(self):
        # This exception is only for the independently reviewed immutable V1.4.
        if self.old!=pref.BASELINE_SHA or self.target!=pref.BASELINE_SHA:
            raise DeployError('Legacy initialization supports only pinned reviewed V1.4 baseline')
        if Path(self.git('rev-parse','--show-toplevel')).resolve()!=self.project:
            raise DeployError('Legacy source is not the repository root')
        if self.git('rev-parse','HEAD')!=pref.BASELINE_SHA or self.git('status','--porcelain','--untracked-files=all'):
            raise DeployError('Legacy baseline checkout must be exact and clean')
        store.repository_id(self.project)
        self.git('fsck','--no-reflogs','--full',pref.BASELINE_SHA)
        # The SHA pins every tracked source/catalog/test; no missing old runner is invoked.
        self.report['kind']='PINNED_LEGACY_BASELINE'

    def network_guard(self,checkout):
        return shared_runner(self.tools).NETWORK_GUARD

    def release_checks(self, checkout, python):
        runner=shared_runner(self.tools)
        test_checkout=self.checkout(self.temp/'legacy-tests',pref.BASELINE_SHA)
        env=pref.safe_environment(self.temp,network=self.network,project=test_checkout)
        env['RELEASE_TEST_ISOLATED_TOPICS']='1'
        checks=dict.fromkeys(runner.CHECK_NAMES,'NOT RUN')
        checks['release guard']='OK'  # Strict immutable SHA + clean Git/fsck above, no C runner at old SHA.
        commands={
            'git diff --check':[['git','-C',str(test_checkout),'diff','--check',pref.BASELINE_SHA,'HEAD']],
            'deploy shell syntax':[['bash','-n',str(test_checkout/path)] for path in self.git('ls-tree','-r','--name-only',pref.BASELINE_SHA).splitlines() if path.startswith('deploy/') and path.endswith('.sh')],
            'compileall':[[python,'-m','compileall','-q','bot.py','app']],
            'check_bot (disposable DB)':[[python,'-c',runner.DB_CHECK],[python,'check_bot.py']],
            'hero abilities':[[python,'-m','app.mini.combat.hero_abilities.validate']],
            'boss abilities':[[python,'-m','app.mini.boss.boss_abilities.validate']],
            'tower':[[python,'-m','app.mini.tower.validate']],
            'JSON and content validators':[[python,'-c',runner.CONTENT_CHECK]],
        }
        for name,items in commands.items():
            if not items:raise DeployError('Empty legacy check')
            for command in items:self.run_command(command,cwd=test_checkout,env=env)
            checks[name]='OK'
        inventory=self.temp/'legacy-inventory.json'
        self.run_command([python,str(pref.shared_scripts(self.tools)/'test_inventory.py'),str(inventory)],cwd=test_checkout,env=env)
        discovered=json.loads(inventory.read_text(encoding='utf-8'))
        if not discovered:raise DeployError('Empty legacy discovery')
        # The checkout is the baseline itself; exact IDs/multiplicity are pinned.
        checks['unittest discovery preservation']='OK'
        output=self.run_command([python,'-m','unittest','discover','-s','tests','-q'],cwd=test_checkout,env=env)
        count=re.search(r'^Ran (\d+) tests? in ',output,re.M)
        if not count or int(count[1])!=len(discovered) or re.search(r'\b(?:skipped|expected failures|unexpected successes)=',output):
            raise DeployError('Legacy unittest count/skips do not prove a release')
        checks['full unittest']='OK'
        self.report.update(release_checks=checks,head_discovered_tests=len(discovered),baseline_discovered_tests=len(discovered),
                           tests=dict(count=len(discovered),failures=0,errors=0,skips=0,expected_failures=0,unexpected_successes=0))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project','db','old','target','evidence','workspace','python','bot-user','service','watcher','update-service'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    try:
        if os.name!='posix' or os.geteuid()!=0:raise DeployError('Root Linux required')
        pref.require_deployment_lock(args.db)
        def interrupted(*unused):raise pref.Interrupted('Legacy verification interrupted')
        signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
        op=LegacyVerification(project=args.project,db=args.db,old=args.old,target=args.target,evidence=args.evidence,
              workspace=args.workspace,tools=Path(__file__).parent,python=args.python,bot_user=args.bot_user,
              services=(args.service,args.watcher,args.update_service))
        op.perform()
        pref.validate_evidence(args.evidence,old=args.old,target=args.target,tools=Path(__file__).parent)
        print('Pinned baseline verification PASS; controlled startup/operator confirmation still required')
        return 0
    except Exception as error:
        print('ERROR: '+(str(error) if isinstance(error,DeployError) else type(error).__name__));return 1


if __name__=='__main__':raise SystemExit(main())
