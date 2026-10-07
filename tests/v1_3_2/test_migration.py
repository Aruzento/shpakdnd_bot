"""Upgrade an actual backed-up V1.3.1 schema and compare every old column."""
import sqlite3
from pathlib import Path
from tests import test_mini_migrations as fixtures
from app.mini.schema import init_mini_db
from app.mini.boss.schema import init_boss_db
from app.mini.db import connect_mini_db
import unittest


class ReleaseMigrationTests(unittest.TestCase):
    setUp=fixtures.MiniMigrationTests.setUp
    seed=fixtures.MiniMigrationTests.seed

    def test_v131_sqlite_backup_preserves_all_existing_data_and_snapshots(self):
        self.seed()
        with connect_mini_db(self.db) as conn:
            conn.execute('DROP TABLE mini_hero_favorites')
            for column in ('class_rules_version','boss_damage','sneaky_stolen'):
                conn.execute(f'ALTER TABLE mini_bosses DROP COLUMN {column}')
        original=self.db
        copy=Path(original.parent)/'upgrade-copy.db'
        with connect_mini_db(original) as source,connect_mini_db(copy) as target:source.backup(target)
        with connect_mini_db(original) as source:
            tables=[r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            columns={table:[r[1] for r in source.execute(f'PRAGMA table_info({table})')] for table in tables}
            before={table:source.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in tables}
        for _ in range(3):init_mini_db(copy);init_boss_db(copy)
        with connect_mini_db(copy) as upgraded:
            for table in tables:
                names=','.join(columns[table])
                self.assertEqual(upgraded.execute(f'SELECT {names} FROM {table} ORDER BY rowid').fetchall(),before[table],table)
            self.assertEqual(upgraded.execute('SELECT class_rules_version,boss_damage,sneaky_stolen FROM mini_bosses').fetchall(),[(0,0,0)])
            self.assertEqual(upgraded.execute('SELECT * FROM mini_hero_favorites').fetchall(),[])
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(),[])
        with connect_mini_db(original) as source:
            self.assertNotIn('boss_damage',[r[1] for r in source.execute('PRAGMA table_info(mini_bosses)')])
