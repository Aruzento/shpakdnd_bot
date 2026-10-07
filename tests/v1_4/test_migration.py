"""Upgrade a backed-up V1.3.3 schema; compare all old rows and old columns."""
import unittest
from pathlib import Path
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.boss.schema import init_boss_db
from tests import test_mini_migrations as fixtures


class V14MigrationTests(unittest.TestCase):
    setUp=fixtures.MiniMigrationTests.setUp
    seed=fixtures.MiniMigrationTests.seed

    def test_v133_backup_all_data_and_snapshots_preserved_through_three_upgrades(self):
        self.seed()
        new_tables=('mini_duel_locks','mini_duels','mini_shadow_rolls','mini_mythic_grants','mini_mythic_fragments',
                    'mini_daily_chests','mini_daily_streak','mini_village_operations','mini_village_boosts','mini_village_residents','mini_villages')
        with connect_mini_db(self.db) as conn:
            for table in new_tables:conn.execute(f'DROP TABLE {table}')
            conn.execute('DROP INDEX idx_mini_gacha_operation')
            for table,fields in (
                ('mini_gacha_pulls',('operation_key','result_json')),
                ('mini_daily_claims',('streak_count','cycle_day','chest_code')),
                ('mini_bosses',('content_version','shadow_extractable','shadow_hero_code'))):
                for field in fields:conn.execute(f'ALTER TABLE {table} DROP COLUMN {field}')
        upgraded=Path(self.db.parent)/'v14-upgrade.db'
        with connect_mini_db(self.db) as source,connect_mini_db(upgraded) as target:source.backup(target)
        with connect_mini_db(self.db) as source:
            tables=[r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            columns={t:[r[1] for r in source.execute(f'PRAGMA table_info({t})')] for t in tables}
            before={t:source.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall() for t in tables}
        for _ in range(3):init_mini_db(upgraded);init_boss_db(upgraded)
        with connect_mini_db(upgraded) as conn:
            for table in tables:
                old=','.join(columns[table])
                self.assertEqual(conn.execute(f'SELECT {old} FROM {table} ORDER BY rowid').fetchall(),before[table],table)
            self.assertEqual(conn.execute('SELECT content_version,shadow_extractable,shadow_hero_code FROM mini_bosses').fetchall(),[(0,1,None)])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertTrue(set(new_tables)<={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        with connect_mini_db(self.db) as source:
            self.assertNotIn('content_version',[r[1] for r in source.execute('PRAGMA table_info(mini_bosses)')])
