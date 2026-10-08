"""Real Bash flow with ONLY fake production commands and disposable paths."""
import os
import queue
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
BASH=shutil.which('bash') if os.name!='nt' else r'C:\Program Files\Git\bin\bash.exe'
OLD='a'*40;TARGET='b'*40


def shell_path(path,*,preserve_symlink=False):
    # Executables must keep their venv entry point, even when it is a symlink.
    absolute=os.path.abspath(path) if preserve_symlink else str(Path(path).resolve())
    value=absolute.replace('\\','/')
    return '/'+value[0].lower()+value[2:] if os.name=='nt' else value


DISPATCH=r'''#!/usr/bin/env bash
set -eu
name="$(basename "$0")"
if [[ "$name" != runuser && "$name" != install ]]; then printf '%s %s\n' "$name" "$*" >> "$FAKE_STATE/trace"; fi
case "$name" in
id) echo 0 ;;
runuser) shift 3; exec "$@" ;;
flock)
    [[ "${FAKE_LOCKED:-0}" != 1 ]] || exit 1
    if [[ "${FAKE_CONCURRENT_LOCK:-0}" == 1 ]]; then mkdir "$FAKE_STATE/lock-owner" 2>/dev/null; fi ;;
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
        fetch) [[ "${FAKE_FETCH_FAIL:-0}" != 1 ]] ;;
        rev-parse)
            if [[ "$*" == *--show-toplevel* ]]; then printf '%s\n' "$SHPAKDND_PROJECT"
            elif [[ "$*" == *origin/main* ]]; then [[ "${FAKE_TARGET_MISSING:-0}" != 1 ]] || exit 1; cat "$FAKE_STATE/target"
            else cat "$FAKE_STATE/head"; fi ;;
        ls-tree) printf 'bot.py\napp/config.py\n.env.example\n' ;;
        switch)
            if [[ "${@: -1}" == "$(cat "$FAKE_STATE/target")" && "${FAKE_CHECKOUT_FAIL:-0}" == 1 ]]; then exit 1; fi
            printf '%s\n' "${@: -1}" > "$FAKE_STATE/head" ;;
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
                ActiveState) cat "$FAKE_STATE/$unit" ;;
                MainPID)
                    count=0;[[ ! -f "$FAKE_STATE/pid-queries" ]] || count="$(cat "$FAKE_STATE/pid-queries")"
                    count=$((count+1));echo "$count" > "$FAKE_STATE/pid-queries"
                    if [[ "${FAKE_PID_CHANGED:-0}" == 1 && "$count" -ge 2 ]]; then echo 124;else echo 123;fi ;;
                NRestarts)
                    count=0;[[ ! -f "$FAKE_STATE/restart-queries" ]] || count="$(cat "$FAKE_STATE/restart-queries")"
                    count=$((count+1));echo "$count" > "$FAKE_STATE/restart-queries"
                    if [[ "${FAKE_RESTART_CHANGED:-0}" == 1 && "$count" -ge 2 ]]; then echo 1;else echo 0;fi ;;
                *) exit 9 ;;
            esac ;;
        is-active)
            status="$(cat "$FAKE_STATE/$unit")"
            [[ "$*" == *--quiet* ]] || echo "$status"
            [[ "$status" == active ]] ;;
        stop) echo inactive > "$FAKE_STATE/$unit" ;;
        start)
            if [[ "$unit" == shpakdnd-bot-watch.path && "${FAKE_WATCH_START_FAIL:-0}" == 1 ]]; then exit 1; fi
            if [[ "$unit" == shpakdnd-bot.service && "${FAKE_START_FAIL:-0}" == 1 ]]; then
                echo inactive > "$FAKE_STATE/$unit"
            else echo active > "$FAKE_STATE/$unit"; fi ;;
        --no-pager) echo 'fake status' ;;
        *) exit 9 ;;
    esac ;;
journalctl)
    [[ "${FAKE_JOURNAL_FAIL:-0}" != 1 ]] || echo 'Traceback: fake startup failure'
    if [[ "${FAKE_LATE_JOURNAL_FAIL:-0}" == 1 && "$(cat "$FAKE_STATE/shpakdnd-bot-watch.path")" == active ]]; then echo 'RuntimeError: late startup failure';fi
    echo 'fake startup journal' ;;
python)
    [[ "${PYTHONDONTWRITEBYTECODE:-0}" == 1 ]] || { echo "Bytecode writes were not disabled" >&2;exit 1; }
    if [[ "$*" == *--redact-env* ]]; then
        if [[ -n "${FAKE_REAL_PYTHON:-}" ]]; then exec "$FAKE_REAL_PYTHON" "$@"; fi
        exec cat
    fi
    if [[ "$*" == *systemd_state.py* ]]; then
        [[ "${FAKE_UNIT_DRIFT:-0}" != 1 ]] || exit 1
        if [[ "${FAKE_UNIT_DRIFT_AFTER:-0}" == 1 && "$(cat "$FAKE_STATE/head")" == "$(cat "$FAKE_STATE/target")" ]]; then exit 1; fi
    elif [[ "$*" == *release_report.py*' gate '* || "$*" == *release_report.py*' gate-staging '* ]]; then
        [[ "${FAKE_FINAL_GATE_FAIL:-0}" != 1 ]] || exit 1
    elif [[ "$*" == *release_lkg.py* ]]; then
        case "$2" in
            target)
                [[ "${FAKE_LKG_ABSENT:-0}" != 1 && "${FAKE_LKG_CORRUPT:-0}" != 1 && "${FAKE_LKG_COMMIT_MISSING:-0}" != 1 ]] || exit 1
                if [[ "${FAKE_LKG_DIFFERENT:-0}" == 1 ]]; then printf 'cccccccccccccccccccccccccccccccccccccccc\n';else cat "$FAKE_STATE/lkg";fi ;;
            smoke|health|startup-health|rollback-health) [[ "${FAKE_SMOKE_FAIL:-0}" != 1 ]] || exit 1 ;;
            status) echo 'TARGET installed; current LKG:';cat "$FAKE_STATE/lkg";echo 'Semantic/LKG promotion reported separately' ;;
            confirm)
                if [[ "${FAKE_SEMANTIC_ABSENT:-0}" == 1 ]]; then echo 'Real Telegram NOT_RUN; previous LKG retained; release NOT CONFIRMED';exit 0;fi
                IFS= read -r answer || answer=""
                if [[ "$answer" == "$(cat "$FAKE_STATE/target")" ]]; then cat "$FAKE_STATE/target" > "$FAKE_STATE/lkg";echo 'LKG CONFIRMED';fi ;;
            *) exit 9 ;;
        esac
    elif [[ "$*" == *release_preflight.py*' run '* || "$*" == *legacy_lkg.py* ]]; then
        while [[ "$1" != --evidence ]]; do shift; done
        evidence="$2"; echo RUNNING > "$evidence"
        printf 'python -m compileall TARGET\ncheck_bot.py SNAPSHOT\nvalidators TARGET\npython -m unittest TARGET\ngit diff --check BASE TARGET\n' >> "$FAKE_STATE/trace"
        [[ "${FAKE_PREFLIGHT_WAIT:-0}" == 0 ]] || sleep "$FAKE_PREFLIGHT_WAIT"
        if [[ "${FAKE_PREFLIGHT_SIGNAL:-0}" == 1 ]]; then kill -TERM "${FAKE_DEPLOY_PID:-$PPID}"; sleep 1; exit 143; fi
        [[ "${FAKE_PREFLIGHT_FAIL:-0}" == 0 ]] || { echo FAIL > "$evidence"; exit 1; }
        echo PASS > "$evidence"
        if [[ "${FAKE_TARGET_CHANGED:-0}" == 1 ]]; then printf '%040d\n' 2 > "$FAKE_STATE/target"; fi
        if [[ "${FAKE_OLD_CHANGED:-0}" == 1 ]]; then printf '%040d\n' 1 > "$FAKE_STATE/head"; fi
    elif [[ "$*" == *release_preflight.py*' validate '* ]]; then
        [[ "${FAKE_EVIDENCE_BAD:-0}" != 1 ]] || exit 1
        if [[ "$*" == *--rollback-db* && "${FAKE_ROLLBACK_INCOMPATIBLE:-0}" == 1 ]]; then exit 1; fi
        echo 'Preflight evidence: PASS; tests=3; rollback_compatible=True'
    elif [[ "$*" == *release_preflight.py*' invalidate '* ]]; then
        while [[ "$1" != --evidence ]]; do shift; done
        echo FAIL > "$2"
    elif [[ "$*" == *release_preflight.py*' deployment-init '* ]]; then
        [[ "${FAKE_SOURCE_SCHEMA_BAD:-0}" != 1 ]] || exit 1
        echo SOURCE > "$FAKE_STATE/db-stage"
        echo 'DB_STAGE SOURCE' >> "$FAKE_STATE/trace"
    elif [[ "$*" == *release_preflight.py*' live-runtime '* ]]; then
        [[ "$(cat "$FAKE_STATE/db-stage")" == SOURCE ]] || exit 1
        echo MIGRATION_STARTED > "$FAKE_STATE/db-stage"
        printf 'DB_STAGE MIGRATION_STARTED\npython runtime --short TARGET\n' >> "$FAKE_STATE/trace"
        if [[ "${FAKE_MIGRATION_SIGNAL:-0}" == 1 ]]; then kill -TERM "$FAKE_DEPLOY_PID"; sleep 2; exit 143; fi
        if [[ "${FAKE_RUNTIME_FAIL:-0}" == 1 || "${FAKE_TARGET_SCHEMA_BAD:-0}" == 1 || "${FAKE_MIGRATION_DATA_CHANGED:-0}" == 1 ]]; then
            echo UNKNOWN > "$FAKE_STATE/db-stage";exit 1
        fi
        echo TARGET > "$FAKE_STATE/db-stage"
        echo 'DB_STAGE TARGET' >> "$FAKE_STATE/trace"
    elif [[ "$*" == *release_preflight.py*' deployment-check '* || "$*" == *release_preflight.py*' deployment-startup '* ]]; then
        stage="$(cat "$FAKE_STATE/db-stage")"
        [[ "$stage" == SOURCE || "$stage" == TARGET ]] || exit 1
        if [[ "$*" == *'--expected-schema source'* ]]; then [[ "$stage" == SOURCE ]] || exit 1; fi
        if [[ "$*" == *'--expected-schema target'* ]]; then
            [[ "$stage" == TARGET && "${FAKE_TARGET_POSTCHECK_FAIL:-0}" != 1 ]] || exit 1
        fi
        [[ "${FAKE_SOURCE_DATA_CHANGED:-0}" != 1 ]] || exit 1
        if [[ "$*" == *--rollback* ]]; then
            [[ "${FAKE_ROLLBACK_UNKNOWN:-0}" != 1 && "${FAKE_LKG_ABSENT:-0}" != 1 ]] || exit 1
            if [[ "$stage" == SOURCE && "${FAKE_LKG_SOURCE_INCOMPATIBLE:-0}" == 1 ]]; then exit 1; fi
            if [[ "$stage" == TARGET && "${FAKE_LKG_TARGET_INCOMPATIBLE:-0}" == 1 ]]; then exit 1; fi
            if [[ "$stage" == TARGET && "${FAKE_ROLLBACK_INCOMPATIBLE:-0}" == 1 ]]; then exit 1; fi
        fi
        if [[ "$*" == *' deployment-startup '* ]]; then touch "$FAKE_STATE/startup-attempted"; fi
    elif [[ "$*" == *release_preflight.py*' deployment-abort '* ]]; then
        if [[ -f "$FAKE_STATE/db-stage" ]] && { [[ "$(cat "$FAKE_STATE/db-stage")" == MIGRATION_STARTED ]] || [[ -f "$FAKE_STATE/startup-attempted" ]]; }; then echo UNKNOWN > "$FAKE_STATE/db-stage"; fi
    elif [[ "$*" == *release_preflight.py*' runtime '* ]]; then
        if [[ "${FAKE_RUNTIME_FAIL:-0}" == 1 && "$(cat "$FAKE_STATE/head")" == "$(cat "$FAKE_STATE/target")" ]]; then exit 1; fi
    elif [[ "$*" == *telegram-deploy-notice.py* ]]; then
        count="$(cat "$FAKE_STATE/notices")"; count=$((count+1));echo "$count" > "$FAKE_STATE/notices"
        if [[ "${FAKE_NOTICE_FAIL:-0}" == "$count" ]]; then echo 'fake Telegram failure' >&2; exit 1; fi
    elif [[ "$*" == *sqlite-deploy.py*' backup '* ]]; then
        while [[ "$1" != --destination ]]; do shift; done
        [[ "${FAKE_BACKUP_FAIL:-0}" != 1 ]] || exit 1
        touch "$2"
        if [[ "${FAKE_SIGNAL:-0}" == 1 ]]; then kill -TERM "${FAKE_DEPLOY_PID:-$PPID}"; fi
    elif [[ "$*" == *check_bot.py* ]]; then
        # Fail only for the newly installed target; rollback can still pass.
        if [[ "${FAKE_CHECK_FAIL:-0}" == 1 && "$(cat "$FAKE_STATE/head")" == "$(cat "$FAKE_STATE/target")" ]]; then exit 1; fi
    elif [[ "$*" == *'-m unittest'* ]]; then
        printf 'Ran 3 tests in 0.001s\n\nOK\n' >&2
    fi ;;
*) echo "Unexpected fake command: $name" >&2;exit 9 ;;
esac
'''


class ShellPathTests(unittest.TestCase):
    def test_executable_does_not_resolve_virtualenv_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'.venv'/'bin'/'python'
            system_python=Path(directory)/'system-python'
            # Simulate dereferencing without requiring Windows symlink privileges.
            with patch.object(sys,'executable',str(executable)), \
                 patch.object(Path,'resolve',return_value=system_python) as resolve:
                value=shell_path(sys.executable,preserve_symlink=True)
                resolve.assert_not_called()
            expected=str(executable).replace('\\','/')
            if os.name=='nt':expected='/'+expected[0].lower()+expected[2:]
            self.assertEqual(value,expected)
            self.assertNotEqual(value,shell_path(system_python))

    @unittest.skipUnless(os.name=='posix','POSIX symlink semantics')
    def test_real_posix_executable_symlink_keeps_venv_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'.venv'/'bin'/'python'
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
            self.assertTrue(executable.is_symlink())
            value=shell_path(executable,preserve_symlink=True)
            self.assertEqual(value,str(executable))
            self.assertNotEqual(value,str(executable.resolve()))
            self.assertEqual(Path(value).resolve(),Path(sys.executable).resolve())


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
        (self.state/'lkg').write_text(OLD+'\n')
        (self.state/'head').write_text(OLD+'\n');(self.state/'target').write_text(TARGET+'\n');(self.state/'notices').write_text('0\n')
        runtime=self.root/'runtime';runtime.mkdir()
        self.env=dict(os.environ,FAKE_STATE=shell_path(self.state),
            SHPAKDND_PROJECT=shell_path(self.project),SHPAKDND_PYTHON=shell_path(self.bin/'python'),
            SHPAKDND_BACKUPS=shell_path(self.root/'backups'),SHPAKDND_LOGS=shell_path(self.root/'logs'),
            SHPAKDND_LOCK=shell_path(self.root/'lock'),SHPAKDND_RUNTIME_DIR=shell_path(runtime),SHPAKDND_START_WAIT='0',
            SHPAKDND_PREFLIGHT_EVIDENCE=shell_path(self.root/'evidence'),SHPAKDND_PREFLIGHT_WORK=shell_path(self.root/'preflight-work'))

    def run_deploy(self,answers='',dry=False,preflight=False,**flags):
        env=dict(self.env,**{k:str(v) for k,v in flags.items()})
        # Set PATH within Bash to avoid MSYS Windows PATH conversion ambiguity.
        command='export PATH="'+shell_path(self.bin)+':$PATH"; export FAKE_DEPLOY_PID=$$; exec bash "'+shell_path(ROOT/'deploy/deploy-shpakdnd.sh')+'"'+(' --dry-run' if dry else ' --preflight' if preflight else '')
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
        markers=[' fetch origin','-m compileall','check_bot.py','validators TARGET','-m unittest','release_preflight.py validate','telegram-deploy-notice.py','systemctl stop shpakdnd-bot-watch.path','systemctl stop shpakdnd-bot-update.service','systemctl stop shpakdnd-bot.service',' backup ',' switch ','runtime --short',' check --db','systemctl start shpakdnd-bot.service','systemctl start shpakdnd-bot-watch.path']
        positions=[trace.index(marker) for marker in markers]
        self.assertEqual(positions,sorted(positions))
        self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertEqual((self.state/'notices').read_text().strip(),'2')
        self.assertIn('Tests: 3 tests, OK',result.stdout)
        self.assertEqual(len(list((self.root/'backups').glob('*.db'))),1)
        self.assertEqual(len(list((self.root/'logs').glob('*.log'))),1)

    def test_confirmed_states_are_recorded_in_order_before_writes_and_startup(self):
        result,trace=self.run_deploy('y\ny\ny\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        markers=['-m unittest','systemctl stop shpakdnd-bot.service',' backup ',
                 'DB_STAGE SOURCE',' switch ','DB_STAGE MIGRATION_STARTED','runtime --short',
                 'DB_STAGE TARGET','deployment-check','deployment-startup','systemctl start shpakdnd-bot.service']
        # There is a SOURCE check before checkout, and a TARGET check afterwards.
        positions=[trace.index(m) if m!='deployment-check' else trace.index(m,trace.index('DB_STAGE TARGET')) for m in markers]
        self.assertEqual(positions,sorted(positions))
        self.assertEqual((self.state/'db-stage').read_text().strip(),'TARGET')

    def test_checkout_failure_allows_source_rollback_only_with_consent(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,FAKE_ROLLBACK_INCOMPATIBLE=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertEqual((self.state/'head').read_text().strip(),OLD)
        self.assertIn('Предыдущая версия восстановлена',result.stdout)
        self.assertNotIn('MIGRATION_STARTED',trace)
        self.assertNotIn('runtime --short',trace)
        self.assertIn('deployment-check',trace);self.assertIn('--rollback',trace)
        self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')

    def test_checkout_failure_without_consent_never_starts_old(self):
        result,trace=self.run_deploy('y\ny\n1\n',FAKE_CHECKOUT_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace);self.assertNotIn('MIGRATION_STARTED',trace)

    def test_failed_migration_blocks_requested_old_rollback(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_RUNTIME_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertEqual((self.state/'db-stage').read_text().strip(),'UNKNOWN')
        self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertNotIn('systemctl start',trace)
        self.assertIn('Rollback запрещён',result.stdout)
        self.assertEqual(trace.count(' switch '),1)

    def test_target_schema_failure_after_runtime_blocks_start_and_rollback(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_TARGET_SCHEMA_BAD=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace)
        self.assertEqual((self.state/'db-stage').read_text().strip(),'UNKNOWN')
        self.assertNotIn('DB_STAGE TARGET',trace)

    def test_same_schema_data_mutation_blocks_start_and_rollback(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_MIGRATION_DATA_CHANGED=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace)
        self.assertEqual((self.state/'db-stage').read_text().strip(),'UNKNOWN')

    def test_sigterm_during_live_migration_never_starts_and_invalidates_evidence(self):
        result,trace=self.run_deploy('y\ny\n',FAKE_MIGRATION_SIGNAL=1)
        self.assertEqual(result.returncode,143,result.stdout+result.stderr);self.assert_stopped()
        self.assertNotIn('systemctl start',trace)
        self.assertEqual((self.state/'db-stage').read_text().strip(),'UNKNOWN')
        reports=list((self.root/'evidence').glob('*.json'))
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0].read_text().strip(),'FAIL')

    def test_unconfirmed_source_data_blocks_checkout_failure_rollback(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_SOURCE_DATA_CHANGED=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn(' switch ',trace);self.assertNotIn('systemctl start',trace)

    def test_unknown_compatibility_blocks_even_source_rollback(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,FAKE_ROLLBACK_UNKNOWN=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace);self.assertNotIn('MIGRATION_STARTED',trace)

    def test_check_failure_does_not_start_and_code_rollback_requires_choice(self):
        result,trace=self.run_deploy('y\ny\n1\n',FAKE_RUNTIME_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn('systemctl start',trace);self.assertNotIn('-m unittest',trace[trace.index('systemctl stop'):])
        self.assertIn('Проверка не пройдена: runtime_init',result.stdout)
        self.assertEqual((self.state/'notices').read_text().strip(),'1')

    def test_confirmed_code_rollback_never_restores_database(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_TARGET_POSTCHECK_FAIL=1)
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
                for unit in ('shpakdnd-bot.service','shpakdnd-bot-watch.path'):
                    (self.state/unit).write_text('active\n')
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

    def prompt_before_answer(self,prompt,*,prefix='',answer='',**flags):
        # Real line-based redactor, fake production; no answer to this prompt is
        # written until its complete line has reached stdout. Pipes work on
        # Windows/Git Bash too, without a platform-specific pseudo-TTY.
        env=dict(self.env,FAKE_REAL_PYTHON=shell_path(sys.executable,preserve_symlink=True),PYTHONUTF8='1',
                 **{k:str(v) for k,v in flags.items()})
        command='export PATH="'+shell_path(self.bin)+':$PATH"; export FAKE_DEPLOY_PID=$$; exec bash "'+shell_path(ROOT/'deploy/deploy-shpakdnd.sh')+'"'
        process=subprocess.Popen([BASH,'--noprofile','--norc','-c',command],env=env,
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        lines=queue.Queue()
        def receive():
            for line in iter(process.stdout.readline,b''):lines.put(line)
            lines.put(None)
        reader=threading.Thread(target=receive,daemon=True);reader.start()
        received=[]
        try:
            if prefix:process.stdin.write(prefix.encode('utf-8'));process.stdin.flush()
            deadline=time.monotonic()+15
            while True:
                try:line=lines.get(timeout=max(0,deadline-time.monotonic()))
                except queue.Empty:self.fail('Prompt was not visible before input: '+prompt+'\n'+b''.join(received).decode('utf-8'))
                if line is None:self.fail('Deploy exited before prompt: '+b''.join(received).decode('utf-8'))
                received.append(line)
                if prompt.encode('utf-8') in line:
                    self.assertTrue(line.endswith(b'\n'))
                    self.assertIsNone(process.poll(),'Deploy must still be waiting for input')
                    break
            if answer:process.stdin.write(answer.encode('utf-8'));process.stdin.flush()
        finally:
            # EOF lets even the unfixed, blocked version exit safely on failure.
            process.stdin.close()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait();raise
            reader.join(timeout=5)
            process.stdout.close()
        return process.returncode

    def test_same_head_prompt_visible_before_answer_through_real_redactor(self):
        (self.state/'target').write_text(OLD+'\n')
        code=self.prompt_before_answer('Всё равно выполнить проверки/restart? [y/N]:',answer='\n')
        self.assertEqual(code,0)
        self.assertNotIn('systemctl stop',(self.state/'trace').read_text())

    def test_update_prompt_visible_before_answer_and_eof_declines(self):
        code=self.prompt_before_answer('Обновить production до origin/main? [y/N]:')
        self.assertEqual(code,0)
        self.assertNotIn('systemctl stop',(self.state/'trace').read_text())

    def test_launch_prompt_visible_before_answer_and_empty_enter_accepts(self):
        code=self.prompt_before_answer('Запустить новую версию? [Y/n]:',prefix='y\ny\n',answer='\n')
        self.assertEqual(code,0)
        self.assertEqual((self.state/'shpakdnd-bot.service').read_text().strip(),'active')
        self.assertEqual((self.state/'shpakdnd-bot-watch.path').read_text().strip(),'active')

    def test_failure_choice_visible_before_answer_and_eof_never_rolls_back(self):
        code=self.prompt_before_answer('Выбор [1]:',prefix='y\ny\n',FAKE_RUNTIME_FAIL=1)
        self.assertEqual(code,1);self.assert_stopped()
        self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertNotIn('systemctl start',(self.state/'trace').read_text())


    def test_preflight_only_never_notices_stops_switches_or_writes_live_db(self):
        result,trace=self.run_deploy(preflight=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for forbidden in ('systemctl stop','systemctl start',' switch ','telegram-deploy-notice.py',' backup ','runtime --short'):
            self.assertNotIn(forbidden,trace)
        self.assertIn('-m unittest TARGET',trace)
        self.assertEqual((self.state/'head').read_text().strip(),OLD)
        self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')
        self.assertEqual((self.state/'shpakdnd-bot.service').read_text().strip(),'active')
        self.assertEqual((self.state/'shpakdnd-bot-watch.path').read_text().strip(),'active')

    def test_every_preflight_gate_failure_keeps_services_running(self):
        for flag in ('FAKE_PREFLIGHT_FAIL','FAKE_FETCH_FAIL','FAKE_TARGET_MISSING','FAKE_DIRTY','FAKE_EVIDENCE_BAD'):
            with self.subTest(flag=flag):
                (self.state/'trace').unlink(missing_ok=True)
                result,trace=self.run_deploy('y\ny\ny\n',**{flag:1})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('systemctl stop',trace);self.assertNotIn('telegram-deploy-notice.py',trace)
                self.assertEqual((self.state/'head').read_text().strip(),OLD)
                for unit in ('shpakdnd-bot.service','shpakdnd-bot-watch.path'):
                    self.assertEqual((self.state/unit).read_text().strip(),'active')
                self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')

    def test_sigterm_during_preflight_cleans_fail_evidence_without_stop(self):
        result,trace=self.run_deploy(preflight=True,FAKE_PREFLIGHT_SIGNAL=1)
        self.assertEqual(result.returncode,143,result.stdout+result.stderr)
        self.assertNotIn('systemctl stop',trace);self.assertNotIn(' switch ',trace)
        self.assertEqual((self.state/'shpakdnd-bot.service').read_text().strip(),'active')
        evidence=list((self.root/'evidence').glob('*.json'))
        self.assertEqual(len(evidence),1);self.assertEqual(evidence[0].read_text().strip(),'FAIL')

    def test_old_sha_changed_during_checks_blocks_maintenance(self):
        result,trace=self.run_deploy('y\n',FAKE_OLD_CHANGED=1)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('systemctl stop',trace);self.assertNotIn('telegram-deploy-notice.py',trace)

    def test_new_origin_main_cannot_replace_pinned_verified_target(self):
        result,trace=self.run_deploy('y\ny\ny\n',FAKE_TARGET_CHANGED=1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotEqual((self.state/'target').read_text().strip(),TARGET)
        self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertIn(' switch --no-overwrite-ignore -C main '+TARGET,trace)

    def test_final_backup_failure_blocks_checkout_and_invalidates_pass(self):
        result,trace=self.run_deploy('y\n1\n',FAKE_BACKUP_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertNotIn(' switch ',trace);self.assertNotIn('systemctl start',trace)
        self.assertIn(' backup ',trace)
        self.assertEqual(next((self.root/'evidence').glob('*.json')).read_text().strip(),'FAIL')

    def test_incompatible_rollback_cannot_switch_or_restart_even_with_consent(self):
        result,trace=self.run_deploy('y\ny\n2\n',FAKE_TARGET_POSTCHECK_FAIL=1,FAKE_ROLLBACK_INCOMPATIBLE=1)
        self.assertNotEqual(result.returncode,0);self.assert_stopped()
        self.assertEqual(trace.count(' switch '),1);self.assertEqual((self.state/'head').read_text().strip(),TARGET)
        self.assertIn('Rollback запрещён',result.stdout)
        after_stop=trace[trace.index('systemctl stop'):]
        for long_check in ('-m unittest','compileall','check_bot.py','validators'):
            self.assertNotIn(long_check,after_stop)

    def test_repeated_preflight_reruns_checks_and_cannot_skip_gate(self):
        first,_=self.run_deploy(preflight=True)
        second,trace=self.run_deploy(preflight=True)
        self.assertEqual(first.returncode,0);self.assertEqual(second.returncode,0)
        self.assertEqual(trace.count('-m unittest TARGET'),2)
        self.assertNotIn('systemctl stop',trace)
        command='export PATH="'+shell_path(self.bin)+':$PATH"; export FAKE_DEPLOY_PID=$$; exec bash "'+shell_path(ROOT/'deploy/deploy-shpakdnd.sh')+'" --skip-preflight'
        result=subprocess.run([BASH,'-c',command],env=self.env,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,2)

    def test_concurrent_preflight_and_deploy_share_single_lock(self):
        command='export PATH="'+shell_path(self.bin)+':$PATH"; export FAKE_DEPLOY_PID=$$; exec bash "'+shell_path(ROOT/'deploy/deploy-shpakdnd.sh')+'" --preflight'
        env=dict(self.env,FAKE_CONCURRENT_LOCK='1',FAKE_PREFLIGHT_WAIT='3')
        first=subprocess.Popen([BASH,'-c',command],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+15
            while not list((self.root/'evidence').glob('*.json')):
                if first.poll() is not None:self.fail('First preflight exited before locking')
                if time.monotonic()>deadline:self.fail('First preflight never began')
                time.sleep(0.02)
            result,trace=self.run_deploy('y\n',FAKE_CONCURRENT_LOCK=1)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('systemctl stop',trace)
            output,error=first.communicate(timeout=15)
            self.assertEqual(first.returncode,0,(output+error).decode('utf-8'))
        finally:
            if first.poll() is None:first.kill();first.communicate()

    def test_pid_restarts_watcher_and_late_journal_failures_stop_unhealthy_stack(self):
        for flag in ('FAKE_PID_CHANGED','FAKE_RESTART_CHANGED','FAKE_WATCH_START_FAIL','FAKE_LATE_JOURNAL_FAIL'):
            with self.subTest(flag=flag):
                for name in ('trace','pid-queries','restart-queries'):(self.state/name).unlink(missing_ok=True)
                for unit in ('shpakdnd-bot.service','shpakdnd-bot-watch.path'):(self.state/unit).write_text('active\n')
                (self.state/'notices').write_text('0\n');(self.state/'head').write_text(OLD+'\n')
                result,trace=self.run_deploy('y\ny\ny\n1\n',**{flag:1})
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr);self.assert_stopped()
                self.assertEqual((self.state/'notices').read_text().strip(),'1')
                self.assertNotIn('-m unittest',trace[trace.index('systemctl stop'):])

    def test_lock_db_alias_and_watched_control_paths_are_rejected_before_write(self):
        alias=self.root/'alias-lock';os.link(self.project/'shpakdnd.db',alias)
        cases=[dict(SHPAKDND_LOCK=shell_path(self.project/'shpakdnd.db')),
               dict(SHPAKDND_LOCK=shell_path(alias)),
               dict(SHPAKDND_PREFLIGHT_WORK=shell_path(self.project/'app'/'temporary'))]
        for flags in cases:
            with self.subTest(flags=flags):
                (self.state/'trace').unlink(missing_ok=True)
                result,trace=self.run_deploy(preflight=True,**flags)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'unchanged fake database')
                self.assertNotIn('systemctl stop',trace);self.assertNotIn('release_preflight.py',trace)
                self.assertEqual((self.state/'shpakdnd-bot.service').read_text().strip(),'active')
        self.assertFalse((self.project/'app').exists())
