from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.superadmin.access import is_superadmin
from app.mini.superadmin.parser import parse_command
from app.mini.superadmin.service import execute
from app.mini.wallet import add_coins,get_balance
from app.mini.gacha import perform_gacha_pull,get_gacha_state,GachaInsufficientFunds
from app.mini.schema import init_mini_db
from app.mini.shop import sync_shop_catalog
from app.mini.equipment.service import equip,get_equipment
from app.mini.tower.service import start_attempt


class SuperadminTests(MiniCase):
    def command(self,action,target='@tester',tail='',key=None):
        command=parse_command(f'/{action} {self.world["chat_id"]}:{self.world["thread_id"]} {target} {tail}')
        if key is None:key=f'case:{len(self.sql("SELECT * FROM mini_superadmin_audit"))}'
        return execute(self.owner,command,operation_key=key,db_path=self.db)

    def test_numeric_owner_only(self):
        self.assertTrue(is_superadmin(self.owner));self.assertFalse(is_superadmin(self.owner+1))
        command=parse_command(f'/superchars {self.world["chat_id"]}:{self.world["thread_id"]}')
        with self.assertRaises(ValueError):execute(self.owner+1,command,db_path=self.db)
        with patch('app.mini.superadmin.access.SUPERADMIN_USER_ID',None):self.assertFalse(is_superadmin(self.owner))

    def test_parser_strict_scope_and_single_resource(self):
        for text in ['/superlook 123 @tester','/superlook 123:abc @tester','/superadd 123:0 @tester -c 0',
                     '/superadd 123:0 @tester -c -2','/superadd 123:0 @tester -c -s 10',
                     '/superadd 123:0 @tester -c 1.5','/superluck 123:0 ALL','/superlook 123:0 @tester -x',
                     '/superchars 123:0 @tester','/superadminadd 123:0 @tester']:
            with self.subTest(text=text),self.assertRaises(ValueError):parse_command(text)
        self.assertEqual(parse_command('/superlook 123:0 @tester').flags,('-c','-s','-p','-i'))

    def test_superlook_flags_and_numeric_target(self):
        self.command('superadd',tail='-c 12');self.command('superadd',tail='-s 5');self.hero()
        self.command('superadd',tail='-i eq_ring_020');equip(self.pid,'eq_ring_020',self.db)
        response=self.command('superlook',target='900123')
        for text in ['900123','Монеты: 12','Осколки: 5','Villager','ATK','★0','eq_ring_020','Equipment ATK: +20']:
            self.assertIn(text,response)
        response=self.command('superlook',tail='-c -s')
        self.assertNotIn('Герои:',response)

    def test_coin_and_shard_add_delete_are_separate(self):
        self.command('superadd',tail='-c 500');self.command('superadd',tail='-s 100')
        self.command('superdel',tail='-c 200');self.command('superdel',tail='-s 30')
        self.assertEqual(get_balance(self.pid,self.db),300)
        self.assertEqual(self.sql('SELECT shards FROM mini_players')[0]['shards'],70)
        self.assertEqual(len(self.sql('SELECT * FROM mini_wallet_transactions')),2)
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),4)

    def test_delete_cannot_overdraw_or_leave_audit(self):
        for flag in ('-c','-s'):
            with self.subTest(flag=flag),self.assertRaises(ValueError):self.command('superdel',tail=f'{flag} 1')
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),[])
        self.assertEqual(get_balance(self.pid,self.db),0)

    def test_grant_specific_hero_duplicate_is_not_gacha(self):
        self.command('superadd',tail='-p Villager')
        self.assertIn('Герой уже есть',self.command('superadd',tail='-p Villager'))
        self.assertEqual(self.sql('SELECT copies FROM mini_player_heroes')[0]['copies'],1)
        self.assertEqual(self.sql('SELECT shards FROM mini_players')[0]['shards'],0)
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
        self.assertEqual(self.sql('SELECT active_hero_id FROM mini_players')[0]['active_hero_id'],self.sql('SELECT hero_id FROM mini_player_heroes')[0]['hero_id'])

    def test_hero_delete_keeps_active_selection_valid(self):
        self.command('superadd',tail='-p Villager');self.command('superadd',tail='-p CityBlacksmith')
        self.command('superdel',tail='-p Villager')
        self.assertEqual(self.sql('SELECT active_hero_id FROM mini_players')[0]['active_hero_id'],self.sql('SELECT hero_id FROM mini_player_heroes')[0]['hero_id'])
        self.command('superdel',tail='-p CityBlacksmith')
        self.assertIsNone(self.sql('SELECT active_hero_id FROM mini_players')[0]['active_hero_id'])

    def test_active_tower_hero_delete_is_rejected(self):
        h=self.hero();start_attempt(self.pid,h['id'],1,self.db)
        with self.assertRaisesRegex(ValueError,'Tower'):self.command('superdel',tail='-p Villager')
        self.assertEqual(len(self.sql('SELECT * FROM mini_player_heroes')),1)

    def test_active_boss_hero_delete_is_rejected(self):
        from app.mini.boss.schema import init_boss_db
        from app.mini.boss.service import create_boss_event,register_player
        from app.mini.boss.catalog import list_boss_templates
        h=self.hero();init_boss_db(self.db)
        boss=create_boss_event(self.world['id'],list_boss_templates()[0]['code'],self.owner,self.db)
        register_player(boss['id'],self.pid,self.db)
        with self.assertRaisesRegex(ValueError,'Boss'):self.command('superdel',tail='-p Villager')

    def test_usable_item_and_equipment_add_delete(self):
        sync_shop_catalog(self.world['id'],self.db)
        self.command('superadd',tail='-i summon_ticket');self.command('superdel',tail='-i summon_ticket')
        self.assertEqual(self.sql('SELECT quantity FROM mini_inventory')[0]['quantity'],3)
        self.command('superadd',tail='-i eq_helmet_017');equip(self.pid,'eq_helmet_017',self.db)
        self.command('superdel',tail='-i eq_helmet_017')
        self.assertEqual(get_equipment(self.pid,self.db)['equipped'],{})
        with self.assertRaises(ValueError):self.command('superadd',tail='-i nonexistent')

    def test_unknown_hero_and_missing_owned_hero_delete(self):
        with self.assertRaises(ValueError):self.command('superadd',tail='-p nonexistent')
        with self.assertRaises(ValueError):self.command('superdel',tail='-p Villager')

    def test_superchars_has_stable_id_username_and_name(self):
        c=parse_command(f'/superchars {self.world["chat_id"]}:{self.world["thread_id"]}')
        text=execute(self.owner,c,db_path=self.db)
        for value in ('900123','@tester','Герой'):self.assertIn(value,text)
        self.sql("UPDATE mini_players SET username='' WHERE id=?",(self.pid,))
        self.assertIn('900123',execute(self.owner,c,db_path=self.db))

    def test_explicit_dnd_world_is_rejected_for_every_command(self):
        for action,tail in [('superlook','-c'),('superadd','-c 1'),('superdel','-c 1'),('superluck',''),('superchars','')]:
            text=f'/{action} -1003376315265:4'+('' if action=='superchars' else ' @tester '+tail)
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'Mini'):
                execute(self.owner,parse_command(text),operation_key=action,db_path=self.db)

    def test_duplicate_username_requires_numeric_target(self):
        from app.mini.players import create_mini_player
        create_mini_player(self.world['id'],900124,'@tester','Другой',self.db)
        with self.assertRaisesRegex(ValueError,'неоднозначен'):self.command('superlook')
        self.assertIn('900123',self.command('superlook',target='900123'))

    def test_audit_contains_actor_scope_target_resource_and_timestamp(self):
        self.command('superadd',tail='-s 14')
        row=self.sql('SELECT * FROM mini_superadmin_audit')[0]
        self.assertEqual(row['admin_user_id'],self.owner);self.assertEqual(row['world_id'],self.world['id'])
        self.assertEqual(row['target_user_id'],900123);self.assertEqual(row['amount'],14)
        self.assertEqual(row['action'],'superadd');self.assertEqual(row['resource'],'shards');self.assertTrue(row['created_at'])

    def test_same_operation_key_does_not_mutate_twice(self):
        self.command('superadd',tail='-c 10',key='once');self.command('superadd',tail='-c 10',key='once')
        self.assertEqual(get_balance(self.pid,self.db),10)
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),1)

    def test_audit_failure_rolls_back_wallet_and_equipment(self):
        self.sql("CREATE TRIGGER reject_audit BEFORE INSERT ON mini_superadmin_audit BEGIN SELECT RAISE(ABORT,'audit failure'); END")
        import sqlite3
        for tail in ('-c 10','-s 10','-i eq_ring_010','-p Villager'):
            with self.subTest(tail=tail),self.assertRaises(sqlite3.IntegrityError):self.command('superadd',tail=tail)
        self.assertEqual(get_balance(self.pid,self.db),0);self.assertEqual(self.sql('SELECT * FROM mini_wallet_transactions'),[])
        self.assertEqual(get_equipment(self.pid,self.db)['owned'],[]);self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])

    def test_superluck_repeated_persistent_and_failed_pull_retains_flag(self):
        self.command('superluck')
        self.assertIn('уже установлена',self.command('superluck'))
        init_mini_db(self.db);get_gacha_state(self.pid,self.db)
        with self.assertRaises(GachaInsufficientFunds):perform_gacha_pull(self.pid,db_path=self.db)
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees')[0]['forced_legendary'],1)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_guarantees')),1)

    def test_superluck_success_is_legendary_and_consumed_once(self):
        self.command('superluck');add_coins(self.pid,60,'test',db_path=self.db)
        result=perform_gacha_pull(self.pid,db_path=self.db)
        self.assertEqual(result['rarity'],'legendary');self.assertTrue(result['forced_legendary'])
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees')[0]['forced_legendary'],0)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            second=perform_gacha_pull(self.pid,db_path=self.db)
        self.assertEqual(second['rarity'],'common');self.assertFalse(second['forced_legendary'])

    def test_superluck_ticket_pull_consumes_guarantee(self):
        sync_shop_catalog(self.world['id'],self.db)
        self.command('superadd',tail='-i summon_ticket');self.command('superluck')
        result=perform_gacha_pull(self.pid,payment='ticket',db_path=self.db)
        self.assertEqual(result['rarity'],'legendary');self.assertEqual(result['tickets'],3)
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees')[0]['forced_legendary'],0)

    def test_failed_gacha_commit_rolls_back_payment_and_guarantee(self):
        self.command('superluck');add_coins(self.pid,30,'test',db_path=self.db)
        self.sql("CREATE TRIGGER reject_pull BEFORE INSERT ON mini_gacha_pulls BEGIN SELECT RAISE(ABORT,'pull failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):perform_gacha_pull(self.pid,db_path=self.db)
        self.assertEqual(get_balance(self.pid,self.db),30)
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees')[0]['forced_legendary'],1)
        self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])

    def test_hidden_override_does_not_change_public_odds_or_global_weights(self):
        from app.mini.ui.heroes import _format_gacha
        from app.mini.catalog import load_hero_catalog
        before=dict(load_hero_catalog()['settings']['rarity_weights'])
        ordinary=get_gacha_state(self.pid,self.db)
        self.command('superluck');state=get_gacha_state(self.pid,self.db)
        self.assertTrue(state['forced_legendary'])
        self.assertEqual(state['rarity_chances'],ordinary['rarity_chances'])
        self.assertEqual(_format_gacha(self.world,state),_format_gacha(self.world,ordinary))
        self.assertEqual(load_hero_catalog()['settings']['rarity_weights'],before)
