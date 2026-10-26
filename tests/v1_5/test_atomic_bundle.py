"""Atomic installer failures use only caller-owned temporary filesystems."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from deploy import install_bundle as bundle, release_preflight
from tests.v1_3_3 import test_deploy_shell as shell
from tests.v1_4_3_1 import test_deploy_confirm as pty

ROOT = Path(__file__).resolve().parents[2]
SHA = 'a' * 40
NEXT = 'b' * 40
POSIX = os.name == 'posix'


class Interrupted(Exception):
    pass


class AtomicBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dest = self.root / 'lib' / 'shpakdnd-deploy'
        self.entry = self.root / 'bin' / 'deploy-shpakdnd'
        self.entry.parent.mkdir()
        self.dest.mkdir(parents=True)
        self.uid = os.getuid() if POSIX else None
        self.snapshot = {n: (ROOT / 'deploy' / n).read_bytes() for n in bundle.FILES}
        self.legacy = self.dest / 'deploy-shpakdnd.sh'
        self.legacy.write_bytes(b'legacy-unchanged')
        if POSIX:
            self.entry.symlink_to(self.legacy)
        self.before = self.legacy.read_bytes()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Windows read-only file flags and POSIX immutable release modes need
        # restoring on OUR disposable tree before TemporaryDirectory cleanup.
        for directory, dirs, files in os.walk(self.root):
            Path(directory).chmod(0o755)
            for name in files:
                p = Path(directory) / name
                if not p.is_symlink():
                    p.chmod(0o644)
        self.tmp.cleanup()

    def install(self, sha=SHA, snapshot=None, event=lambda phase: None):
        return bundle.install(snapshot or self.snapshot, sha, self.dest, self.entry, self.uid, event)

    def old_preserved(self):
        self.assertEqual(self.legacy.read_bytes(), self.before)
        if POSIX:
            self.assertEqual(os.readlink(self.entry), str(self.legacy))
        else:
            self.assertFalse(self.entry.exists())

    def publish(self, sha=SHA):
        if POSIX:
            return self.install(sha)
        # This host has no symlink privilege. Assert the native activation
        # refusal rather than add a skip or pretend it is Linux. Preparation,
        # byte validation, faults and recovery still execute on this host.
        with self.assertRaises(OSError):
            self.install(sha)
        self.old_preserved()
        return self.dest / 'releases' / sha

    def test_success_legacy_transition_complete_hashes_and_modes(self):
        version = self.publish()
        bundle.validate(version, self.snapshot, SHA, self.uid)
        self.assertEqual(set(p.name for p in version.iterdir()), set(bundle.FILES) | {'launch.sh', 'bundle.json'})
        self.assertEqual(self.legacy.read_bytes(), self.before)
        if POSIX:
            self.assertEqual(self.entry.resolve(), version / 'launch.sh')
            self.assertEqual(version.stat().st_mode & 0o777, 0o555)

    def test_same_bundle_reinstallation_is_idempotent(self):
        version = self.publish()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in version.iterdir()}
        self.publish()
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in version.iterdir()})
        self.assertEqual(list((self.dest / 'releases').glob('.staging-*')), [])

    def test_interruption_after_every_file_and_phase_preserves_entry_and_recovers(self):
        phases = ['prepared_directories'] + ['copied:' + n for n in bundle.FILES] + ['manifest', 'launcher', 'validated', 'published', 'before_switch']
        for phase in phases:
            with self.subTest(phase=phase):
                # A fresh SHA per case ensures even post-publish paths execute.
                sha = hashlib.sha1(phase.encode()).hexdigest()
                def interrupt(value):
                    if value == phase:
                        raise Interrupted(phase)
                if POSIX or phase != 'before_switch':
                    with self.assertRaises(Interrupted):
                        self.install(sha, event=interrupt)
                else:
                    with self.assertRaises(OSError):
                        self.install(sha, event=interrupt)
                self.old_preserved()
                # Recovery verifies any already-published directory; never
                # reuses incomplete stages. Reset legacy only in our fixture.
                self.publish(sha)
                if POSIX:
                    self.entry.unlink()
                    self.entry.symlink_to(self.legacy)

    def test_missing_corrupt_and_extra_component_are_rejected(self):
        for kind in ('missing', 'corrupt', 'extra'):
            sha = hashlib.sha1(kind.encode()).hexdigest()
            version = self.publish(sha)
            version.chmod(0o755)
            target = version / 'release_guard.py'
            target.chmod(0o644)
            if kind == 'missing':
                target.unlink()
            elif kind == 'corrupt':
                target.write_bytes(b'corrupt')
                target.chmod(0o444)
            else:
                target.chmod(0o444)
                (version / 'unexpected-executable').write_bytes(b'bad')
            version.chmod(0o555)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                bundle.validate(version, self.snapshot, sha, self.uid)

    def test_manifest_hash_and_launcher_tamper_are_rejected(self):
        for name in ('bundle.json', 'launch.sh'):
            sha = hashlib.sha1(name.encode()).hexdigest()
            version = self.publish(sha)
            file = version / name
            file.chmod(0o644)
            file.write_bytes(file.read_bytes() + b' ')
            file.chmod(0o555 if name == 'launch.sh' else 0o444)
            with self.assertRaisesRegex(ValueError, 'HASH'):
                bundle.validate(version, self.snapshot, sha, self.uid)

    def test_low_space_and_write_permission_error_leave_previous(self):
        with patch.object(bundle.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(1, 1, 0)):
            with self.assertRaisesRegex(OSError, 'SPACE'):
                self.install()
        self.old_preserved()
        with patch.object(bundle, 'write_file', side_effect=PermissionError('fixture denied')):
            with self.assertRaises(PermissionError):
                self.install()
        self.old_preserved()

    def test_atomic_replace_error_keeps_old_entrypoint_and_new_complete_version(self):
        with patch.object(bundle.os, 'replace', side_effect=PermissionError('switch refused')):
            with self.assertRaises(OSError):
                self.install()
        self.old_preserved()
        bundle.validate(self.dest / 'releases' / SHA, self.snapshot, SHA, self.uid)
        self.publish()

    def test_wrong_link_and_parent_symlink_fail_closed(self):
        if POSIX:
            self.entry.unlink()
            self.entry.symlink_to(self.root / 'unknown')
            with self.assertRaisesRegex(ValueError, 'LINK'):
                self.install()
            self.entry.unlink()
            self.entry.symlink_to(self.legacy)
            outside = self.root / 'outside'
            outside.mkdir()
            (self.dest / 'releases').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'SYMLINK'):
                self.install()
        else:
            with patch.object(Path, 'is_symlink', return_value=True), self.assertRaisesRegex(ValueError, 'SYMLINK'):
                self.install()
        self.old_preserved()

    def test_partial_snapshot_and_unapproved_sha_are_rejected(self):
        bad = dict(self.snapshot)
        del bad['release_guard.py']
        with self.assertRaisesRegex(ValueError, 'SNAPSHOT'):
            self.install(snapshot=bad)
        with self.assertRaises(ValueError):
            self.install('not-reviewed')
        self.old_preserved()

    def test_version_never_overwritten_if_content_changed_for_same_sha(self):
        self.publish()
        changed = dict(self.snapshot, **{'release_guard.py': b'bad'})
        with self.assertRaisesRegex(ValueError, 'HASH'):
            self.install(snapshot=changed)
        self.assertEqual((self.dest / 'releases' / SHA / 'release_guard.py').read_bytes(), self.snapshot['release_guard.py'])

    def test_previous_versions_are_retained(self):
        old = self.publish()
        new = self.publish(NEXT)
        self.assertNotEqual(old, new)
        bundle.validate(old, self.snapshot, SHA, self.uid)
        bundle.validate(new, self.snapshot, NEXT, self.uid)
        self.publish(SHA)
        if POSIX:
            self.assertEqual(self.entry.resolve(), old / 'launch.sh')
        self.assertEqual(self.legacy.read_bytes(), self.before)

    def test_source_git_sha_bytes_and_symlink_validation(self):
        # Tiny isolated Git repository with actual source entries, not mocked
        # git output; no user checkout or protected branch mutations.
        source = self.root / 'source'
        deploy = source / 'deploy'
        deploy.mkdir(parents=True)
        for name in bundle.SOURCE_FILES:
            (deploy / name).write_bytes((ROOT / 'deploy' / name).read_bytes())
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.PIPE).decode().strip()
        git('init')
        git('add', 'deploy')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
        sha = git('rev-parse', 'HEAD')
        self.assertEqual(bundle.source_snapshot(deploy, sha), self.snapshot)
        with self.assertRaises(ValueError):
            bundle.source_snapshot(deploy, '0' * 40)
        (deploy / 'release_guard.py').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'MODIFIED'):
            bundle.source_snapshot(deploy, sha)

    def test_real_flock_blocks_install_and_deployment_same_lock(self):
        if POSIX:
            lock = self.root / 'deploy.lock'
            with bundle.deploy_lock(lock, self.uid):
                with self.assertRaises(BlockingIOError):
                    with bundle.deploy_lock(lock, self.uid):
                        self.fail('second installer entered')
                result = subprocess.run(['flock', '-n', str(lock), 'true'], capture_output=True)
                self.assertNotEqual(result.returncode, 0, 'deployment flock must also fail')
            with bundle.deploy_lock(lock, self.uid):
                pass
            lock.unlink()
            lock.symlink_to(self.root / 'outside-lock')
            with self.assertRaises(OSError):
                with bundle.deploy_lock(lock, self.uid):
                    self.fail('unsafe lock followed')
        else:
            self.assertIn('shpakdnd-deploy.lock', (ROOT / 'deploy/deploy-shpakdnd.sh').read_text(encoding='utf-8'))
            self.assertIn('shpakdnd-deploy.lock', (ROOT / 'deploy/install_bundle.py').read_text(encoding='utf-8'))

    def test_runtime_verifier_detects_hash_mode_and_extra_tampering(self):
        version = self.publish()
        if POSIX:
            def verify():
                return subprocess.run([str(version / 'launch.sh'), '--help'], capture_output=True)
            self.assertEqual(verify().returncode, 0)
            helper = version / 'release_guard.py'
            helper.chmod(0o644)
            helper.write_bytes(b'bad')
            helper.chmod(0o444)
            self.assertNotEqual(verify().returncode, 0)
            helper.chmod(0o644)
            helper.write_bytes(self.snapshot['release_guard.py'])
            self.assertNotEqual(verify().returncode, 0, 'writable helper')
            helper.chmod(0o444)
            version.chmod(0o755)
            (version / 'extra').write_bytes(b'bad')
            version.chmod(0o555)
            self.assertNotEqual(verify().returncode, 0)
        else:
            self.assertIn(b'/usr/bin/python3 -I -B', (version / 'launch.sh').read_bytes())
            self.assertIn(bundle.digest((version / 'bundle.json').read_bytes()).encode(), (version / 'launch.sh').read_bytes())

    def test_signal_exit_after_publish_and_switch_leaves_only_complete_versions(self):
        if POSIX:
            import multiprocessing
            for phase in ('published', 'activated'):
                sha = hashlib.sha1(phase.encode()).hexdigest()
                def child():
                    def signal_exit(value):
                        if value == phase:
                            os._exit(73)
                    self.install(sha, event=signal_exit)
                process = multiprocessing.get_context('fork').Process(target=child)
                process.start()
                process.join(15)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 73)
                version = self.dest / 'releases' / sha
                bundle.validate(version, self.snapshot, sha, self.uid)
                if phase == 'published':
                    self.old_preserved()
                else:
                    self.assertEqual(self.entry.resolve(), version / 'launch.sh')
                self.publish(sha)
                self.entry.unlink()
                self.entry.symlink_to(self.legacy)
        else:
            # Verify the same post-rename invariants through an injected abort;
            # native process/fork and symlink semantics are exercised on Linux.
            def abort(value):
                if value == 'published':
                    raise Interrupted(value)
            with self.assertRaises(Interrupted):
                self.install(event=abort)
            self.old_preserved()
            bundle.validate(self.dest / 'releases' / SHA, self.snapshot, SHA, self.uid)

    def test_ci_retains_required_gates_and_diagnostic_hook_propagates_failure(self):
        current = (ROOT / '.github/workflows/linux-release.yml').read_text(encoding='utf-8')
        baseline = subprocess.check_output(['git', 'show', 'fc1c65a6bd7a9dd7a9bbd9f23ae71ad0849bc1fd:.github/workflows/linux-release.yml'], cwd=ROOT).decode()
        for line in baseline.splitlines():
            if line.startswith('      - name:'):
                self.assertIn(line, current)
        self.assertNotIn('continue-on-error', current)
        self.assertIn('timeout-minutes: 60', current)
        self.assertIn('python-version: ["3.11", "3.13"]', current)
        command = 'export RUNNER_TEMP="' + shell.shell_path(self.root) + '" GITHUB_ACTION=negative-fixture; source "' + shell.shell_path(ROOT / 'scripts/ci_step_diagnostics.sh') + '"; false'
        proc = subprocess.run([shell.BASH, '--noprofile', '--norc', '-e', '-c', command], capture_output=True)
        self.assertEqual(proc.returncode, 1)
        timings = (self.root / 'release-diagnostics/timings.tsv').read_text(encoding='utf-8')
        self.assertIn('negative-fixture\t1\t', timings)
        self.assertTrue(list((self.root / 'release-diagnostics').glob('*.log')))

    def test_frozen_bundle_is_complete_and_byte_compatible(self):
        version = self.publish()
        candidate = self.root / 'candidate'
        shutil.copytree(ROOT / 'deploy', candidate / 'deploy')
        with patch.object(release_preflight, '__file__', str(version / 'release_preflight.py')):
            release_preflight.validate_frozen_bundle(candidate)
            (candidate / 'deploy/release_guard.py').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'MISMATCH'):
                release_preflight.validate_frozen_bundle(candidate)

    def test_installed_entrypoint_dry_preflight_and_failed_gate_keep_sentinels(self):
        version = self.publish()
        harness = shell.DeployShellTests('test_shell_syntax_and_dry_run_no_production_mutation')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.script = version / 'deploy-shpakdnd.sh'
        if POSIX:
            # Root owner is pinned for production; fixture uid is pinned only
            # in this generated launcher. Fake id/systemd never touch the host.
            harness.script = self.entry
        initial = {n: (harness.project / n).read_bytes() for n in ('.env', 'shpakdnd.db')}
        for kwargs, expected in ((dict(dry=True), 0), (dict(preflight_only=True), 0), (dict(preflight_only=True, FAKE_PREFLIGHT_FAIL='1'), 1)):
            result, trace = harness.run_deploy(**kwargs)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            for forbidden in ('systemctl stop', 'systemctl start', 'git switch', ' backup '):
                self.assertNotIn(forbidden, trace)
        self.assertEqual(initial, {n: (harness.project / n).read_bytes() for n in initial})
        self.assertFalse((harness.root / 'external-stories').exists())
        for name in ('release_guard.py', 'deploy-shpakdnd.sh'):
            self.assertEqual(self.snapshot[name], (version / name).read_bytes())

    def test_installed_entrypoint_preserves_controlling_tty_and_rollback_guard(self):
        version = self.publish()
        if POSIX:
            harness = shell.DeployShellTests('test_shell_syntax_and_dry_run_no_production_mutation')
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            harness.script = self.entry
            harness.env.update(FAKE_V15_GUARD_FAIL='1', FAKE_CHECK_FAIL='1')
            terminal = pty.Terminal(harness, redirect_stdin=True)
            self.addCleanup(terminal.close)
            terminal.eof_stdin()
            terminal.wait_text('\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c')
            terminal.send('y\n')
            terminal.wait_text('\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c')
            terminal.send('y\n')
            terminal.wait_text('\u0412\u044b\u0431\u043e\u0440 [1]')
            terminal.send('2\n')
            self.assertNotEqual(terminal.finish(), 0)
            self.assertIn('BLOCKED_V15_ROLLBACK', terminal.text)
            harness.assert_stopped()
        else:
            # Portable frozen contract; real controlling-terminal branch above
            # must execute on both Linux matrix entries, without a new skip.
            self.assertEqual((version / 'deploy-shpakdnd.sh').read_bytes(), self.snapshot['deploy-shpakdnd.sh'])
