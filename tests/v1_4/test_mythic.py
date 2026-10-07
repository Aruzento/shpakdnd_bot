import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app.mini.catalog import load_hero_catalog
from app.mini.heroes import sync_hero_catalog,get_player_heroes
from app.mini.mythic import service as m
from app.mini.schema import init_mini_db
from tests.v1_3.support import MiniCase


class MythicTests(MiniCase):
    def setUp(self):
        super().setUp();self.content=load_hero_catalog();hero=copy.deepcopy(self.content['heroes'][0])
        hero.update(code='test_mythic',rarity='mythic',fragment_cost=10)
        self.content['heroes'].append(hero)
        self.mock=patch('app.mini.catalog._read_hero_catalog',return_value=self.content);self.mock.start();self.addCleanup(self.mock.stop)
        sync_hero_catalog(self.db)
    def grant(self,amount=15,key='one',code='test_mythic'):return m.grant_mythic_fragments(self.pid,code,amount,'test',key,self.db)

    def test_grant_and_exact_idempotency(self):
        self.assertTrue(self.grant()['applied']);self.assertFalse(self.grant()['applied'])
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],15)
        with self.assertRaises(ValueError):self.grant(amount=16)

    def test_wrong_rarity_and_invalid_amount_source_key(self):
        with self.assertRaises(ValueError):self.grant(code='Villager')
        for value in (0,-1,True,1.5):
            with self.assertRaises(ValueError):self.grant(amount=value)
        for source,key in (('', 'one'),('test','')):
            with self.assertRaises(ValueError):m.grant_mythic_fragments(self.pid,'test_mythic',1,source,key,self.db)

    def test_insufficient_craft_atomic(self):
        self.grant(9)
        with self.assertRaises(ValueError):m.craft(self.pid,'test_mythic',self.db)
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],9)
        self.assertFalse(m.list_heroes(self.pid,self.db)[0]['owned'])

    def test_craft_remainder_and_second_craft_rejected(self):
        self.grant(25);m.craft(self.pid,'test_mythic',self.db)
        with self.assertRaises(ValueError):m.craft(self.pid,'test_mythic',self.db)
        hero=m.list_heroes(self.pid,self.db)[0];self.assertTrue(hero['owned']);self.assertEqual(hero['fragments'],15)
        self.assertEqual(self.sql('SELECT stars FROM mini_player_heroes WHERE player_id=?',(self.pid,))[0]['stars'],0)

    def test_restart_and_fragments_stay_per_hero(self):
        hero=copy.deepcopy(self.content['heroes'][-1]);hero['code']='other_mythic';self.content['heroes'].append(hero);sync_hero_catalog(self.db)
        self.grant();init_mini_db(self.db)
        values={h['code']:h['fragments'] for h in m.list_heroes(self.pid,self.db)}
        self.assertEqual(values,{'test_mythic':15,'other_mythic':0})
        m.craft(self.pid,'test_mythic',self.db);init_mini_db(self.db)
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],5)

    def test_concurrent_craft_only_one_unlock(self):
        self.grant(30)
        def craft(_):
            try:return m.craft(self.pid,'test_mythic',self.db)['crafted']
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(craft,range(2))),1)
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],20)

    def test_failed_unlock_rolls_back_fragment_spend(self):
        self.grant()
        self.sql("CREATE TRIGGER deny_mythic BEFORE INSERT ON mini_player_heroes BEGIN SELECT RAISE(ABORT,'denied'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):m.craft(self.pid,'test_mythic',self.db)
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],15)

    def test_concurrent_grant_only_one_reward(self):
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.grant(),range(2)))
        self.assertEqual(sum(r['applied'] for r in results),1)
        self.assertEqual(m.list_heroes(self.pid,self.db)[0]['fragments'],15)


    def test_craft_by_stable_database_id_and_wrong_rarity_rejected(self):
        from app.mini.mythic.service import craft_by_id, list_heroes
        content=list_heroes(self.pid,self.db)[0]
        self.assertIsNotNone(content['id'])
        m.grant_mythic_fragments(self.pid,content['code'],content['fragment_cost'],'test','idgrant',self.db)
        self.assertTrue(craft_by_id(self.pid,content['id'],self.db)['crafted'])
        with self.assertRaises(ValueError):craft_by_id(self.pid,self.hero()['id'],self.db)
