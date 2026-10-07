import sqlite3
from tests.v1_3.support import MiniCase
from app.mini.db import connect_mini_db
from app.mini.equipment.service import get_equipment,equip,unequip,grant_in_transaction,remove_in_transaction
from app.mini.schema import init_mini_db
from app.mini.tower.service import start_attempt,list_heroes,get_state,attack


class EquipmentTests(MiniCase):
    def grant(self,code):
        with connect_mini_db(self.db) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            grant_in_transaction(c,self.pid,code)

    def test_equip_replace_unequip_retains_ownership(self):
        self.grant('eq_helmet_010');self.grant('eq_helmet_020')
        self.assertEqual(equip(self.pid,'eq_helmet_010',self.db)['attack_bonus'],10)
        result=equip(self.pid,'eq_helmet_020',self.db)
        self.assertEqual(result['attack_bonus'],20)
        self.assertEqual(len(result['owned']),2)
        self.assertEqual(unequip(self.pid,'helmet',self.db)['attack_bonus'],0)

    def test_aggregate_all_slots_max_300(self):
        for slot in ('helmet','ring','cloak'):
            code=f'eq_{slot}_100';self.grant(code);equip(self.pid,code,self.db)
        self.assertEqual(get_equipment(self.pid,self.db)['attack_bonus'],300)

    def test_restart_migration_preserves_equipment(self):
        self.grant('eq_ring_042');equip(self.pid,'eq_ring_042',self.db)
        init_mini_db(self.db);init_mini_db(self.db)
        self.assertEqual(get_equipment(self.pid,self.db)['equipped']['ring']['code'],'eq_ring_042')

    def test_delete_last_copy_atomically_clears_slot(self):
        code='eq_cloak_022';self.grant(code);self.grant(code);equip(self.pid,code,self.db)
        with connect_mini_db(self.db) as c:
            c.execute('BEGIN IMMEDIATE');remove_in_transaction(c,self.pid,code)
        self.assertEqual(get_equipment(self.pid,self.db)['attack_bonus'],22)
        with connect_mini_db(self.db) as c:
            c.execute('BEGIN IMMEDIATE');remove_in_transaction(c,self.pid,code)
        self.assertEqual(get_equipment(self.pid,self.db)['equipped'],{})

    def test_cannot_equip_unowned_or_invalid_slot(self):
        with self.assertRaises(ValueError):equip(self.pid,'eq_ring_100',self.db)
        with self.assertRaises(ValueError):unequip(self.pid,'boots',self.db)

    def test_same_account_bonus_for_different_heroes(self):
        self.grant('eq_ring_020');equip(self.pid,'eq_ring_020',self.db)
        h=self.hero('Villager');other=self.hero('CityBlacksmith')
        first=start_attempt(self.pid,h['id'],1,self.db)
        self.assertEqual(first['equipment_bonus'],20)
        self.sql("UPDATE mini_tower_attempts SET status='lost',shields=0 WHERE id=?",(first['id'],))
        second=start_attempt(self.pid,other['id'],1,self.db)
        self.assertEqual(second['equipment_bonus'],20)

    def test_mid_attempt_equipment_change_does_not_rewrite_snapshot(self):
        h=self.hero();a=start_attempt(self.pid,h['id'],1,self.db)
        self.grant('eq_ring_020');equip(self.pid,'eq_ring_020',self.db)
        self.assertEqual(get_state(self.pid,self.db)['attempt']['equipment_bonus'],0)
        self.assertEqual(get_state(self.pid,self.db)['equipment_bonus'],20)
