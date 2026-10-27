"""Synthetic staging acceptance: no real host, credentials, service or polling."""
from contextlib import contextmanager, closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from deploy import staging_launcher as launch, staging_inventory as inventory

ROOT = Path(__file__).resolve().parents[2]
BOT = '923456:' + 'A' * 35
OLD_BOT = 923456
SETTINGS = {'-1003883723333': {'2': {'admin': '@stage_admin', 'characters': {}, 'mini': True}},
            '-5320347021': {'0': {'admin': '@stage_admin', 'characters': {'@stage_one': 'Fixture one', '@stage_two': 'Fixture two'}}}}


class StagingAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.code = self.root / 'code'
        self.uid = os.getuid() if os.name == 'posix' else None
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(ROOT), str(self.code)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.code), 'checkout', '--detach', launch.APP_SHA], check=True, capture_output=True)
        source = (ROOT / 'deploy/staging_launcher.py').read_bytes()
        for directory in ('config', 'runtime/editorial', 'stories', 'locks'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        story = self.root / 'stories/tayna_propavshey_ovtsy.json'
        story.write_bytes((self.code / 'content/stories/unpublished/tayna_propavshey_ovtsy/tayna_propavshey_ovtsy.json').read_bytes())
        story.chmod(0o444)
        (self.root / 'config/staging-launch.py').write_bytes(source)
        self.write_json('topic-settings.json', SETTINGS)
        self.observed = dict(schema_version=1, hostname='VM-338333', observed_at=int(time.time()),
                             existing_bot_id=OLD_BOT, existing_scopes=[[-1003883723333, 2], [-5320347021, 0]],
                             saved_scopes=[[-1003883723333, 2], [-5320347021, 0]],
                             service=dict(ActiveState='active', MainPID='303'), process_ids=[303],
                             polling_inventory_complete=True, unknown_bot_processes=0)
        self.write_json('test-inventory.json', self.observed)
        text = 'BOT_TOKEN=' + BOT + '\nBOT_TIMEZONE=Europe/Moscow\n'
        text += ''.join(k + '=' + str(self.root / suffix) + '\n' for k, suffix in launch.PATHS.items())
        (self.code / '.env').write_text(text, encoding='utf-8')
        (self.code / '.env').chmod(0o600)
        self.policy = dict(schema_version=1, candidate_sha=self.git('rev-parse', 'HEAD'),
                           env_sha256=launch.sha(self.code / '.env'), topics_sha256=launch.sha(self.root / 'config/topic-settings.json'),
                           launcher_sha256=launch.sha(self.root / 'config/staging-launch.py'), expected_bot_id=923456,
                           allowed_scopes=[[-1003883723333, 2], [-5320347021, 0]], mode='replace_existing_test', tooling_sha=self.git('rev-parse', 'HEAD'))
        self.save_policy()
        (self.root / 'config/staging-launch.py').chmod(0o444)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for directory, dirs, files in os.walk(self.root):
            Path(directory).chmod(0o755)
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(0o644)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.code), *args], stderr=subprocess.PIPE).decode().strip()

    def write_json(self, name, data):
        file = self.root / 'config' / name
        if file.exists():
            file.chmod(0o644)
        file.write_text(json.dumps(data), encoding='utf-8')
        file.chmod(0o444)

    def save_policy(self):
        self.write_json('policy.json', self.policy)

    @contextmanager
    def environment(self):
        # Remove inherited isolation/test token, never inherit real credentials.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DND_CHECK_', 'RELEASE_')) and k not in ('PYTHONPATH', 'BOT_TOKEN', *launch.PATHS)}
        with patch.dict(os.environ, env, clear=True), patch.object(Path, 'cwd', return_value=self.code):
            yield

    def validate(self):
        with self.environment():
            return launch.validate(self.root / 'config/policy.json', stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333')

    def reject(self, reason):
        with self.assertRaisesRegex(launch.Blocked, reason):
            self.validate()

    def test_valid_configuration_never_opens_sqlite_or_network(self):
        with patch('sqlite3.connect', side_effect=AssertionError('SQLite forbidden')), patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            state = self.validate()
        self.assertEqual(state['bot_id'], 923456)
        self.assertEqual(set(state['settings']), {-1003883723333, -5320347021})
        self.assertFalse((self.code / 'shpakdnd.db').exists())
        with self.environment(), self.assertRaisesRegex(launch.Blocked, 'NOT_TEST_HOST'):
            launch.validate(self.root / 'config/policy.json', stage_root=self.root, trusted_uid=self.uid, hostname='VM-436996')

    def test_policy_duplicate_keys_unknown_fields_and_boolean_versions(self):
        for update in (dict(schema_version=True), dict(schema_version=1.0), dict(unknown=True), dict(mode='parallel_unapproved')):
            previous = dict(self.policy)
            self.policy.update(update)
            self.save_policy()
            with self.assertRaises(launch.Blocked):
                self.validate()
            self.policy = previous
        self.save_policy()
        file = self.root / 'config/policy.json'
        file.chmod(0o644)
        file.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
        file.chmod(0o444)
        self.reject('DUPLICATE')

    def test_wrong_path_cwd_and_symlink_rejected_before_app_import(self):
        with self.environment(), patch.object(Path, 'cwd', return_value=ROOT), self.assertRaisesRegex(launch.Blocked, 'CWD'):
            launch.validate(self.root / 'config/policy.json', stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333')
        with self.assertRaisesRegex(launch.Blocked, 'PATH'):
            launch.absolute(Path('relative'))
        env = self.code / '.env'
        previous = env.read_text(encoding='utf-8')
        env.write_text(previous.replace(str(self.root / 'stories'), '/var/lib/shpakdnd/stories'), encoding='utf-8')
        self.policy['env_sha256'] = launch.sha(env)
        self.save_policy()
        self.reject('RUNTIME_PATH')
        with patch.object(Path, 'is_symlink', return_value=True), self.assertRaisesRegex(launch.Blocked, 'SYMLINK'):
            launch.absolute(self.root)

    def test_wrong_token_environment_override_and_existing_test_identity(self):
        with self.environment(), patch.dict(os.environ, BOT_TOKEN='different-private-value'), self.assertRaisesRegex(launch.Blocked, 'TOKEN_OVERRIDE'):
            launch.validate(self.root / 'config/policy.json', stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333')
        self.policy['expected_bot_id'] = 823456
        file = self.code / '.env'
        file.write_text(file.read_text(encoding='utf-8').replace(BOT, str(823456) + ':' + 'B' * 35), encoding='utf-8')
        self.policy['env_sha256'] = launch.sha(file)
        self.save_policy()
        self.reject('EXISTING_TEST_TOKEN')
        file.write_text(file.read_text(encoding='utf-8').replace(str(823456) + ':' + 'B' * 35, 'malformed-token'), encoding='utf-8')
        self.policy['env_sha256'] = launch.sha(file)
        self.save_policy()
        self.reject('TOKEN_FORMAT')

    def test_scope_whitelist_blocks_production_aliases_and_captured_test_chats(self):
        for settings in ({'-1003376315265': SETTINGS['-1003883723333']},
                         {'-0800000001': SETTINGS['-1003883723333']},
                         {'-1003883723333': {'2': dict(SETTINGS['-1003883723333']['2'], mini='true')}}):
            with self.subTest(settings=settings), self.assertRaises(launch.Blocked):
                launch.topics(settings, {(-1003883723333, 2), (-5320347021, 0)})
        self.policy['allowed_scopes'] = [[-700000001, 2], [-700000002, 0]]
        self.save_policy()
        self.reject('TEST_SCOPE_MISMATCH')

    def test_stale_future_partial_and_unknown_inventory_cannot_authorize_start(self):
        for update in (dict(observed_at=int(time.time()) - 301), dict(observed_at=int(time.time()) + 60),
                       dict(observed_at=True), dict(polling_inventory_complete=False), dict(unknown_bot_processes=1),
                       dict(schema_version=True), dict(service={'ActiveState': 'inactive'})):
            with self.subTest(update=update):
                self.write_json('test-inventory.json', dict(self.observed, **update))
                with self.assertRaises(launch.Blocked):
                    self.validate()
        self.write_json('test-inventory.json', self.observed)

    def test_changed_source_launcher_env_and_topics_fail_closed(self):
        for file in (self.code / '.env', self.root / 'config/staging-launch.py', self.root / 'config/topic-settings.json', self.root / 'stories/tayna_propavshey_ovtsy.json'):
            original = file.read_bytes()
            mode = file.stat().st_mode & 0o777
            file.chmod(0o644)
            file.write_bytes(original + b' ')
            file.chmod(mode)
            with self.subTest(file=file.name), self.assertRaises(launch.Blocked):
                self.validate()
            file.chmod(0o644)
            file.write_bytes(original)
            file.chmod(mode)
        (self.code / 'bot.py').write_bytes((self.code / 'bot.py').read_bytes() + b'\n# dirty\n')
        self.reject('CODE_CHANGED')

    def test_private_policy_permissions_and_writable_parent(self):
        if os.name == 'posix':
            policy = self.root / 'config/policy.json'
            policy.chmod(0o644)
            self.reject('PERMISSIONS')
            policy.chmod(0o444)
            parent = policy.parent
            parent.chmod(0o777)
            self.reject('PARENT_PERMISSIONS')
            parent.chmod(0o755)
        else:
            with patch.object(Path, 'is_symlink', return_value=True), self.assertRaises(launch.Blocked):
                launch.trusted(self.root / 'config/policy.json', self.uid)

    def test_poll_without_separate_approval_never_imports_app_or_opens_database(self):
        with self.environment(), patch.object(launch, 'inject') as inject, patch.object(launch.runpy, 'run_path') as run:
            self.assertEqual(launch.main(['--poll'], stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333'), 2)
            inject.assert_not_called()
            run.assert_not_called()
        self.assertFalse((self.code / 'shpakdnd.db').exists())

    def test_same_bot_lock_blocks_concurrent_start_without_real_polling(self):
        if os.name != 'posix':
            with self.environment(), patch.object(launch, 'inject') as inject:
                self.assertEqual(launch.main(['--poll'], stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333'), 2)
                inject.assert_not_called()
            return
        self.write_json('test-inventory.json', dict(self.observed, service=dict(ActiveState='inactive', MainPID='0'), process_ids=[]))
        state = self.validate()
        self.write_json('start-approval.json', dict(approved_at=int(time.time()), candidate_sha=self.policy['candidate_sha'],
                        fingerprint=state['fingerprint'], polling_approved=True, bot_id=923456))
        lock = self.root / 'locks/polling-923456.lock'
        lock.write_bytes(b'')
        lock.chmod(0o660)
        attempts = []
        def fake_bot(*args, **kwargs):
            attempts.append(launch.main(['--poll'], stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333'))
        with self.environment(), patch.object(launch, 'inject'), patch.object(launch, 'no_other_polling'), patch.object(launch.runpy, 'run_path', side_effect=fake_bot):
            self.assertEqual(launch.main(['--poll'], stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333'), 0)
        self.assertEqual(attempts, [2])

    def test_live_process_guard_rejects_other_bot_and_ignores_self(self):
        proc = self.root / 'process-probe'
        (proc / '912345').mkdir(parents=True)
        (proc / '912345/cmdline').write_bytes(b'python\0/old/bot.py\0')
        with self.assertRaisesRegex(launch.Blocked, 'OTHER_POLLING'):
            launch.no_other_polling(proc)
        with patch.object(launch.os, 'getpid', return_value=912345):
            launch.no_other_polling(proc)

    def test_poll_active_legacy_is_rejected_even_with_fresh_approval(self):
        state = self.validate()
        self.write_json('start-approval.json', dict(approved_at=int(time.time()), candidate_sha=self.policy['candidate_sha'],
                        fingerprint=state['fingerprint'], polling_approved=True, bot_id=923456))
        with self.environment(), patch.object(launch, 'inject') as inject:
            self.assertEqual(launch.main(['--poll'], stage_root=self.root, trusted_uid=self.uid, hostname='VM-338333'), 2)
            inject.assert_not_called()

    def child(self, tail):
        program = '''import importlib.util,os,sys
from pathlib import Path
path=Path(sys.argv[1]);root=Path(sys.argv[2]);os.chdir(root/'code')
spec=importlib.util.spec_from_file_location('external_stage',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
uid=os.getuid() if os.name=='posix' else None
''' + tail
        with self.environment():
            return subprocess.run([sys.executable, '-c', program, str(self.root / 'config/staging-launch.py'), str(self.root)], capture_output=True, text=True, encoding='utf-8', timeout=60, cwd=self.code)

    def test_actual_check_bot_adapter_scopes_migrations_and_source_unchanged(self):
        before = {name: (self.code / name).read_bytes() for name in ('app/topics.py', 'app/mini/worlds.py', 'bot.py', 'check_bot.py')}
        for number in range(3):
            proc = self.child("raise SystemExit(module.main(['--check'],stage_root=root,trusted_uid=uid,hostname='VM-338333'))\n")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertNotIn(BOT, proc.stdout + proc.stderr)
        with closing(sqlite3.connect(self.code / 'shpakdnd.db')) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT chat_id,thread_id FROM mini_worlds').fetchall(), [(-1003883723333, 2)])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mini_heroes').fetchone()[0], 79)
            self.assertEqual(conn.execute('SELECT SUM(active) FROM mini_heroes').fetchone()[0], 78)
            self.assertEqual(conn.execute("SELECT active FROM mini_heroes WHERE code='panic_dungeon_engineer'").fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mini_players').fetchone()[0], 0)
        self.assertEqual(before, {name: (self.code / name).read_bytes() for name in before})
        self.assertFalse(self.git('status', '--porcelain'))

    def test_real_bot_import_captures_staging_mapping_without_polling(self):
        proc = self.child('''from unittest.mock import patch
state=module.validate(root/'config/policy.json',stage_root=root,trusted_uid=uid,hostname='VM-338333')
module.inject(state)
with patch('aiogram.Bot') as bot:
 data=module.runpy.run_path(str(root/'code/bot.py'),run_name='staging_import_probe')
 bot.assert_not_called()
import app.topics as topics,app.context as context,app.mini.worlds as worlds
assert topics.TOPIC_SETTINGS is context.TOPIC_SETTINGS is worlds.TOPIC_SETTINGS
assert set(topics.TOPIC_SETTINGS)=={-1003883723333,-5320347021}
assert data['sync_configured_mini_worlds'] is worlds.sync_configured_mini_worlds
print('BOT_IMPORT_SCOPES_PASS_NO_POLLING')
''')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('BOT_IMPORT_SCOPES_PASS_NO_POLLING', proc.stdout)
        self.assertNotIn(BOT, proc.stdout + proc.stderr)

    def test_early_world_import_is_rejected_instead_of_replacing_stale_alias(self):
        with patch.dict(sys.modules, {'app.mini.worlds': types.ModuleType('app.mini.worlds')}), self.assertRaisesRegex(launch.Blocked, 'ALREADY_IMPORTED'):
            launch.inject(dict(code=self.code, settings={}))

    def inventory_fixture(self):
        project = self.root / 'legacy'
        (project / 'app').mkdir(parents=True, exist_ok=True)
        (project / 'app/topics.py').write_text('TOPIC_SETTINGS = ' + repr({-1003883723333: {2: {'admin': '@stage_admin', 'mini': True}}, -5320347021: {0: {'admin': '@stage_admin'}}}), encoding='utf-8')
        (project / '.env').write_text('BOT_TOKEN=' + str(OLD_BOT) + ':' + 'B' * 35 + '\n', encoding='utf-8')
        (project / 'shpakdnd.db').write_bytes(b'NOT_SQLITE_MUST_NOT_BE_OPENED')
        saved = self.root / 'saved-topics.py'
        saved.write_bytes((project / 'app/topics.py').read_bytes())
        proc = self.root / 'proc'
        (proc / '303').mkdir(parents=True, exist_ok=True)
        (proc / '303/cmdline').write_bytes(b'python\0bot.py\0')
        props = dict(ActiveState='active', SubState='running', MainPID='303', NRestarts='0', User='fixture', WorkingDirectory=str(project), FragmentPath='/fixture/unit')
        def run(*args):
            if args[:2] == ('systemctl', 'cat'):
                return '[Service]\nWorkingDirectory=' + str(project)
            if args[0] == 'git':
                return 'a' * 40 if 'rev-parse' in args else ' M app/topics.py'
            key = args[args.index('-p') + 1]
            return props.get(key, '')
        return project, saved, proc, run

    def test_inventory_is_read_only_redacted_and_captures_legacy_local_topics(self):
        project, saved, proc, run = self.inventory_fixture()
        resolve = Path.resolve
        before = {name: (project / name).read_bytes() for name in ('.env', 'shpakdnd.db', 'app/topics.py')}
        def resolved(path, *args, **kwargs):
            return project if path == proc / '303/cwd' else resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolved), patch('sqlite3.connect', side_effect=AssertionError('live DB open')), patch('socket.socket.connect', side_effect=AssertionError('network')):
            result = inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=run, saved_topics=saved)
        self.assertEqual(result['existing_bot_id'], OLD_BOT)
        self.assertEqual(result['git_status'], ' M app/topics.py')
        self.assertFalse(result['sqlite_opened'])
        self.assertNotIn('B' * 35, json.dumps(result))
        self.assertEqual(before, {name: (project / name).read_bytes() for name in before})
        with self.assertRaisesRegex(ValueError, 'NOT_TEST_HOST'):
            inventory.inventory(hostname='VM-436996', project=project, proc=proc, run=run, saved_topics=saved)

    def test_inventory_unknown_polling_and_unit_token_override_block(self):
        project, saved, proc, run = self.inventory_fixture()
        resolve = Path.resolve
        with patch.object(Path, 'resolve', lambda p, *a, **k: project if p.name == 'cwd' else resolve(p, *a, **k)):
            (proc / '304').mkdir()
            (proc / '304/cmdline').write_bytes(b'python\0bot.py\0')
            with self.assertRaisesRegex(ValueError, 'POLLING_INVENTORY'):
                inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=run, saved_topics=saved)
            shutil.rmtree(proc / '304')
            def override(*args):
                if 'Environment' in args:
                    return 'BOT_TOKEN=123456:' + 'C' * 35
                return run(*args)
            with self.assertRaisesRegex(ValueError, 'TOKEN_OVERRIDE'):
                inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=override, saved_topics=saved)

    def test_stopped_inventory_preserves_approved_legacy_db_link(self):
        project, saved, proc, run = self.inventory_fixture()
        shutil.rmtree(proc / '303')
        def stopped(*args):
            if 'ActiveState' in args:
                return 'inactive'
            if 'MainPID' in args:
                return '0'
            return run(*args)
        target = project / 'shpakdnd.db'
        if os.name == 'posix':
            target.rename(self.root / 'old-test.db')
            target.symlink_to(self.root / 'old-test.db')
        result = inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=stopped,
                                     saved_topics=saved, legacy_db=self.root / 'old-test.db')
        self.assertEqual(result['process_ids'], [])
        self.assertEqual(result['service']['ActiveState'], 'inactive')
        self.assertFalse(result['sqlite_opened'])
        if os.name == 'posix':
            with self.assertRaisesRegex(ValueError, 'DB_LINK'):
                inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=stopped,
                                    saved_topics=saved, legacy_db=self.root / 'different.db')

    def test_inventory_accepts_only_verified_saved_dnd_addition(self):
        project, saved, proc, run = self.inventory_fixture()
        full = inventory.literal_topics(saved)
        mini_only = {-1003883723333: full[-1003883723333]}
        (project / 'app/topics.py').write_text('TOPIC_SETTINGS = ' + repr(mini_only), encoding='utf-8')
        resolve = Path.resolve
        with patch.object(Path, 'resolve', lambda p, *a, **k: project if p.name == 'cwd' else resolve(p, *a, **k)):
            result = inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=run, saved_topics=saved)
            self.assertEqual(result['existing_scopes'], [[-1003883723333, 2]])
            self.assertEqual(result['saved_scope_additions'], [[-5320347021, 0]])
            self.assertFalse(result['active_topics_match_saved'])
            self.assertFalse(result['sqlite_opened'])
            changed = {-1003883723333: {2: dict(full[-1003883723333][2], admin='@other_admin')}, -5320347021: full[-5320347021]}
            saved.write_text('TOPIC_SETTINGS = ' + repr(changed), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'SAVED_TOPICS_DIFFER'):
                inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=run, saved_topics=saved)
            saved.write_text('TOPIC_SETTINGS = ' + repr(mini_only), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'SAVED_SCOPES_CHANGED'):
                inventory.inventory(hostname='VM-338333', project=project, proc=proc, run=run, saved_topics=saved)

    def test_launcher_distinguishes_active_mini_from_approved_saved_dnd(self):
        self.write_json('test-inventory.json', dict(self.observed, existing_scopes=[[-1003883723333, 2]]))
        self.assertEqual(set(self.validate()['settings']), {-1003883723333, -5320347021})
        for update in (dict(saved_scopes=[]), dict(saved_scopes=[[-1003376315265, 3]]),
                       dict(existing_scopes=[[-5320347021, 0]]), dict(existing_scopes=[]),
                       dict(existing_scopes=[[-1003883723333, 2], [-1003883723333, 2]]),
                       dict(saved_scopes=[[-1003883723333, True], [-5320347021, 0]])):
            self.write_json('test-inventory.json', dict(self.observed, **update))
            self.reject('TEST_SCOPE_MISMATCH')

    def test_documented_clone_into_precreated_directory_and_bash_syntax(self):
        parent = self.root / 'prepared'
        code = parent / 'code'
        code.mkdir(parents=True, mode=0o750)
        self.assertEqual(list(code.iterdir()), [])
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(ROOT), str(code)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(code), 'checkout', '--detach', launch.APP_SHA], check=True, capture_output=True)
        self.assertEqual(subprocess.check_output(['git', '-C', str(code), 'rev-parse', 'HEAD'], text=True).strip(), launch.APP_SHA)
        self.assertFalse((code / 'shpakdnd.db').exists())
        document = (ROOT / 'docs/v1.5/STAGING_ACCEPTANCE.md').read_text(encoding='utf-8')
        blocks = re.findall(r'```bash\n(.*?)```', document, re.S)
        self.assertGreaterEqual(len(blocks), 4)
        if os.name == 'posix':
            for block in blocks:
                result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        else:
            # Windows verifies actual clone/filesystem commands above; POSIX CI parses bash blocks.
            self.assertLess(document.index('install -d -o root -g shpak-stage -m 750 "$STAGE"'), document.index('runuser -u shpak-stage -- git clone'))

    def test_authored_acceptance_scenarios_are_retained_and_story_command_is_isolated(self):
        baseline = subprocess.check_output(['git', 'show', 'd795df06207d3f6927dee3378be7cf02fc25b93e:docs/v1.5/STAGING_ACCEPTANCE.md'], cwd=ROOT).decode('utf-8')
        current = (ROOT / 'docs/v1.5/STAGING_ACCEPTANCE.md').read_text(encoding='utf-8')
        marker = '## Правила протокола и SQL-доказательств'
        self.assertEqual(baseline[baseline.index(marker):], current[current.index(marker):])
        self.assertIn('--label staging-story --', current)
        external = self.root / 'empty-external-stories'
        external.mkdir()
        proc = subprocess.run([sys.executable, 'scripts/run_isolated_checks.py', '--label', 'staging-negative-story', '--', 'tools/validate_release_content.py', '--directory', str(external)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=25)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn('BLOCKED', proc.stdout)
