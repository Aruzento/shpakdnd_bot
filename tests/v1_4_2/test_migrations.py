from tests.v1_3.support import MiniCase
from app.mini.schema import init_mini_db
from app.mini.gacha import get_gacha_state
from app.mini.validate_v142 import audit


class MigrationTests(MiniCase):
    def test_old_schema_starts_pity_at_zero_preserves_history_and_admin_guarantee(self):
        hero=self.hero()
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)',(self.pid,))
        self.sql('INSERT INTO mini_gacha_pulls(player_id,hero_id,cost_coins) VALUES(?,?,30)',(self.pid,hero['id']))
        history=self.sql('SELECT * FROM mini_gacha_pulls')
        self.sql('DROP TABLE mini_gacha_pity')
        self.sql('DROP INDEX idx_mini_purchase_operation')
        self.sql('ALTER TABLE mini_purchases DROP COLUMN operation_key')
        self.sql('ALTER TABLE mini_purchases DROP COLUMN result_json')
        for _ in range(3):init_mini_db(self.db)
        self.assertEqual(get_gacha_state(self.pid,self.db)['pity'],dict(uncommon=0,rare=0,legendary=0))
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),history)
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees')[0]['forced_legendary'],1)
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])

    def test_read_only_dynamic_catalog_audit(self):
        result=audit()
        self.assertTrue(all(result['ordinary_gacha_pools'].values()))
        self.assertTrue(result['shadow_links'])
        self.assertEqual(result['missing_images'],0)
