"""Real Bash flow with ONLY fake production commands and disposable paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
BASH=shutil.which('bash') if os.name!='nt' else r'C:\Program Files\Git\bin\bash.exe'
OLD='a'*40;TARGET='b'*40


def shell_path(path):
    value=str(Path(path).resolve()).replace('\\','/')
    return '/'+value[0].lower()+value[2:] if os.name=='nt' else value


DISPATCH=r'''#!/usr/bin/env bash
set -eu
name="$(basename "$0")"
if [[ "$name" != runuser && "$name" != install ]]; then printf '%s %s\n' "$name" "$*" >> "$FAKE_STATE/trace"; fi
case "$name" in
id) echo 0 ;;
runuser) shift 3; exec "$@" ;;
flock) [[ "${FAKE_LOCKED:-0}" != 1 ]] ;;
install)
    directory=0;args=()
    while (($#)); do
        case "$1" in -o|-g|-m) shift 2 ;; -d) directory=1;shift ;; *) args+=("$1"); shift ;; esac
    done
    if ((directory)); then mkdir -p -- "${args[@]}"; else cp -- "${args[@]}"; fi ;;
git)
    shift 2
    case "$1" in
        status) [[ "${FAKE_DIRTY:-0}" != 1 ]] || printf ' M app/local.py\n' ;;
        fetch) : ;;
        rev-parse)
            if [[ "$*" == *--show-toplevel* ]]; then printf '%s\n' "$SHPAKDND_PROJECT"
            elif [[ "$*" == *origin/main* ]]; then cat "$FAKE_STATE/target"
            else cat "$FAKE_STATE/head"; fi ;;
        ls-tree) printf 'bot.py\napp/config.py\n.env.example\n' ;;
        switch) printf '%s\n' "${@: -1}" > "$FAKE_STATE/head" ;;
        diff) [[ "${FAKE_DIFF_FAIL:-0}" != 1 ]] ;;
        *) echo "unknown git $*" >&2; exit 9 ;;
    esac ;;
systemctl)
    unit="${@: -1}"
    case "$1" in
        show)
            case "$3" in
                LoadState) echo loaded ;;
                User) echo shpakbot ;;
                WorkingDirectory) echo "$SHPAKDND_PROJECT" ;;
                ExecStart) echo "$SHPAKDND_PYTHON bot.py" ;;
                Triggers) echo shpakdnd-bot-update.service ;;
                MainPID) echo 123 ;;
                NRestarts) echo 0 ;;
                *) exit 9 ;;
            esac ;;
        is-active)
            status="$(cat "$FAKE_STATE/$unit")"
            [[ "$*" == *--quiet* ]] || echo "$status"
            [[ "$status" == active ]] ;;
        stop) echo inactive > "$FAKE_STATE/$unit" ;;
        start)
            if [[ "$unit" == shpakdnd-bot.service && "${FAKE_START_FAIL:-0}" == 1 ]]; then
                echo inactive > "$FAKE_STATE/$unit"
            else echo active > "$FAKE_STATE/$unit"; fi ;;
        --no-pager) echo 'fake status' ;;
        *) exit 9 ;;
    esac ;;
journalctl)
    [[ "${FAKE_JOURNAL_FAIL:-0}" != 1 ]] || echo 'Traceback: fake startup failure'
    echo 'fake startup journal' ;;
python)
    if [[ "$*" == *--redact-env* ]]; then exec cat; fi
    if [[ "$*" == *telegram-deploy-notice.py* ]]; then
        count="$(cat "$FAKE_STATE/notices")"; count=$((count+1));echo "$count" > "$FAKE_STATE/notices"
        if [[ "${FAKE_NOTICE_FAIL:-0}" == "$count" ]]; then echo 'fake Telegram failure' >&2; exit 1; fi
    elif [[ "$*" == *sqlite-deploy.py*' backup '* ]]; then
        while [[ "$1" != --destination ]]; do shift; done
        touch "$2"
        if [[ "${FAKE_SIGNAL:-0}" == 1 ]]; then kill -TERM "$PPID"; fi
    elif [[ "$*" == *check_bot.py* ]]; then
        # Fail only for the newly installed target; rollback can still pass.
        if [[ "${FAKE_CHECK_FAIL:-0}" == 1 && "$(cat "$FAKE_STATE/head")" == "$(cat "$FAKE_STATE/target")" ]]; then exit 1; fi
    elif [[ "$*" == *'-m unittest'* ]]; then
        printf 'Ran 3 tests in 0.001s\n\nOK\n' >&2
    fi ;;
*) echo "Unexpected fake command: $name" >&2;exit 9 ;;
esac
'''


@unittest.skipUnless(BASH and Path(BASH).exists(),'Bash unavailable')
class DeployShellTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.project=self.root/'project';self.project.mkdir()
        self.state=self.root/'state';self.state.mkdir()
        self.bin=self.root/'bin';self.bin.mkdir()
        for name in ('id','runuser','flock','install','git','systemctl','journalctl','python'):
            file=self.bin/name;file.write_text(DISPATCH,encoding='utf-8',newline='\n');file.chmod(0o755)
        (self.project/'.env').write_text('BOT_TOKEN=fake-not-a-real-token\n')
        (self.project/'shpakdnd.db').write_bytes(b'unchanged fake database')
        for unit,value in [('shpakdnd-bot.service','active'),('shpakdnd-bot-watch.path','active'),('shpakdnd-bot-update.service','inactive')]:
            (self.state/unit).write_text(value+'\n')
        (self.state/'head').write_text(OLD+'\n');(self.state/'target').write_text(TARGET+'\n');(self.state/'notices').write_text('0\n')
        runtime=self.root/'runtime';runtime.mkdir()
        self.env=dict(os.environ,FAKE_STATE=shell_path(self.state),
            SHPAKDND_PROJECT=shell_path(self.project),SHPAKDND_PYTHON=shell_path(self.bin/'python'),
            SHPAKDND_BACKUPS=shell_path(self.root/'backups'),SHPAKDND_LOGS=shell_path(self.root/'logs'),
            SHPAKDND_LOCK=shell_path(self.root/'lock'),SHPAKDND_RUNTIME_DIR=shell_path(runtime),SHPAKDND_START_WAIT='0')

    def run_deploy(self,answers='',dry=False,**flags):
        env=dict(self.env,**{k:str(v) for k,v in flags.items()})
        # Set PATH within Bash to avoid MSYS Windows PATH conversion ambiguity.
        command='export PATH="'+shell_path(self.bin)+':$PATH"; exec bash "'+shell_path(ROOT/'deploy/deploy-shpakdnd.sh')+'"'+(' --dry-run' if dry else '')
        result=subprocess.run([BASH,'--noprofile','--norc','-c',command],env=env,input=answers.encode('utf-8'),capture_output=True,timeout=60)
        result.stdout=result.stdout.decode('utf-8');result.stderr=result.stderr.decode('utf-8')
        trace=(self.state/'trace').read_text(encoding='utf-8') if (self.state/'trace').exists() else ''
        return result,trace

    def assert_stopped(self):
        for unit in ('shpakdnd-bot.service','shpakdnd-bot-watch.path'):
            self.assertEqual((self.state/unit).read_text().strip(),'inactive')

    def test_shell_syntax_and_dry_run_no_production_mutation(self):
        for name in ('deploy-shpakdnd.sh','install-deploy-shpakdnd.sh','update-shpakdnd-bot.sh'):
            result=subprocess.run([BASH,'-n',shell_path(ROOT/'deploy'/name)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
        result,trace=self.run_deploy(dry=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Dry run: OK',result.stdout);self.assertIn('git -C '+shell_path(self.project)+' fetch origin',trace)
        for forbidden in ('systemctl stop','systemctl start','git switch','telegram-deploy-notice.py',' backup ','-m unittest','check_bot.py'):
            self.assertNotIn(forbidden,trace)
        self.assertEqual((self.state/'head').read_text().strip(),OLD)
        self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')
        self.assertFalse((self.root/'backups').exists())

    def test_dirty_worktree_and_duplicate_lock_never_stop_bot(self):
        for flag in ('FAKE_DIRTY','FAKE_LOCKED'):
            with self.subTest(flag=flag):
                result,trace=self.run_deploy('y\n',**{flag:1})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('systemctl stop',trace)
                self.assertIn('app/local.py' if flag=='FAKE_DIRTY' else 'Deployment уже выполняется',result.stdout+result.stderr)

    def test_success_orders_fetch_notice_stop_backup_checkout_checks_start(self):
        result,trace=self.run_deploy('y\ny\ny\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        markers=[' fetch origin','telegram-deploy-notice.py','systemctl stop shpakdnd-bot-watch.path','systemctl stop shpakdnd-bot-update.service','systemctl stop shpakdnd-bot.service',' backup ',' switch ','-m compileall','check_bot.py','-m unittest',' check --db',' diff --check','systemctl start shpakdnd-bot.service','systemctl start shpakdnd-bot-watch.path']
        positions=[trace.index(marker) for marker in markers]
        self.assertEqual(positions,sorted(positions))
        self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertEqual((self.state/'notices').read_text().strip(),'2')
        self.assertIn('Tests: 3 tests, OK',result.stdout)
        self.assertEqual(len(list((self.root/'backups').glob('*.db'))),1)
        self.assertEqual(len(list((self.root/'logs').glob('*.log'))),1)

    def test_check_failure_does_not_start_and_code_rollback_requires_choice(self):
        result,trace=self.run_deploy('y\ny\n1\n',FAKE_CHECK_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace);self.assertNotIn('-m unittest',trace)
        self.assertIn('Проверка не пройдена: check_bot',result.stdout)
        self.assertEqual((self.state/'notices').read_text().strip(),'1')

    def test_confirmed_code_rollback_never_restores_database(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_CHECK_FAIL=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertEqual((self.state/'head').read_text().strip(),OLD)
        self.assertIn('Предыдущая версия восстановлена',result.stdout)
        self.assertIn('systemctl start shpakdnd-bot-watch.path',trace)
        self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')
        self.assertEqual(trace.count(' backup '),1)

    def test_notice_failure_needs_extra_confirmation_before_stop(self):
        result,trace=self.run_deploy('y\nn\n',FAKE_NOTICE_FAIL=1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Продолжить deployment?',result.stdout)
        self.assertNotIn('systemctl stop',trace)

    def test_final_notice_failure_is_warning_after_healthy_deployment(self):
        result,trace=self.run_deploy('y\ny\ny\n',FAKE_NOTICE_FAIL=2)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WARNING: Финальное',result.stdout);self.assertIn('Deployment завершён',result.stdout)

    def test_target_decline_restarts_old_code_only_after_confirmation(self):
        result,trace=self.run_deploy('y\nn\ny\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.state/'head').read_text().strip(),OLD)
        self.assertNotIn(' switch ',trace);self.assertIn('systemctl start shpakdnd-bot.service',trace)

    def test_eof_at_launch_never_starts_bot(self):
        result,trace=self.run_deploy('y\ny\n')
        self.assertEqual(result.returncode,2,result.stdout+result.stderr);self.assert_stopped()
        self.assertNotIn('systemctl start',trace);self.assertIn('Ввод закрыт',result.stdout)

    def test_failed_start_and_journal_failure_never_end_maintenance(self):
        for flag in ('FAKE_START_FAIL','FAKE_JOURNAL_FAIL'):
            with self.subTest(flag=flag):
                (self.state/'notices').write_text('0\n')
                (self.state/'trace').unlink(missing_ok=True)
                result,trace=self.run_deploy('y\ny\ny\n1\n',**{flag:1})
                self.assertEqual(result.returncode,1,result.stdout+result.stderr);self.assert_stopped()
                self.assertNotIn('systemctl start shpakdnd-bot-watch.path',trace)
                self.assertEqual((self.state/'notices').read_text().strip(),'1')
                self.assertIn('journalctl',trace)

    def test_same_head_does_not_restart_without_consent(self):
        (self.state/'target').write_text(OLD+'\n')
        result,trace=self.run_deploy('n\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Production уже находится',result.stdout)
        self.assertNotIn('systemctl stop',trace)

    def test_sigterm_after_stop_reports_state_without_start_or_checkout(self):
        result,trace=self.run_deploy('y\n',FAKE_SIGNAL=1)
        self.assertEqual(result.returncode,143,result.stdout+result.stderr);self.assert_stopped()
        self.assertIn('Deployment прерван',result.stdout)
        self.assertIn('Bot service: inactive',result.stdout)
        self.assertNotIn('systemctl start',trace);self.assertNotIn(' switch ',trace)
