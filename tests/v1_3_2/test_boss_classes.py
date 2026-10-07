import json
import sqlite3
import unittest
from unittest.mock import patch
from tests.boss import test_combat_v2 as fixtures
from app.mini.boss.combat import hit_boss,force_finish_battle
from app.mini.boss.service import select_battle_hero
from app.mini.boss.schema import init_boss_db
from app.mini.schema import init_mini_db
from app.mini.db import connect_mini_db
from app.mini.wallet import get_balance
from app.mini.heroes import set_active_hero
from app.mini.combat import classes


class BossClassIntegrationTests(unittest.TestCase):
    tearDown=fixtures.CombatV2IntegrationTests.tearDown
    update_boss=fixtures.CombatV2IntegrationTests.update_boss
    update_hero=fixtures.CombatV2IntegrationTests.update_hero
    participant=fixtures.CombatV2IntegrationTests.participant
    start=fixtures.CombatV2IntegrationTests.start
    hit=fixtures.CombatV2IntegrationTests.hit

    def setUp(self):
        fixtures.CombatV2IntegrationTests.setUp(self)
        self.update_boss(max_hp=100000,current_hp=100000,reward_coins=100,reward_shields=2,reward_shields_max=2)

    def choose(self,first,second='none',passive='none',features='[]',ability='none'):
        with connect_mini_db(self.db) as conn:
            for h,kind,p in ((self.hero,first,passive),(self.villager,second,'none')):
                conn.execute("UPDATE mini_heroes SET class_tag=?,attack=100,passive_key=?,special_trait='none',faction='commoners',attack_range='melee' WHERE id=?",(kind,p,h))
        select_battle_hero(self.boss['id'],self.players[0]['id'],self.hero,self.db)
        self.update_boss(features_json=features)
        return self.start(ability)

    def test_two_warriors_buff_both_initial_attacks_without_mutating_storage(self):
        self.choose('warrior','warrior')
        before=[self.participant(i)['attack'] for i in range(2)]
        self.assertEqual(self.hit(0)['base_damage'],110)
        self.assertEqual(self.hit(1)['base_damage'],110)
        self.assertEqual([self.participant(i)['attack'] for i in range(2)],before)
        self.assertEqual(before,[100,100])

    def test_banished_warrior_no_longer_contributes(self):
        self.choose('warrior','warrior')
        with connect_mini_db(self.db) as conn:conn.execute('UPDATE mini_boss_participants SET banished=1 WHERE boss_id=? AND player_id=?',(self.boss['id'],self.players[1]['id']))
        self.assertEqual(self.hit(0)['base_damage'],105)

    def test_warrior_input_precedes_passive_and_faction(self):
        self.choose('warrior',passive='strong_start')
        self.update_boss(faction='beasts')
        result=self.hit(0)
        self.assertEqual(result['base_damage'],105)
        self.assertEqual(result['ability_damage'],210)
        self.assertEqual(result['damage_after_faction'],420)

    def test_secondary_extra_attack_does_not_repeat_class_or_input_bonus(self):
        self.choose('warrior',passive='blood_frenzy')
        for _ in range(3):self.hit(0);self.hit(1)
        result=self.hit(0)
        self.assertEqual(result['base_damage'],105)
        self.assertEqual(result['extra_damage'],100)

    def test_guardian_restores_one_existing_shield(self):
        self.choose('guardian')
        self.update_boss(reward_shields=1)
        with patch('app.mini.combat.classes.roll_success',return_value=True) as roll:
            result=self.hit(0)
        roll.assert_called_once_with(5)
        self.assertEqual(result['state']['boss']['reward_shields'],2)
        self.assertTrue(any(e['type']=='class_guardian' for e in result['boss_events']))

    def test_flying_miss_does_not_proc_guardian_or_mark_beast_success(self):
        self.choose('guardian',features='["flying"]')
        self.update_boss(reward_shields=1)
        with patch('app.mini.combat.classes.roll_success') as roll:
            result=self.hit(0)
        self.assertEqual(result['damage'],0);roll.assert_not_called()
        self.assertEqual(result['state']['boss']['reward_shields'],1)

    def test_sneaky_wallet_reward_and_counter_are_atomic(self):
        self.choose('sneaky')
        with patch('app.mini.combat.classes.roll_success',return_value=True):result=self.hit(0)
        self.assertEqual(result['state']['boss']['sneaky_stolen'],5)
        self.assertEqual(result['state']['real_reward_coins'],95)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),5)
        self.assertEqual(json.loads(self.participant(0)['hero_state_json'])['class_stolen_coins'],5)
        self.assertEqual(get_balance(self.players[1]['id'],self.db),0)

    def test_sneaky_restart_repeated_callback_and_settlement_do_not_double_pay(self):
        self.choose('sneaky')
        with patch('app.mini.combat.classes.roll_success',return_value=True):
            hit_boss(self.boss['id'],self.players[0]['id'],now=self.now,expected_round=1,expected_position=1,db_path=self.db)
        init_mini_db(self.db);init_boss_db(self.db)
        with self.assertRaises(ValueError):hit_boss(self.boss['id'],self.players[0]['id'],now=self.now,expected_round=1,expected_position=1,db_path=self.db)
        payout=force_finish_battle(self.boss['id'],now=self.now,db_path=self.db)
        self.assertEqual(payout['rewards']['coins_each'],95)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),100)
        self.assertEqual(get_balance(self.players[1]['id'],self.db),95)
        with self.assertRaises(ValueError):force_finish_battle(self.boss['id'],now=self.now,db_path=self.db)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),100)

    def test_sneaky_failed_wallet_write_rolls_back_attack_and_reward(self):
        self.choose('sneaky');before=self.participant(0)
        with connect_mini_db(self.db) as conn:conn.execute("CREATE TRIGGER fail_wallet BEFORE INSERT ON mini_wallet_transactions BEGIN SELECT RAISE(ABORT,'wallet'); END")
        with patch('app.mini.combat.classes.roll_success',return_value=True),self.assertRaises(sqlite3.IntegrityError):self.hit(0)
        self.assertEqual(self.participant(0),before)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),0)
        with connect_mini_db(self.db) as conn:self.assertEqual(conn.execute('SELECT sneaky_stolen FROM mini_bosses WHERE id=?',(self.boss['id'],)).fetchone()[0],0)

    def test_healer_repairs_only_boss_loss_and_survives_restart(self):
        self.choose('healer');self.update_boss(boss_damage=20,reward_percent=80,sneaky_stolen=5,reward_corruption=10)
        result=self.hit(0)
        b=result['state']['boss']
        self.assertEqual((b['boss_damage'],b['sneaky_stolen'],b['reward_corruption']),(15,5,10))
        self.assertEqual(result['state']['real_reward_coins'],80)
        init_mini_db(self.db);init_boss_db(self.db)
        from app.mini.boss.service import get_boss
        self.assertEqual(get_boss(self.boss['id'],self.db)['boss_damage'],15)

    def test_beast_progression_survives_restart_and_global_hero_switch(self):
        self.choose('beast')
        for n in range(10):
            self.assertEqual(self.hit(0)['damage'],100+2*n)
            if n==3:
                set_active_hero(self.players[0]['id'],self.villager,self.db)
                init_mini_db(self.db);init_boss_db(self.db)
            if n!=9:self.hit(1)
        self.assertEqual(json.loads(self.participant(0)['hero_state_json'])['class_successful_hits'],10)

    def test_magic_shield_removal_is_not_successful_hp_hit(self):
        self.choose('beast',ability='magic_shield')
        with patch('app.mini.combat.classes.roll_success') as roll:result=self.hit(0)
        self.assertEqual(result['damage'],0);roll.assert_not_called()
        self.assertNotIn('class_successful_hits',json.loads(self.participant(0)['hero_state_json']))

    def test_mage_uses_existing_magic_shield_mechanics(self):
        self.choose('mage',ability='magic_shield')
        result=self.hit(0)
        self.assertEqual(result['damage'],0)
        self.assertFalse(json.loads(result['state']['boss']['ability_state_json'])['shield_active'])

    def test_technical_blocks_construct_hp_regeneration(self):
        self.choose('technical',features='["construct"]')
        self.hit(0);result=self.hit(1)
        self.assertEqual(result['state']['boss']['current_hp'],99800)

    def test_old_saved_fighting_battle_keeps_old_class_rules(self):
        self.update_boss(class_rules_version=0)
        self.choose('warrior','warrior')
        before=self.participant(0)
        init_mini_db(self.db);init_boss_db(self.db)
        self.assertEqual(self.participant(0),before)
        self.assertEqual(self.hit(0)['base_damage'],100)

    def test_sneaky_lethal_hit_settles_only_remaining_reward_once(self):
        self.choose('sneaky');self.update_boss(current_hp=100,max_hp=100)
        with patch('app.mini.combat.classes.roll_success',return_value=True):result=self.hit(0)
        self.assertTrue(result['battle_ended'])
        self.assertEqual(result['rewards']['coins_each'],95)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),100)
        self.assertEqual(get_balance(self.players[1]['id'],self.db),0)
        init_mini_db(self.db);init_boss_db(self.db)
        repeated=hit_boss(self.boss['id'],self.players[0]['id'],now=self.now,expected_round=1,expected_position=1,db_path=self.db)
        self.assertFalse(repeated['applied'])
        self.assertEqual(get_balance(self.players[0]['id'],self.db),100)

    def test_healer_lethal_hit_restores_only_permanent_loss_before_payout(self):
        self.choose('healer');self.update_boss(current_hp=100,max_hp=100,boss_damage=20,reward_percent=80,sneaky_stolen=10,reward_corruption=10)
        result=self.hit(0)
        self.assertTrue(result['battle_ended'])
        self.assertEqual(result['rewards']['coins_each'],75)
        self.assertEqual(get_balance(self.players[0]['id'],self.db),75)
        self.assertEqual(result['state']['boss']['boss_damage'],15)
        self.assertEqual(result['state']['boss']['sneaky_stolen'],10)

    def test_beast_extra_hit_advances_ordinary_counter_once(self):
        self.choose('beast',passive='blood_frenzy')
        for _ in range(3):self.hit(0);self.hit(1)
        result=self.hit(0)
        self.assertEqual(result['extra_damage'],100)
        self.assertEqual(json.loads(self.participant(0)['hero_state_json'])['class_successful_hits'],4)

    def test_class_reward_changes_are_in_turn_publication_state_and_text(self):
        from app.mini.boss.public import _turn_state_key,format_public_turn
        self.choose('sneaky')
        with patch('app.mini.combat.classes.roll_success',return_value=True):result=self.hit(0)
        boss=result['state']['boss']
        before=dict(boss,sneaky_stolen=0)
        self.assertNotEqual(_turn_state_key(before),_turn_state_key(boss))
        with patch('app.config.DB_PATH',self.db):text=format_public_turn(boss,result['state']['participants'])
        self.assertIn('Состояние награды: 95%',text)
        self.assertIn('Реальная награда: 95 монет',text)
