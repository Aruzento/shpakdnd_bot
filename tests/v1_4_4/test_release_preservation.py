"""Strict preservation checks for the pre-existing Mini publication backfill."""
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.db import schema
from app.mini.migrations.v1_4_2_ui import apply
from app.mini.schema import init_mini_db
from app.mini.players import create_mini_player
from app.mini.worlds import register_mini_world
from tools import validate_dnd_shop_release as release


class PublicationModelTests(unittest.TestCase):
    def before(self, shares=(), publications=()):
        return {
            'mini_public_shares': (['player_id', 'operation_key'], list(shares)),
            'mini_ui_publications': (['player_id', 'operation_key', 'status'], list(publications)),
        }

    def test_exact_three_and_insert_ignore_collisions(self):
        before = self.before([(1, 'share:2:3'), (1, 'ordinary'), (1, 'share:2.3')],
                             [(1, 'keep', 'published')])
        additions = release.publication_additions(before)
        self.assertEqual(additions, {(1, 'share:2:3', 'sending'),
                                    (1, 'share:2.3', 'sending'), (1, 'ordinary', 'sending')})
        original = before['mini_ui_publications'][1]
        release.assert_publications(original + list(additions), original, additions)

    def test_existing_status_is_not_overwritten_and_players_are_distinct(self):
        before = self.before([(1, 'share:2:3'), (2, 'share:2:3')],
                             [(1, 'share:2:3', 'published'), (1, 'share:2.3', 'failed')])
        self.assertEqual(release.publication_additions(before),
                         {(2, 'share:2:3', 'sending'), (2, 'share:2.3', 'sending')})

    def test_compatibility_rule_is_exact(self):
        before = self.before([(1, k) for k in ('share:a:b:c', 'Share:a:b', 'share:', 'share:a')])
        self.assertEqual(len(release.publication_additions(before)), 4)

    def test_new_tables_are_explicit_and_cannot_gain_economic_rows(self):
        with self.assertRaises(AssertionError):
            release.new_shop_tables({}, {'unreviewed': (['id'], [])})
        with self.assertRaises(AssertionError):
            release.new_shop_tables({}, {'dnd_shop_credit': (['balance'], [('1',)])})
        with self.assertRaises(AssertionError):
            release.new_shop_tables({'removed': ([], [])}, {})
        self.assertEqual(release.new_shop_tables({}, {'dnd_shop_credit': (['balance'], [])}),
                         {'dnd_shop_credit': (['balance'], [])})

    def test_type_changes_are_not_hidden_by_python_numeric_equality(self):
        with self.assertRaises(AssertionError):
            release.assert_publications([(1.0, 'old', 'sending')], [(1, 'old', 'sending')], set())


class PublicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'fixture.db'
        with patch.object(schema, 'DB_PATH', self.db), patch.object(schema, 'LEGACY_DB_PATH', self.db.with_name('absent.db')):
            schema.init_db()
        init_mini_db(self.db)
        world = register_mini_world(-911, 2, 'Synthetic validator fixture', db_path=self.db)
        self.player = create_mini_player(world, 911, 'fixture', 'Fixture', db_path=self.db)['id']
        self.execute("INSERT INTO mini_ui_publications VALUES(?, 'keep', 'published')", (self.player,))
        for key in ('share:123:456', 'ordinary'):
            self.execute('INSERT INTO mini_public_shares VALUES(?,?)', (self.player, key))
        self.before = release.snapshot(self.db)

    def execute(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(sql, args)

    def migrate(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            apply(conn)

    def test_real_migration_adds_only_three_and_repeats_preserve_all_tables(self):
        self.migrate()
        release.assert_preserved(self.db, self.before)
        additions = release.publication_additions(self.before)
        self.assertEqual(len(additions), 3)
        migrated = release.snapshot(self.db)
        introduced = release.new_shop_tables(self.before, migrated)
        expected_schema = release.schema_snapshot(self.db)
        for _ in range(2):
            with patch.object(schema, 'DB_PATH', self.db), patch.object(schema, 'LEGACY_DB_PATH', self.db.with_name('absent.db')):
                schema.init_db()
            release.assert_preserved(self.db, self.before)
            release.assert_repeated(self.db, migrated, introduced, expected_schema)

    def test_reject_missing_expected_insert(self):
        self.migrate()
        self.execute("DELETE FROM mini_ui_publications WHERE operation_key='ordinary'")
        with self.assertRaisesRegex(AssertionError, 'unexpected inserts'):
            release.assert_preserved(self.db, self.before)

    def test_reject_unexplained_insert(self):
        self.migrate()
        self.execute("INSERT INTO mini_ui_publications VALUES(?,'intruder','sending')", (self.player,))
        with self.assertRaisesRegex(AssertionError, 'unexpected inserts'):
            release.assert_preserved(self.db, self.before)

    def test_reject_edit_of_preexisting_row(self):
        self.migrate()
        self.execute("UPDATE mini_ui_publications SET status='sending' WHERE operation_key='keep'")
        with self.assertRaises(AssertionError):
            release.assert_preserved(self.db, self.before)

    def test_reject_delete_of_preexisting_row(self):
        self.migrate()
        self.execute("DELETE FROM mini_ui_publications WHERE operation_key='keep'")
        with self.assertRaises(AssertionError):
            release.assert_preserved(self.db, self.before)

    def test_reject_wrong_status_of_expected_new_record(self):
        self.migrate()
        self.execute("UPDATE mini_ui_publications SET status='published' WHERE operation_key='ordinary'")
        with self.assertRaisesRegex(AssertionError, 'unexpected inserts'):
            release.assert_preserved(self.db, self.before)

    def test_reject_unrelated_historical_change(self):
        self.migrate()
        self.execute("UPDATE mini_players SET coins=coins+1 WHERE id=?", (self.player,))
        with self.assertRaisesRegex(AssertionError, 'mini_players'):
            release.assert_preserved(self.db, self.before)

    def test_source_share_change_does_not_expand_permission(self):
        self.execute("INSERT INTO mini_public_shares VALUES(?,'new')", (self.player,))
        self.migrate()
        with self.assertRaises(AssertionError):
            release.assert_preserved(self.db, self.before)

    def test_new_shop_tables_frozen_across_old_startup(self):
        self.migrate()
        migrated = release.snapshot(self.db)
        introduced = {'dnd_shop_catalog_versions': migrated['dnd_shop_catalog_versions']}
        expected_schema = release.schema_snapshot(self.db)
        self.execute("INSERT INTO dnd_shop_catalog_versions VALUES('synthetic','digest','',0,CURRENT_TIMESTAMP)")
        with self.assertRaisesRegex(AssertionError, 'New shop table changed'):
            release.assert_repeated(self.db, migrated, introduced, expected_schema)

    def test_reject_schema_changes_after_first_startup(self):
        self.migrate()
        migrated = release.snapshot(self.db)
        expected_schema = release.schema_snapshot(self.db)
        self.execute('CREATE INDEX unreviewed ON mini_ui_publications(status)')
        with self.assertRaisesRegex(AssertionError, 'Schema changed'):
            release.assert_repeated(self.db, migrated, {}, expected_schema)

    def test_full_old_startup_gate_uses_only_disposable_copy(self):
        # Initialize the complete catalog/world startup first, then reproduce the
        # old production shares missing only their three publications.
        # This is a historical source fixture: bootstrap from the exact old
        # code/catalog being verified. New S5 behavior is audited separately.
        release.run_old(self.legacy_code(), self.db, 'import check_bot;check_bot.main()')
        self.execute("DELETE FROM mini_ui_publications WHERE operation_key!='keep'")
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        release.verify_ref(release.BASE, self.db)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), digest)


    def legacy_code(self):
        import subprocess, zipfile
        root = Path(self.temp.name)
        code = root / 'legacy-code'
        if not code.exists():
            archive = root / 'legacy-code.zip'
            subprocess.run(['git','archive','--format=zip','--output='+str(archive),
                            release.BASE,'app','check_bot.py'],cwd=release.ROOT,check=True)
            code.mkdir()
            with zipfile.ZipFile(archive) as source:
                source.extractall(code)
        return code

    def test_old_startup_only_deactivates_new_s5_and_mythic_on_disposable_copy(self):
        import json
        release.run_old(release.ROOT,self.db,'import check_bot;check_bot.main()')
        manifest=json.loads((release.ROOT/'docs/v1.5/S5_IMPORT_MANIFEST.json').read_text(encoding='utf-8'))['heroes']
        codes={h['code'] for h in manifest}
        mythics=json.loads((release.ROOT/'app/mini/content/heroes/mythic.json').read_text(encoding='utf-8'))['heroes']
        codes.update(h['code'] for h in mythics)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            hero=conn.execute('SELECT id FROM mini_heroes WHERE code=?',(manifest[0]['code'],)).fetchone()[0]
            conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id,copies,stars) VALUES(?,?,3,2)',(self.player,hero))
            conn.execute('UPDATE mini_players SET coins=321,shards=123,mythic_shards=17,active_hero_id=? WHERE id=?',(hero,self.player))
        digest=hashlib.sha256(self.db.read_bytes()).hexdigest()
        copy=Path(self.temp.name)/'old-startup-copy.db'
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(copy)) as target:
            source.backup(target)
        expected=release.snapshot(copy)
        columns,rows=expected['mini_heroes']; code_index=columns.index('code'); active_index=columns.index('active')
        self.assertEqual(sum(r[code_index] in codes for r in rows),25)
        updated=[]
        for row in rows:
            row=list(row)
            if row[code_index] in codes: row[active_index]=0
            updated.append(tuple(row))
        expected['mini_heroes']=(columns,updated)
        release.run_old(self.legacy_code(),copy,'import check_bot;check_bot.main()')
        # All other typed columns/rows, player money, ownership, stars and all
        # schemas remain subject to the original strict preservation guard.
        release.assert_preserved(copy,expected)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),digest)
