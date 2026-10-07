import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.mini.catalog import load_hero_catalog,RARITIES,HERO_CATALOG_DIR,HEROES_PATH
from app.mini.gacha import _choose_hero_code,perform_gacha_pull,GachaNoHeroes
from app.mini.heroes import get_player_heroes,sync_hero_catalog,get_hero_by_code
from app.mini.hero_upgrades import hero_upgrade_state,upgrade_hero
from tests.v1_3.support import MiniCase


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for rarity in RARITIES:
            (self.root/f'{rarity}.json').write_text((HERO_CATALOG_DIR/f'{rarity}.json').read_text(encoding='utf-8'),encoding='utf-8')
        self.patch=patch('app.mini.catalog.HERO_CATALOG_DIR',self.root);self.patch.start();self.addCleanup(self.patch.stop)

    def write(self,rarity,heroes):
        (self.root/f'{rarity}.json').write_text(json.dumps({'heroes':heroes}),encoding='utf-8')

    def test_six_catalogs_loaded_empty_exclusives_valid(self):
        self.assertEqual(len(list(self.root.glob('*.json'))),6)
        catalog=load_hero_catalog();self.assertIn('settings',catalog)
        self.assertGreater(len(catalog['heroes']),30)
        for rarity in ('mythic','shadow'):self.assertEqual(json.loads((self.root/f'{rarity}.json').read_text())['heroes'],[])

    def test_duplicate_across_files(self):
        hero=copy.deepcopy(load_hero_catalog()['heroes'][0]);hero['rarity']='shadow';self.write('shadow',[hero])
        with self.assertRaisesRegex(ValueError,'повтор'):load_hero_catalog()

    def test_rarity_file_mismatch(self):
        hero=copy.deepcopy(load_hero_catalog()['heroes'][0]);hero['code']='wrong';self.write('shadow',[hero])
        with self.assertRaisesRegex(ValueError,'rarity'):load_hero_catalog()

    def test_missing_catalog_rejected(self):
        (self.root/'shadow.json').unlink()
        with self.assertRaisesRegex(ValueError,'Не найден'):load_hero_catalog()

    def test_invalid_root_and_entries_rejected(self):
        for raw in ('[]','{"heroes":{}}','{"heroes":[null]}','{"heroes":[{"code":[]}]}'):
            (self.root/'shadow.json').write_text(raw,encoding='utf-8')
            with self.assertRaises(ValueError):load_hero_catalog()

    def test_legacy_codes_remain_exact(self):
        original=json.loads(Path(__file__).with_name('legacy_heroes.json').read_text(encoding='utf-8'))
        current={h['code']:h for h in load_hero_catalog()['heroes']}
        for old in original:
            self.assertIn(old['code'],current)
            if old['code'] not in ('Kirito','SonDzhi'):
                self.assertEqual(current[old['code']],old)

    def test_mythic_requires_positive_fragment_cost(self):
        hero=copy.deepcopy(load_hero_catalog()['heroes'][0]);hero.update(code='myth',rarity='mythic')
        for cost in (None,0,-1,True,1.5):
            hero['fragment_cost']=cost;self.write('mythic',[hero])
            with self.assertRaisesRegex(ValueError,'fragment_cost'):load_hero_catalog()
        hero['fragment_cost']=5;self.write('mythic',[hero]);load_hero_catalog()

    def test_gacha_excludes_mythic_shadow_even_with_positive_weights(self):
        data=load_hero_catalog()
        for rarity in ('shadow','mythic'):
            hero=copy.deepcopy(data['heroes'][0]);hero.update(code=rarity,rarity=rarity,active=True)
            data['heroes'].append(hero);data['settings']['rarity_weights'][rarity]=10**9
        with patch('app.mini.gacha.load_hero_catalog',return_value=data),patch('app.mini.gacha.secrets.randbelow',side_effect=lambda bound:bound-1):
            for luck in (True,False):
                self.assertNotIn(_choose_hero_code(luck_active=luck),('shadow','mythic'))

    def test_no_ordinary_pool_does_not_fallback_to_exclusive(self):
        data=load_hero_catalog();hero=copy.deepcopy(data['heroes'][0]);hero['rarity']='shadow';data['heroes']=[hero]
        data['settings']['rarity_weights']['shadow']=100
        with patch('app.mini.gacha.load_hero_catalog',return_value=data),self.assertRaises(GachaNoHeroes):_choose_hero_code()


class OwnershipTests(MiniCase):
    def test_collection_and_upgrade_accept_both_exclusive_rarities(self):
        data=load_hero_catalog()
        for rarity in ('shadow','mythic'):
            hero=copy.deepcopy(data['heroes'][0]);hero.update(code='fixture_'+rarity,rarity=rarity,fragment_cost=5)
            data['heroes'].append(hero)
        with patch('app.mini.catalog._read_hero_catalog',return_value=data):
            sync_hero_catalog(self.db)
            self.sql('UPDATE mini_players SET shards=1000 WHERE id=?',(self.pid,))
            for rarity in ('shadow','mythic'):
                hero=self.hero('fixture_'+rarity)
                state=hero_upgrade_state(hero);self.assertIn('can_upgrade',state)
                result=upgrade_hero(self.pid,hero['id'],self.db)
                self.assertEqual(result['stars'],1)
            self.assertTrue({'shadow','mythic'} <= {h['rarity'] for h in get_player_heroes(self.pid,self.db)})

    def test_sync_preserves_hero_ids_and_ownership(self):
        hero=self.hero();sync_hero_catalog(self.db)
        self.assertEqual(get_hero_by_code('Villager',self.db)['id'],hero['id'])
        self.assertEqual(get_player_heroes(self.pid,self.db)[0]['id'],hero['id'])


    def test_repeat_gacha_key_charges_and_grants_once_after_restart(self):
        from app.mini.wallet import add_coins,get_balance
        from app.mini.schema import init_mini_db
        add_coins(self.pid,100,'Тест',db_path=self.db)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            first=perform_gacha_pull(self.pid,'coins',self.db,operation_key='same')
            init_mini_db(self.db)
            second=perform_gacha_pull(self.pid,'coins',self.db,operation_key='same')
            self.assertTrue(second['repeated']);self.assertEqual(first['pull_id'],second['pull_id'])
            self.assertEqual(get_balance(self.pid,self.db),100-first['cost_coins'])
            self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),1)
            with self.assertRaises(ValueError):perform_gacha_pull(self.pid,'ticket',self.db,operation_key='same')

    def test_concurrent_coupon_consumed_and_hero_given_once(self):
        from app.mini.items import grant_item_in_transaction
        from app.mini.weekly import sync_items_in_transaction
        from app.mini.db import connect_mini_db
        from concurrent.futures import ThreadPoolExecutor
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');sync_items_in_transaction(conn);grant_item_in_transaction(conn,self.pid,'summon_ticket',2)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:perform_gacha_pull(self.pid,'ticket',self.db,operation_key='coupon'),range(2)))
        self.assertEqual(results[0]['pull_id'],results[1]['pull_id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),1)
        self.assertEqual(self.sql("SELECT quantity FROM mini_inventory inv JOIN mini_items i ON i.id=inv.item_id WHERE i.code='summon_ticket' AND inv.player_id=?",(self.pid,))[0]['quantity'],4)


    def test_public_gacha_odds_and_available_total_exclude_exclusives(self):
        from app.mini.gacha import get_gacha_state
        data=load_hero_catalog()
        for rarity in ('mythic','shadow'):
            h=copy.deepcopy(data['heroes'][0]);h.update(code='pool_'+rarity,rarity=rarity,fragment_cost=10)
            data['heroes'].append(h);data['settings']['rarity_weights'][rarity]=100000
        with patch('app.mini.catalog._read_hero_catalog',return_value=data):
            sync_hero_catalog(self.db);state=get_gacha_state(self.pid,self.db)
        self.assertFalse({'mythic','shadow'} & state['rarity_counts'].keys())
        self.assertFalse({'mythic','shadow'} & state['rarity_chances'].keys())
        self.assertAlmostEqual(sum(state['rarity_chances'].values()),100,delta=.2)
        self.assertEqual(state['total'],sum(bool(h.get('active',True)) and h['rarity'] in ('common','uncommon','rare','legendary') for h in data['heroes']))
