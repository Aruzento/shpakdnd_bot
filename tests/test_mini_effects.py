import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('BOT_TOKEN','test-token')
from app.mini.effects import registry
from app.mini.effects.contracts import EffectDefinition, ItemUseError
from app.mini.effects.builtins import charged_effect
from app.mini.items import use_inventory_item, get_effect_charges
from app.mini.catalog import load_shop_catalog
from app.mini.boss.catalog import load_boss_item_catalog
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.players import create_mini_player
from app.mini.db import connect_mini_db
from tests.topic_fixtures import isolated_topics

class EffectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())

    def test_unknown_keys_rejected_and_ticket_stays_gacha_only(self):
        registry.validate_effect_key('')
        registry.validate_effect_key('gacha_ticket')
        self.assertIsNone(registry.get_effect('gacha_ticket').handler)
        with self.assertRaises(ValueError): registry.validate_effect_key('unknown')
        with self.assertRaises(ValueError): registry.validate_effect_key('',allow_empty=False)

    def test_registration_rejects_duplicates_empty_keys_and_invalid_handlers(self):
        with self.assertRaises(ValueError): registry.register_effect(EffectDefinition('gacha_luck','duplicate'))
        with self.assertRaises(ValueError): registry.register_effect(EffectDefinition(' ','bad'))
        with self.assertRaises(ValueError): registry.register_effect(EffectDefinition('test-invalid','bad',handler=5))

    def test_catalog_boundaries_reject_unregistered_item_effects(self):
        from app.mini import catalog
        from app.mini.boss import catalog as bosses
        with tempfile.TemporaryDirectory() as tmp:
            for module,loader,path_name in ((catalog,load_shop_catalog,'SHOP_PATH'),(bosses,load_boss_item_catalog,'ITEMS_JSON')):
                original=loader()
                if module is catalog:
                    next(p for p in original['products'] if p['delivery']=='inventory')['item']['effect_key']='unknown'
                else:
                    original['items'][0]['effect_key']='unknown'
                path=Path(tmp)/'content.json'; path.write_text(json.dumps(original),encoding='utf-8')
                with patch.object(module,path_name,path):
                    with self.assertRaises(ValueError): loader()

    def test_new_registered_handler_uses_same_atomic_item_service(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(registry._EFFECTS):
            db=Path(tmp)/'mini.db'; init_mini_db(db)
            world=sync_configured_mini_worlds(db)[0]['id']; player=create_mini_player(world,101,'@test','Игрок',db)['id']
            registry.register_effect(EffectDefinition('test-charge','Fixture only',charged_effect,'Fixture'))
            with connect_mini_db(db) as conn:
                item=conn.execute("INSERT INTO mini_items(code,name,category,effect_key) VALUES('test','Test','test','test-charge')").lastrowid
                conn.execute('INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(?,?,2)',(player,item))
            first=use_inventory_item(player,item,operation_key='one',db_path=db)
            again=use_inventory_item(player,item,operation_key='one',db_path=db)
            self.assertEqual(first['charges'],1); self.assertEqual(first['remaining'],1)
            self.assertTrue(again['repeated']); self.assertEqual(get_effect_charges(player,'test-charge',db),1)
            self.assertEqual(registry.active_effect_title('test-charge'),'Fixture')

    def test_invalid_pouch_amount_rolls_back_inventory_and_history(self):
        from app.mini.boss.schema import init_boss_db
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'mini.db'; init_mini_db(db); init_boss_db(db)
            world=sync_configured_mini_worlds(db)[0]['id']; player=create_mini_player(world,101,'@test','Игрок',db)['id']
            with connect_mini_db(db) as conn:
                item=conn.execute("SELECT id FROM mini_items WHERE code='boss_coin_pouch'").fetchone()[0]
                conn.execute('INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(?,?,1)',(player,item))
            with self.assertRaises(ItemUseError):
                use_inventory_item(player,item,amount_picker=lambda a,b:31,operation_key='bad',db_path=db)
            with connect_mini_db(db) as conn:
                self.assertEqual(conn.execute('SELECT quantity FROM mini_inventory WHERE item_id=?',(item,)).fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT coins FROM mini_players').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM mini_wallet_transactions').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM mini_item_uses').fetchone()[0],0)
