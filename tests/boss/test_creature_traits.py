
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN","test-token")
from app.mini.boss import combat
from app.mini.boss import runtime
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import create_boss_event, register_player, close_registration, get_boss, select_battle_hero
from app.mini.schema import init_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.wallet import get_balance
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.db import connect_mini_db
from app.mini.combat import creatures
from app.mini.boss.notices import combat_event_lines
from app.mini.boss.public import format_public_boss, format_public_turn
from tests.topic_fixtures import isolated_topics

class CreatureIntegrationFixture(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"test.db"
        init_mini_db(self.db); init_boss_db(self.db); sync_hero_catalog(self.db)
        world=sync_configured_mini_worlds(self.db)[0]["id"]
        self.players=[create_mini_player(world,97000+i,f"@trait{i}",f"Игрок {i}",self.db) for i in range(2)]
        with connect_mini_db(self.db) as conn:
            self.heroes=[r[0] for r in conn.execute("SELECT id FROM mini_heroes ORDER BY id LIMIT 2")]
            for player,hero in zip(self.players,self.heroes):
                conn.execute("INSERT INTO mini_player_heroes(player_id,hero_id,stars,copies) VALUES(?,?,0,1)",(player["id"],hero))
                conn.execute("UPDATE mini_players SET active_hero_id=? WHERE id=?",(hero,player["id"]))
                conn.execute("""UPDATE mini_heroes SET faction='commoners',attack=100,passive_key='none',
                                special_trait='none',class_tag='none',damage_type='slashing',attack_range='melee' WHERE id=?""",(hero,))
        self.event=create_boss_event(world,"training_golem",999,self.db)
        self.update(min_players=2,max_hp=1000,current_hp=1000,faction="commoners",ability_key="none",
                    ability_text="Нет особой способности.",features_json="[]",reward_coins=100,reward_items_json="[]",
                    reward_shields=3,reward_shields_max=3)
        for player in self.players:
            register_player(self.event["id"],player["id"],self.db)
        self.now=datetime(2026,10,6,12,tzinfo=timezone.utc)

    def update(self,**values):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_bosses SET "+", ".join(k+"=?" for k in values)+" WHERE id=?",
                         (*values.values(),self.event["id"]))

    def hero(self,index=0,**values):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_heroes SET "+", ".join(k+"=?" for k in values)+" WHERE id=?",
                         (*values.values(),self.heroes[index]))

    def start(self,*features,**values):
        self.update(features_json=json.dumps(features),**values)
        close_registration(self.event["id"],self.db)
        return combat.start_battle(self.event["id"],now=self.now,db_path=self.db)

    def boss(self):
        return get_boss(self.event["id"],self.db)

    def state(self):
        return json.loads(self.boss()["feature_state_json"])

    def seed(self,**values):
        current=self.state(); current.update(values)
        self.update(feature_state_json=json.dumps(current))

    def hit(self,index=0,**values):
        return combat.hit_boss(self.event["id"],self.players[index]["id"],now=self.now,db_path=self.db,**values)

    def round(self):
        self.hit(0); return self.hit(1)

    def events(self,kind):
        with connect_mini_db(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM mini_boss_events WHERE boss_id=? AND event_type=?",
                                (self.event["id"],kind)).fetchone()[0]

    def participant(self,index=0,**values):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET "+", ".join(k+"=?" for k in values)
                         +" WHERE boss_id=? AND player_id=?",(*values.values(),self.event["id"],self.players[index]["id"]))

class CreatureBattleTests(CreatureIntegrationFixture):
    def test_armored_pipeline_integration(self):
        self.start("armored")
        result=self.hit()
        self.assertEqual(result["hero_final_damage"],100); self.assertEqual(result["damage"],80)

    def test_flying_melee_block_consumes_turn_without_hit_effects(self):
        self.hero(special_trait="poisonous",passive_key="rune_spark",class_tag="mage")
        self.start("flying","undead",ability_key="magic_shield")
        with patch.object(creatures,"roll_success") as roll:
            result=self.hit()
        roll.assert_not_called()
        self.assertEqual(result["damage"],0)
        self.assertFalse(self.state().get("boss_poisoned"))
        self.assertTrue(json.loads(self.boss()["ability_state_json"])["shield_active"])
        self.assertEqual(self.boss()["current_turn_position"],2)
        self.assertTrue(any("досягаемости" in line for line in combat_event_lines(result)))

    def test_melee_flying_hero_can_hit(self):
        self.hero(special_trait="flying")
        self.start("flying")
        self.assertEqual(self.hit()["damage"],100)

    def test_ranged_holy_hits_flying_undead_and_nonstacking(self):
        for index in (0,1): self.hero(index,special_trait="holy",attack_range="ranged")
        self.start("flying","undead")
        self.hit(); self.assertTrue(self.state()["holy_miss_active"])
        with patch.object(creatures,"roll_success",return_value=False):
            self.hit(1)
        self.assertEqual(self.events("holy_applied"),1)
        self.assertFalse(self.state()["holy_miss_active"])

    def test_melee_holy_cannot_affect_flying_undead(self):
        self.hero(special_trait="holy")
        self.start("flying","undead")
        self.hit(); self.assertFalse(self.state().get("holy_miss_active",False))

    def test_undead_resurrection_then_victory_with_restart(self):
        self.start("undead",max_hp=100,current_hp=100)
        result=self.hit(); self.assertFalse(result["battle_ended"])
        self.assertEqual(self.boss()["current_hp"],25)
        init_boss_db(self.db)
        result=self.hit(1)
        self.assertTrue(result["battle_ended"]); self.assertEqual(result["rewards"]["coins_each"],100)
        self.assertEqual(self.events("undead_revived"),1)
        self.assertEqual(self.boss()["status"],"defeated")

    def test_callback_repetition_does_not_repeat_resurrection(self):
        self.start("undead",max_hp=100,current_hp=100)
        self.hit(expected_round=1,expected_position=1)
        with self.assertRaises(combat.BossNotYourTurn):
            self.hit(expected_round=1,expected_position=1)
        self.assertEqual(self.events("undead_revived"),1); self.assertEqual(self.boss()["current_hp"],25)

    def test_poison_ticks_once_on_next_boss_turn(self):
        self.hero(special_trait="poisonous")
        self.start()
        with patch.object(creatures,"roll_success",return_value=True):
            self.hit()
        self.assertEqual(self.boss()["current_hp"],900)
        init_boss_db(self.db)
        result=self.hit(1)
        self.assertEqual(self.boss()["current_hp"],780)
        self.assertFalse(self.state()["boss_poisoned"])
        self.assertEqual(self.events("poison_tick"),1)
        self.assertTrue(any("20 урона" in line for line in combat_event_lines(result)))
        with patch.object(creatures,"roll_success",return_value=False):
            self.hit(); self.hit(1)
        self.assertEqual(self.events("poison_tick"),1)

    def test_poison_kills_with_shared_victory_and_no_corruption(self):
        self.start("demonic")
        self.update(current_hp=210,reward_percent=80,reward_corruption=20)
        self.seed(boss_poisoned=True)
        result=self.round()
        self.assertEqual(self.boss()["status"],"defeated")
        self.assertEqual(result["rewards"]["coins_each"],80)
        self.assertEqual(self.boss()["reward_corruption"],0)
        self.assertEqual(self.boss()["reward_percent"],80)

    def test_poison_death_triggers_undead_resurrection(self):
        self.start("undead")
        self.update(current_hp=210); self.seed(boss_poisoned=True)
        result=self.round()
        self.assertFalse(result["battle_ended"]); self.assertEqual(self.boss()["current_hp"],250)
        self.assertEqual(self.events("undead_revived"),1)

    def test_echo_death_triggers_shared_resurrection(self):
        self.start("undead")
        self.participant(hero_state_json=json.dumps({"pending_echo_damage":1000}))
        # The actual engine key is covered/adjusted to the installed contract below.
        self.hero(passive_key="battle_echo")
        with connect_mini_db(self.db) as conn:
            snap=json.loads(conn.execute("SELECT hero_snapshot_json FROM mini_boss_participants WHERE boss_id=? AND player_id=?",
                                         (self.event["id"],self.players[0]["id"])).fetchone()[0])
            snap["passive_key"]="battle_echo"
        self.participant(hero_snapshot_json=json.dumps(snap))
        result=combat.advance_expired_turns(self.event["id"],now=self.now,db_path=self.db)
        self.assertEqual(self.boss()["current_hp"],250)
        self.assertEqual(self.events("undead_revived"),1)
        self.assertEqual(self.boss()["status"],"fighting")

    def test_construct_heals_after_full_round_only(self):
        self.start("construct")
        self.hit(); self.assertEqual(self.boss()["current_hp"],900)
        self.hit(1); self.assertEqual(self.boss()["current_hp"],900)
        self.assertEqual(self.events("construct_regeneration"),1)

    def test_construct_technical_counter_uses_frozen_snapshot(self):
        self.hero(class_tag="technical")
        self.start("construct")
        self.hero(class_tag="none")
        self.round()
        self.assertEqual(self.boss()["current_hp"],800)
        self.assertEqual(self.events("construct_regeneration"),0)

    def test_construct_banished_technical_does_not_counter(self):
        self.hero(class_tag="technical")
        self.start("construct")
        self.participant(banished=1)
        combat.advance_expired_turns(self.event["id"],now=self.now,db_path=self.db)
        self.hit(1)
        self.assertEqual(self.boss()["current_hp"],1000)

    def test_poisonous_shield_penetration_uses_decay_once(self):
        self.start("poisonous")
        self.round()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_percent"]),(2,90))
        self.update(reward_shields=0)
        self.round()
        self.assertEqual(self.boss()["reward_percent"],80)

    def test_poisonous_rapier_ignores_shields_without_double_decay(self):
        self.start("poisonous",ability_key="rapier")
        with patch("app.mini.boss.boss_abilities.engine._roll_success",return_value=True):
            self.round()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_percent"]),(3,90))

    def test_poisonous_critical_decay_twice_for_two_attacks(self):
        self.start("poisonous",ability_key="critical_strike")
        with patch("app.mini.boss.boss_abilities.engine._roll_success",return_value=True):
            self.round()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_percent"]),(1,80))

    def test_holy_regeneration_and_legacy_demon_counter(self):
        self.hero(special_trait="demon")
        self.start("holy")
        self.round()
        self.assertEqual(self.boss()["current_hp"],800)
        self.participant(banished=1)
        combat.advance_expired_turns(self.event["id"],now=self.now,db_path=self.db)
        self.hit(1)
        self.assertEqual(self.boss()["current_hp"],950)

    def test_holy_regeneration_caps_without_counter(self):
        self.start("holy")
        self.round()
        self.assertEqual(self.boss()["current_hp"],1000)

    def test_holy_miss_before_critical_rng_keeps_hydra_and_corruption(self):
        self.start("holy","demonic",ability_key="critical_strike")
        self.seed(holy_miss_active=True)
        with patch.object(creatures,"roll_success",return_value=True), patch("app.mini.boss.boss_abilities.engine._roll_success") as roll:
            result=self.round()
        roll.assert_not_called()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_percent"]),(3,100))
        self.assertEqual(self.boss()["current_hp"],1000)
        self.assertEqual(self.boss()["reward_corruption"],10)
        self.assertFalse(self.state()["holy_miss_active"])
        self.assertEqual(self.events("holy_miss"),1)

    def test_holy_miss_rapier_no_reward_attack_passives(self):
        self.start("undead",ability_key="rapier")
        self.seed(holy_miss_active=True)
        with patch.object(creatures,"roll_success",return_value=True), patch.object(runtime,"apply_boss_attack_passives") as passive, patch("app.mini.boss.boss_abilities.engine._roll_success") as roll:
            self.round()
        passive.assert_not_called(); roll.assert_not_called()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_percent"]),(3,100))

    def test_holy_miss_does_not_cancel_hydra(self):
        self.start("undead",ability_key="hydra_regeneration")
        self.seed(holy_miss_active=True)
        with patch.object(creatures,"roll_success",return_value=True): self.round()
        self.assertEqual(self.boss()["current_hp"],900)

    def test_holy_nonmiss_consumes_effect_and_attacks_normally(self):
        self.start("undead")
        self.seed(holy_miss_active=True)
        with patch.object(creatures,"roll_success",return_value=False): self.round()
        self.assertFalse(self.state()["holy_miss_active"])
        self.assertEqual(self.boss()["reward_shields"],2)

    def test_demonic_corruption_display_and_victory_payout(self):
        self.start("demonic")
        self.update(reward_percent=80)
        self.round()
        self.assertEqual(self.boss()["reward_corruption"],10)
        self.assertEqual(combat.get_combat_state(self.event["id"],db_path=self.db)["current_reward_coins"],70)
        self.assertIn("Порча: 10%",format_public_turn(self.boss(),[]))
        self.update(current_hp=1)
        result=self.hit()
        self.assertEqual(result["rewards"]["coins_each"],80)
        self.assertEqual(self.boss()["reward_corruption"],0)

    def test_corruption_zero_effective_reward_defeats(self):
        self.start("demonic")
        self.update(reward_percent=10)
        result=self.round()
        self.assertTrue(result["battle_ended"]); self.assertEqual(self.boss()["status"],"failed")
        self.assertEqual(result["rewards"]["shards_each"],10)

    def test_undead_buffer_absorbs_permanent_decay_and_never_pays_extra(self):
        for i in (0,1): self.hero(i,special_trait="undead")
        state=self.start("poisonous")
        self.assertEqual(state["effective_reward_percent"],115)
        self.round()
        self.assertEqual((self.boss()["reward_temp_hp"],self.boss()["reward_percent"]),(5,100))
        self.round()
        self.assertEqual((self.boss()["reward_temp_hp"],self.boss()["reward_percent"]),(0,95))
        self.update(current_hp=1)
        result=self.hit()
        self.assertEqual(result["rewards"]["coins_each"],95)

    def test_undead_unused_buffer_pays_at_most_original_reward(self):
        self.hero(special_trait="undead")
        self.start(max_hp=100)
        self.assertEqual(self.hit()["rewards"]["coins_each"],100)

    def test_armored_start_current_and_max_shields_once(self):
        for i in (0,1): self.hero(i,special_trait="armored")
        self.start()
        self.assertEqual((self.boss()["reward_shields"],self.boss()["reward_shields_max"]),(4,4))
        init_boss_db(self.db)
        with self.assertRaises(combat.BossCombatError):
            combat.start_battle(self.event["id"],now=self.now,db_path=self.db)
        self.assertEqual(self.boss()["reward_shields_max"],4)

    def test_construct_restores_one_shield_after_hero_action(self):
        self.hero(special_trait="construct")
        self.start(); self.update(reward_shields=1)
        with patch.object(creatures,"roll_success",return_value=True) as roll: self.hit()
        roll.assert_called_once_with(10)
        self.assertEqual(self.boss()["reward_shields"],2)
        self.assertEqual(self.events("construct_shield"),1)

    def test_endround_construct_runs_on_mockery_skip_once(self):
        self.hero(passive_key="mockery")
        self.start("construct","holy","demonic")
        self.participant(hit_count=2)
        self.round()
        self.assertEqual(self.events("construct_regeneration"),1)
        self.assertEqual(self.events("holy_regeneration"),0)
        self.assertEqual(self.boss()["reward_corruption"],0)

    def test_battle_state_survives_repeated_init_without_rerolls(self):
        self.hero(special_trait="undead")
        self.start("undead","demonic")
        self.seed(boss_undead_revived=True,boss_poisoned=True,holy_miss_active=True)
        self.update(reward_corruption=20,reward_shields=1,current_hp=345)
        before=self.boss()
        with patch.object(creatures,"roll_success") as roll:
            for _ in range(3): init_boss_db(self.db)
        roll.assert_not_called()
        after=self.boss()
        for key in ("trait_rules_version","feature_state_json","reward_temp_hp","reward_corruption",
                    "current_hp","reward_percent","reward_shields","reward_shields_max"):
            self.assertEqual(before[key],after[key])

    def test_transaction_rolls_back_trait_rng_effects_damage_and_journal(self):
        self.hero(special_trait="poisonous")
        self.start("undead")
        before=self.boss()
        with patch.object(creatures,"roll_success",return_value=True), patch.object(combat,"advance_after_turn",side_effect=RuntimeError("rollback")):
            with self.assertRaisesRegex(RuntimeError,"rollback"): self.hit()
        self.assertEqual(self.boss(),before)
        self.assertEqual(self.events("poison_applied"),0)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mini_boss_actions").fetchone()[0],0)

    def test_timeout_does_not_repeat_lifecycle_on_recovery(self):
        self.start("demonic","construct")
        moment=self.now+timedelta(hours=8)
        result=combat.advance_expired_turns(self.event["id"],now=moment,db_path=self.db)
        self.assertTrue(result["changed"])
        self.assertEqual(self.boss()["reward_corruption"],10)
        before=self.boss()
        result=combat.advance_expired_turns(self.event["id"],now=moment,db_path=self.db)
        self.assertFalse(result["changed"]); self.assertEqual(self.boss(),before)

    def test_started_hero_cannot_change_and_traits_are_snapshots(self):
        self.start("flying")
        self.hero(special_trait="flying")
        self.assertEqual(self.hit()["damage"],0)
        with self.assertRaises(ValueError):
            select_battle_hero(self.event["id"],self.players[0]["id"],self.heroes[0],self.db)

    def test_existing_v0_battle_keeps_old_features_inert(self):
        self.start("undead","armored","flying","demonic",trait_rules_version=0)
        self.assertEqual(self.hit()["damage"],100)
        self.update(current_hp=1)
        result=self.hit(1)
        self.assertTrue(result["battle_ended"]); self.assertEqual(self.events("undead_revived"),0)

    def test_explicit_mechanism_template_migration_preserves_old_event_snapshot(self):
        self.update(ability_key="mechanism",features_json='["construct","armored"]',trait_rules_version=0)
        init_boss_db(self.db)
        self.assertEqual(self.boss()["ability_key"],"mechanism")
        self.assertEqual(self.boss()["trait_rules_version"],0)

    def test_boss_card_all_features_and_rewards_visible_before_selection(self):
        self.update(features_json=json.dumps(sorted(creatures.features(dict(self.boss(),features_json='["armored","undead","construct","flying","poisonous","holy","demonic"]')))))
        caption=format_public_boss(self.boss(),[])
        self.assertIn("Особенности:",caption); self.assertIn("Награда:",caption); self.assertIn("Доп. награда:",caption)
        for value in ("Нежить","Конструкт","Летающий","Ядовитый","Святой","Демонический","Бронированный"):
            self.assertIn(value,caption)


    def test_old_schema_migration_preserves_every_entity_and_is_repeatable(self):
        self.start("undead",trait_rules_version=0)
        self.hit()
        self.update(current_hp=411,reward_percent=70,reward_shields=1)
        additions=("trait_rules_version","feature_state_json","reward_temp_hp","reward_corruption")
        before={k:v for k,v in self.boss().items() if k not in additions}
        with connect_mini_db(self.db) as conn:
            other={row[0]:conn.execute('SELECT * FROM "'+row[0]+'" ORDER BY rowid').fetchall()
                   for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mini_%'")
                   if row[0]!="mini_bosses"}
            for field in additions: conn.execute("ALTER TABLE mini_bosses DROP COLUMN "+field)
        for _ in range(3):init_boss_db(self.db)
        after=self.boss()
        self.assertEqual({k:v for k,v in after.items() if k not in additions},before)
        self.assertEqual(after["trait_rules_version"],0)
        self.assertEqual(after["feature_state_json"],"{}")
        with connect_mini_db(self.db) as conn:
            for table,rows in other.items():
                self.assertEqual(conn.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),rows,table)

    def test_fresh_schema_has_safe_defaults_for_legacy_insert(self):
        with connect_mini_db(self.db) as conn:
            defaults={row[1]:row[4] for row in conn.execute("PRAGMA table_info(mini_bosses)")}
        self.assertEqual(defaults["trait_rules_version"],"0")
        self.assertEqual(defaults["feature_state_json"],"'{}'")
        self.assertEqual(defaults["reward_temp_hp"],"0")
        self.assertEqual(defaults["reward_corruption"],"0")

    def test_poisonous_reward_guard_preserves_reward_and_shields(self):
        self.start("poisonous")
        self.participant(hero_state_json='{"reward_guard_charges":1}')
        result=self.round()
        self.assertEqual(self.boss()["reward_percent"],100)
        self.assertEqual(self.boss()["reward_shields"],3)
        self.assertEqual(result["reward_event"]["type"],"reward_guard")

    def test_undead_resurrection_delays_kamikaze_until_final_death(self):
        self.start("undead",ability_key="kamikaze",max_hp=100,reward_shields=0,reward_shields_max=0)
        result=self.hit()
        self.assertFalse(result["battle_ended"])
        self.assertEqual(self.boss()["reward_percent"],100)
        result=self.hit(1)
        self.assertEqual(self.boss()["status"],"failed")
        self.assertEqual(self.events("kamikaze_destroyed_reward"),1)

    def test_poison_kill_before_holy_and_corruption_and_round_healing(self):
        self.start("holy","demonic","construct")
        self.update(current_hp=210)
        self.seed(boss_poisoned=True)
        result=self.round()
        self.assertTrue(result["battle_ended"])
        self.assertEqual(self.boss()["status"],"defeated")
        self.assertEqual(self.events("holy_regeneration"),0)
        self.assertEqual(self.events("demonic_corruption"),0)
        self.assertEqual(self.events("construct_regeneration"),0)

    def test_hydra_then_poison_order_is_documented_and_deterministic(self):
        self.start(ability_key="hydra_regeneration")
        self.update(current_hp=210)
        self.seed(boss_poisoned=True)
        self.round()
        self.assertEqual(self.boss()["current_hp"],90)  # 210-200+100-20
        self.assertEqual(self.boss()["status"],"fighting")

    def test_mockery_skip_keeps_holy_and_poison_until_next_attempt(self):
        self.hero(passive_key="mockery")
        self.start("undead","construct")
        self.participant(hit_count=2)
        self.seed(holy_miss_active=True,boss_poisoned=True)
        with patch.object(creatures,"roll_success") as roll:self.round()
        roll.assert_not_called()
        self.assertTrue(self.state()["holy_miss_active"])
        self.assertTrue(self.state()["boss_poisoned"])
        self.assertEqual(self.events("poison_tick"),0)
        self.assertEqual(self.events("construct_regeneration"),1)

    def test_holy_miss_preserves_nondamaging_paralysis(self):
        self.start("undead",ability_key="paralysis")
        self.seed(holy_miss_active=True)
        with patch.object(creatures,"roll_success",return_value=True), patch(
            "app.mini.boss.boss_abilities.engine._choose_participant",side_effect=lambda p:p[-1]):
            self.round()
        self.assertEqual(self.events("paralysis"),1)
        self.assertEqual(self.boss()["reward_shields"],3)

    def test_banishment_immediately_removes_technical_counter(self):
        self.hero(class_tag="technical")
        self.start("construct",ability_key="banishment")
        self.update(ability_state_json='{"boss_turns":4}')
        with patch("app.mini.boss.boss_abilities.engine._roll_success",return_value=True), patch(
            "app.mini.boss.boss_abilities.engine._choose_participant",side_effect=lambda p:p[0]):
            self.round()
        self.assertEqual(self.boss()["current_hp"],900)
        self.assertEqual(self.events("banishment"),1)

    def test_construct_nonproc_and_full_cap_do_not_change_shields(self):
        self.hero(special_trait="construct")
        self.start()
        with patch.object(creatures,"roll_success") as roll:self.hit()
        roll.assert_not_called()
        self.assertEqual(self.boss()["reward_shields"],3)
        self.hit(1)
        with patch.object(creatures,"roll_success",return_value=False) as roll:self.hit()
        roll.assert_called_once_with(10)
        self.assertEqual(self.boss()["reward_shields"],2)

    def test_extra_attack_passes_armor_and_shared_resurrection(self):
        self.hero(passive_key="blood_frenzy")
        self.start("armored","undead")
        self.participant(hit_count=3)
        self.update(current_hp=130)
        result=self.hit()
        self.assertEqual(result["damage"],116)  # passive 145 -> armored 80%
        self.assertEqual(result["extra_damage"],14)
        self.assertFalse(result["battle_ended"])
        self.assertEqual(self.boss()["current_hp"],250)
        self.assertEqual(self.events("undead_revived"),1)

    def test_admin_victory_removes_corruption_but_preserves_decay(self):
        self.start("demonic")
        self.update(reward_percent=80,reward_corruption=60)
        result=combat.force_finish_battle(self.event["id"],now=self.now,db_path=self.db)
        self.assertEqual(result["rewards"]["coins_each"],80)
        self.assertEqual(self.boss()["reward_corruption"],0)
        self.assertEqual(self.boss()["reward_percent"],80)

    def test_duplicate_defeated_callback_never_grants_rewards_twice(self):
        self.start(max_hp=100)
        self.hit(expected_round=1,expected_position=1)
        before=get_balance(self.players[0]["id"],self.db)
        result=self.hit(expected_round=1,expected_position=1)
        self.assertFalse(result["applied"])
        self.assertEqual(get_balance(self.players[0]["id"],self.db),before)

    def test_failed_victory_rolls_back_resurrection_flags_and_payout(self):
        self.start("undead",max_hp=100)
        self.hit()
        before=self.boss()
        with patch("app.mini.boss.rewards.change_balance_in_transaction",side_effect=RuntimeError("wallet rollback")):
            with self.assertRaisesRegex(RuntimeError,"wallet rollback"):self.hit(1)
        self.assertEqual(self.boss(),before)
        self.assertEqual(self.events("undead_revived"),1)


    def test_temporary_reward_state_belongs_to_battle_only(self):
        with connect_mini_db(self.db) as conn:
            columns={r[1] for r in conn.execute("PRAGMA table_info(mini_boss_participants)")}
        self.assertFalse({"trait_rules_version","feature_state_json","reward_temp_hp","reward_corruption"} & columns)


    def test_card_distinguishes_real_payout_from_temporary_buffer(self):
        self.hero(special_trait="undead")
        self.start()
        caption=format_public_boss(self.boss(),[])
        self.assertIn("Награда: 100 монет",caption)
        self.assertIn("Запас награды: 115%",caption)
        self.assertNotIn("Награда: 115 монет",caption)

    def test_card_distinguishes_real_reward_from_demonic_effective_hp(self):
        self.start("demonic")
        self.update(reward_percent=80,reward_corruption=20)
        caption=format_public_boss(self.boss(),[])
        self.assertIn("Награда: 80 монет",caption)
        self.assertIn("Запас награды: 60%",caption)
        self.assertIn("Порча: 20%",caption)
