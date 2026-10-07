import sqlite3
import unittest
from tests import test_mini_migrations as migration_fixture
from app.mini.schema import init_mini_db,MINI_TABLES
from app.mini.db import connect_mini_db


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=migration_fixture.MiniMigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db=self.fixture.db
    def test_upgrade_from_pre_v1_3_schema_keeps_old_tables_and_boss_snapshots(self):
        self.fixture.seed()
        new_tables={'mini_equipment','mini_equipment_owned','mini_equipment_slots','mini_tower_progress',
                    'mini_tower_attempts','mini_tower_rewards','mini_gacha_guarantees','mini_superadmin_audit'}
        before={k:v for k,v in self.fixture.snapshot().items() if k not in new_tables}
        with connect_mini_db(self.db) as conn:
            for table in ('mini_tower_rewards','mini_tower_attempts','mini_tower_progress','mini_equipment_slots',
                          'mini_equipment_owned','mini_equipment','mini_gacha_guarantees','mini_superadmin_audit'):
                conn.execute(f'DROP TABLE {table}')
        init_mini_db(self.db)
        after=self.fixture.snapshot()
        self.assertEqual({k:v for k,v in after.items() if k not in new_tables},before)
        self.assertTrue(new_tables<=after.keys())
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
