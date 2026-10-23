"""V1.5 retires per-hero crafting while preserving legacy balances verbatim."""
from concurrent.futures import ThreadPoolExecutor
from app.mini.mythic import service as m
from app.mini.schema import init_mini_db
from tests.v1_3.support import MiniCase


class MythicTests(MiniCase):
    def setUp(self):
        super().setUp()
        self.sql("INSERT INTO mini_mythic_fragments VALUES(?,'legacy_test_only',17)",(self.pid,))
    def grant(self,amount=15,key='one'):
        return m.grant_mythic_shards(self.pid,amount,'test',key,self.db)
    def test_grant_and_exact_idempotency(self):
        self.assertEqual(self.grant(),{'balance':15});self.assertEqual(self.grant(),{'balance':15})
        self.assertEqual(len(self.sql('SELECT * FROM mini_mythic_ledger')),1)
        with self.assertRaises(ValueError):self.grant(amount=16)
    def test_invalid_amount_source_key(self):
        for value in (0,-1,True,1.5):
            with self.assertRaises(ValueError):self.grant(value)
        for source,key in (('', 'one'),('test','')):
            with self.assertRaises(ValueError):m.grant_mythic_shards(self.pid,1,source,key,self.db)
    def test_retired_craft_does_not_spend_legacy_fragments(self):
        with self.assertRaises(ValueError):m.craft(self.pid,'legacy_test_only',self.db)
        self.assertEqual(m.inspect_legacy(self.db),[(self.pid,'legacy_test_only',17)])
    def test_retired_per_hero_grant_is_rejected(self):
        with self.assertRaises(ValueError):m.grant_mythic_fragments(self.pid,'legacy_test_only',25,'test','old',self.db)
        self.assertEqual(self.sql('SELECT amount FROM mini_mythic_fragments')[0]['amount'],17)
    def test_restart_keeps_distinct_currencies_and_legacy_balances(self):
        self.grant();init_mini_db(self.db)
        self.assertEqual(m.inspect_legacy(self.db),[(self.pid,'legacy_test_only',17)])
        self.assertEqual(self.sql('SELECT mythic_shards FROM mini_players WHERE id=?',(self.pid,))[0]['mythic_shards'],15)
    def test_concurrent_craft_never_unlocks_a_hero(self):
        def craft(_):
            try:m.craft(self.pid,'legacy_test_only',self.db);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertFalse(any(pool.map(craft,range(2))))
        self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])
    def test_failed_ledger_write_rolls_back_universal_balance(self):
        import sqlite3
        self.sql("CREATE TRIGGER deny_mythic BEFORE INSERT ON mini_mythic_ledger BEGIN SELECT RAISE(ABORT,'denied'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.grant()
        self.assertEqual(self.sql('SELECT mythic_shards FROM mini_players WHERE id=?',(self.pid,))[0]['mythic_shards'],0)
    def test_concurrent_grant_only_one_reward(self):
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.grant(),range(2)))
        self.assertEqual(results,[{'balance':15}]*2)
        self.assertEqual(len(self.sql('SELECT * FROM mini_mythic_ledger')),1)
    def test_craft_by_id_is_retired_even_for_owned_hero(self):
        h=self.hero()
        with self.assertRaises(ValueError):m.craft_by_id(self.pid,h['id'],self.db)
        self.assertEqual(m.list_heroes(self.pid,self.db),[])
