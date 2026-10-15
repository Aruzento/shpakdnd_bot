"""Real files/Git with fake systemd; production services are never addressed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.v1_4_1.test_preflight import ROOT,pref
from tests.v1_3_3.test_deploy_shell import BASH,shell_path
import release_state as store
import systemd_state as units
from deploy_helpers import DeployError


class InstalledSystemdTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.root.chmod(0o700)
        self.source=self.root/'reviewed';self.source.mkdir();(self.source/'deploy').mkdir();(self.source/'scripts').mkdir()
        for name in set(units.SOURCE_NAMES+tuple('deploy/'+n for n in pref.BUNDLE)+tuple('scripts/'+n for n in pref.SHARED_SCRIPTS)):
            shutil.copy2(ROOT/name,self.source/name)
        for args in [('init','-q'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),
                     ('config','core.autocrlf','false'),('config','commit.gpgsign','false'),
                     ('remote','add','origin','https://github.com/Aruzento/shpakdnd_bot.git'),('add','.'),('commit','-qm','Reviewed fixtures')]:
            subprocess.run(['git','-C',str(self.source),*args],check=True,capture_output=True)
        self.sha=subprocess.check_output(['git','-C',str(self.source),'rev-parse','HEAD'],text=True).strip()
        self.project=self.root/'live';self.project.mkdir()
        self.unit_dir=self.root/'units';self.unit_dir.mkdir()
        self.entry=self.root/'bin';self.entry.mkdir();self.alias=self.entry/'update-shpakdnd-bot.sh'
        self.alias.write_text('#!/bin/bash\nsystemctl restart shpakdnd-bot.service\n')
        (self.unit_dir/units.UNIT_NAMES[0]).write_text('Known existing bot unit\n')
        for unit in units.UNIT_NAMES[1:]:(self.unit_dir/unit).write_text('legacy '+unit)
        self.manifest=self.root/'private'/'systemd.json';self.helper=self.root/'helpers'
        self.trace=[];self.props={};self.failure=None;self.dropin=''
        self.initial_pid='123';self.restarts='0'
        for unit in units.UNIT_NAMES:
            self.props[unit]={'LoadState':'loaded','DropInPaths':'','NeedDaemonReload':'no',
                'FragmentPath':str(self.unit_dir/unit),'ActiveState':'active' if unit!=units.UNIT_NAMES[2] else 'inactive',
                'MainPID':'123','NRestarts':'0','ExecMainStatus':'0'}
        self.props[units.UNIT_NAMES[0]].update(User='shpakbot',WorkingDirectory=str(self.project),
                ExecStart='{ path='+sys.executable+' ; argv[]='+sys.executable+' '+str(self.project)+'/bot.py ; }')
        uid=getattr(os,'geteuid',lambda:0)()
        self.addCleanup(patch.stopall)
        patch.object(store,'TRUSTED_UID',uid).start()
        self.original_command=store.command
        patch.object(store,'command',side_effect=self.command).start()

    def command(self,args,**kwargs):
        args=list(map(str,args));self.trace.append(args)
        if args[0]=='git':return self.original_command(args,**kwargs)
        if args[0]=='bash':return self.original_command([BASH,args[1],shell_path(args[2])],**kwargs)
        if args[0]=='systemd-analyze':
            if self.failure=='syntax':raise DeployError('Invalid unit syntax')
            return ''
        if args[0]!='systemctl':raise AssertionError(args)
        if args[1]=='show':return self.props[args[-1]].get(args[args.index('-p')+1],'')
        action=args[1];unit=args[-1]
        if self.failure==action:raise DeployError('Injected '+action+' failure')
        if action=='mask':self.props[unit]['ActiveState']='inactive'
        elif action=='disable':self.props[unit]['ActiveState']='inactive'
        elif action=='stop':self.props[unit]['ActiveState']='inactive'
        elif action=='daemon-reload':self.load_observer()
        elif action=='enable':self.props[unit]['ActiveState']='active'
        elif action=='start':self.props[unit]['ActiveState']='inactive';self.props[unit]['ExecMainStatus']='0'
        elif action!='unmask':raise AssertionError(args)
        return ''

    def load_observer(self):
        service=(self.unit_dir/units.UNIT_NAMES[2]).read_text(encoding='utf-8')
        path=(self.unit_dir/units.UNIT_NAMES[1]).read_text(encoding='utf-8')
        if 'ExecStart=' not in service:return
        values=dict(line.split('=',1) for line in service.splitlines() if '=' in line)
        self.props[units.UNIT_NAMES[2]].update(values)
        helper=values['ExecStart']
        self.props[units.UNIT_NAMES[2]]['ExecStart']='{ path='+helper+' ; argv[]='+helper+' ; }'
        watched=[line.split('=',1)[1]+' (PathModified)' for line in path.splitlines() if line.startswith('PathModified=')]
        self.props[units.UNIT_NAMES[1]].update(Paths='\n'.join(watched),Unit=units.UNIT_NAMES[2],Triggers=units.UNIT_NAMES[2])
        self.props[units.UNIT_NAMES[2]]['DropInPaths']=self.dropin

    def install(self):
        return units.install(self.source,self.sha,project=str(self.project),python=sys.executable,
                unit_dir=str(self.unit_dir),helper_dir=str(self.helper),manifest_path=self.manifest,legacy_entry=str(self.alias))

    def binding(self):return units.verify_installed(self.manifest,project=self.project,active=True)

    def assert_bot_untouched(self):
        self.assertEqual(self.props[units.UNIT_NAMES[0]]['ActiveState'],'active')
        self.assertEqual(self.props[units.UNIT_NAMES[0]]['MainPID'],'123')
        for call in self.trace:
            if call[0]=='systemctl' and call[1] in {'start','stop','restart','disable','mask'}:
                self.assertNotEqual(call[-1],units.UNIT_NAMES[0])

    def test_complete_installation_orders_quarantine_reload_verify_activate(self):
        result=self.install();self.assertEqual(result['status'],'CONFIRMED')
        self.binding();self.assert_bot_untouched()
        mutations=[a[1] for a in self.trace if a[0]=='systemctl' and a[1]!='show']
        self.assertEqual(mutations,['mask','disable','stop','daemon-reload','unmask','daemon-reload','enable','start'])
        self.assertNotIn('systemctl restart',self.alias.read_text())
        self.assertIn(result['helper'],self.alias.read_text())
        backups=list((self.manifest.parent/'installations').glob('*/legacy-helper.txt'))
        self.assertEqual(len(backups),1);self.assertIn('systemctl restart',backups[0].read_text())

    def test_repeated_verified_installation_is_idempotent_without_reload(self):
        first=self.install();self.trace.clear();second=self.install()
        self.assertEqual(first,second)
        self.assertFalse([a for a in self.trace if a[0]=='systemctl' and a[1]!='show'])

    def test_syntax_failure_leaves_legacy_masked_and_bot_active(self):
        self.failure='syntax'
        with self.assertRaisesRegex(DeployError,'syntax'):self.install()
        self.assertFalse(self.manifest.exists());self.assert_bot_untouched()
        self.assertEqual(self.props[units.UNIT_NAMES[1]]['ActiveState'],'inactive')
        self.assertFalse([a for a in self.trace if a[:2]==['systemctl','enable']])

    def test_partial_reload_failure_never_reactivates_old_helper(self):
        self.failure='daemon-reload'
        with self.assertRaisesRegex(DeployError,'failure'):self.install()
        self.assertFalse(self.manifest.exists());self.assert_bot_untouched()
        self.assertEqual(self.props[units.UNIT_NAMES[2]]['ActiveState'],'inactive')
        report=list((self.manifest.parent/'installations').glob('*/report.json'))[0]
        self.assertEqual(store.read_record(report)['status'],'FAIL')

    def test_unexpected_dropin_rejected_before_safe_watcher_activation(self):
        self.dropin='/etc/systemd/system/override.conf'
        with self.assertRaisesRegex(DeployError,'drop-in'):self.install()
        self.assertFalse(self.manifest.exists());self.assert_bot_untouched()
        self.assertFalse([a for a in self.trace if a[:2]==['systemctl','enable']])

    def test_execstart_substitution_is_detected(self):
        self.install();self.props[units.UNIT_NAMES[2]]['ExecStart']='{ path=/bin/bash ; argv[]=/bin/bash unsafe.sh ; }'
        with self.assertRaisesRegex(DeployError,'ExecStart'):self.binding()

    def test_fragment_substitution_is_detected(self):
        self.install();self.props[units.UNIT_NAMES[2]]['FragmentPath']='/run/systemd/system/unsafe.service'
        with self.assertRaisesRegex(DeployError,'FragmentPath'):self.binding()

    def test_watch_rule_or_unit_drift_is_detected(self):
        self.install()
        for field,value in [('Paths','/opt/shpakdnd-bot/shpakdnd.db (PathModified)'),('Unit','another.service'),('Triggers','another.service')]:
            original=self.props[units.UNIT_NAMES[1]][field];self.props[units.UNIT_NAMES[1]][field]=value
            with self.assertRaises(DeployError):self.binding()
            self.props[units.UNIT_NAMES[1]][field]=original

    def test_helper_or_unit_content_drift_is_detected(self):
        installed=self.install();Path(installed['helper']).write_text('unsafe changed helper')
        with self.assertRaisesRegex(DeployError,'drift'):self.binding()

    def test_effective_dropin_or_reload_requirement_blocks_gate(self):
        self.install()
        for field,value in [('DropInPaths','/etc/override.conf'),('NeedDaemonReload','yes')]:
            self.props[units.UNIT_NAMES[0]][field]=value
            with self.assertRaisesRegex(DeployError,'drop-in|reload'):self.binding()
            self.props[units.UNIT_NAMES[0]][field]='' if field=='DropInPaths' else 'no'

    def test_hardening_drift_or_additional_exec_blocks_gate(self):
        self.install()
        for field,value in [('NoNewPrivileges','no'),('ExecStartPost','unsafe'),('CapabilityBoundingSet','CAP_SYS_ADMIN')]:
            original=self.props[units.UNIT_NAMES[2]].get(field,'');self.props[units.UNIT_NAMES[2]][field]=value
            with self.assertRaises(DeployError):self.binding()
            self.props[units.UNIT_NAMES[2]][field]=original

    def test_binding_drift_invalidates_preflight(self):
        self.install();binding=self.binding()
        record=store.read_record(self.manifest);record['source_sha']='d'*40;store.save_record(self.manifest,record)
        with self.assertRaisesRegex(DeployError,'since preflight'):units.verify_binding(binding)

    def test_unsafe_file_permissions_or_owner_fail_closed(self):
        installed=self.install();helper=Path(installed['helper'])
        if os.name=='posix':
            helper.chmod(0o777)
            with self.assertRaisesRegex(DeployError,'permissions'):self.binding()
            helper.chmod(0o755)
            with patch.object(store,'TRUSTED_UID',os.geteuid()+1):
                with self.assertRaisesRegex(DeployError,'owner'):self.binding()
        else:
            with patch.object(Path,'is_symlink',return_value=True):
                with self.assertRaisesRegex(DeployError,'symlink'):self.binding()

    def test_dirty_or_wrong_source_sha_is_rejected_before_services(self):
        with self.assertRaisesRegex(DeployError,'SHA/worktree'):
            units.install(self.source,'e'*40,unit_dir=self.unit_dir,manifest_path=self.manifest)
        self.assertFalse([a for a in self.trace if a[0]=='systemctl'])
        (self.source/'deploy/update-shpakdnd-bot.sh').write_text('unsafe local copy')
        with self.assertRaisesRegex(DeployError,'SHA/worktree'):self.install()
        self.assertFalse([a for a in self.trace if a[0]=='systemctl'])

    def test_sigterm_in_install_phase_retains_fail_report_and_quarantine(self):
        self.failure='daemon-reload'
        with patch.object(store,'atomic_bytes',wraps=store.atomic_bytes) as atomic:
            with self.assertRaises(DeployError):self.install()
            self.assertTrue(atomic.called)
        self.assert_bot_untouched();self.assertFalse(self.manifest.exists())

    def test_interrupt_during_reload_records_failure_without_legacy_reactivation(self):
        original=self.command
        def interrupted(args,**kw):
            if list(args)[:2]==['systemctl','daemon-reload']:raise KeyboardInterrupt('SIGTERM during installer')
            return original(args,**kw)
        with patch.object(store,'command',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):self.install()
        self.assert_bot_untouched();self.assertFalse(self.manifest.exists())
        self.assertTrue((self.unit_dir/(units.UNIT_NAMES[2]+'.d')/'shpakdnd-quarantine.conf').exists())
        reports=list((self.manifest.parent/'installations').glob('*/report.json'))
        self.assertEqual(store.read_record(reports[0])['status'],'FAIL')

    def test_power_loss_between_unit_replacements_leaves_persistent_quarantine(self):
        import time
        ready=self.root/'child-ready'
        kwargs=dict(source=str(self.source),sha=self.sha,project=str(self.project),python=sys.executable,
                    unit_dir=str(self.unit_dir),helper_dir=str(self.helper),manifest_path=str(self.manifest),legacy_entry=str(self.alias))
        parameters=self.root/'child-input.json'
        parameters.write_text(json.dumps(dict(kwargs=kwargs,props=self.props,ready=str(ready),bash=BASH)))
        child=self.root/'child.py'
        child.write_text("""import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,str(Path(sys.argv[1]).parent))
import systemd_state as u,release_state as s
v=json.loads(Path(sys.argv[2]).read_text());s.TRUSTED_UID=getattr(os,'geteuid',lambda:0)()
original=s.command
from tests.v1_3_3.test_deploy_shell import shell_path
# Fixture alone calls a fake bus; no host systemctl is ever executed.
def bus(args,**kw):
 args=list(map(str,args))
 if args[0]=='git':return original(args,**kw)
 if args[0]=='bash':return original([v['bash'],args[1],shell_path(args[2])],**kw)
 if args[0]=='systemd-analyze':return ''
 if args[1]=='show':return v['props'][args[-1]].get(args[args.index('-p')+1],'')
 if args[1] in {'mask','disable','stop'}:v['props'][args[-1]]['ActiveState']='inactive'
 return ''
s.command=bus
atomic=s.atomic_bytes
def replace(path,content,mode=0o600):
 if Path(path)==Path(v['kwargs']['unit_dir'])/u.UNIT_NAMES[1]:
  Path(v['ready']).write_text('durable quarantine; partial unit installation')
  time.sleep(60)
 return atomic(path,content,mode)
s.atomic_bytes=replace
u.install(**v['kwargs'])
""",encoding='utf-8')
        process=subprocess.Popen([sys.executable,str(child),str(ROOT/'deploy'),str(parameters)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+20
            while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            if not ready.exists():
                if process.poll() is None:process.kill()
                out,err=process.communicate(timeout=5);self.fail((out+err).decode('utf-8'))
            process.kill();process.communicate(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.communicate(timeout=5)
        self.assertFalse(self.manifest.exists());self.assert_bot_untouched()
        quarantine=self.unit_dir/(units.UNIT_NAMES[2]+'.d')/'shpakdnd-quarantine.conf'
        self.assertIn('ConditionPathExists=!',quarantine.read_text())
        self.assertTrue((self.manifest.parent/'observer-quarantined').exists())
        reports=list((self.manifest.parent/'installations').glob('*/report.json'))
        self.assertEqual(store.read_record(reports[0])['status'],'RUNNING')
        self.assertFalse([a for a in self.trace if a[:2]==['systemctl','enable']])

    def test_untrusted_version_parent_rejected_before_creation_or_service_changes(self):
        self.helper.mkdir();versions=self.helper/'versions';versions.mkdir()
        trusted=store.trusted_path
        def reject(path,**kw):
            if Path(path)==versions:raise DeployError('untrusted version parent')
            return trusted(path,**kw)
        with patch.object(store,'trusted_path',side_effect=reject):
            with self.assertRaisesRegex(DeployError,'untrusted version parent'):self.install()
        self.assertFalse((versions/self.sha).exists())
        self.assertFalse([a for a in self.trace if a[0]=='systemctl'])

    def test_whole_invocation_journal_errors_block_startup_health(self):
        self.install();value=self.binding();process=dict(pid='123',restarts='0',invocation='a'*32,started_monotonic='123456')
        for error in ('ERROR: database failure','sqlite3.OperationalError: locked','Start request repeated too quickly'):
            with patch.object(units,'verify_binding'),patch.object(units,'process_identity',return_value=process),patch.object(store,'command',return_value='ok\n'*150+error) as call:
                with self.assertRaisesRegex(DeployError,'journal'):units.health(value,since='ignored')
                self.assertNotIn('-n',call.call_args.args[0]);self.assertNotIn('--since',call.call_args.args[0])

    def test_before_watcher_health_rejects_premature_activation(self):
        self.install();value=self.binding()
        with patch.object(units,'verify_binding'):
            with self.assertRaisesRegex(DeployError,'Watcher must remain inactive'):units.health(value,since='ignored',before_watcher=True)
