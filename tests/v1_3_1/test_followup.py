"""Hidden administrative luck and shared active hero lifecycle regressions."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.v1_3.support import MiniCase
from app.mini.boss.combat import start_battle, get_combat_state, hit_boss
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    close_registration, create_boss_event, list_participants,
    register_player, select_battle_hero,
)
from app.mini.gacha import get_gacha_state, perform_gacha_pull
from app.mini.heroes import get_active_hero, get_collection_summary, get_player_hero, set_active_hero
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.superadmin.parser import parse_command
from app.mini.superadmin.service import execute
from app.mini.ui.heroes import _format_gacha, _gacha_menu, hero_active_callback
from app.mini.ui.hero_cards import hero_caption, hero_card_menu, hero_share_caption, show_gacha_pull_result
from app.mini.boss.selection import boss_select_hero_callback


class HiddenSuperluckTests(MiniCase):
    def enable(self):
        command = parse_command(f"/superluck {self.world['chat_id']}:{self.world['thread_id']} @tester")
        execute(self.owner, command, operation_key=f"luck:{len(self.sql('SELECT * FROM mini_superadmin_audit'))}", db_path=self.db)

    def assert_hidden(self, text):
        for secret in ('гаранти', 'superluck', 'forced_legendary'):
            self.assertNotIn(secret, text.lower())

    def test_menu_and_buttons_identical_before_and_after_override(self):
        ordinary = get_gacha_state(self.pid, self.db)
        self.enable()
        forced = get_gacha_state(self.pid, self.db)
        self.assertTrue(forced['forced_legendary'])
        self.assertEqual(forced['rarity_chances'], ordinary['rarity_chances'])
        self.assertEqual(_format_gacha(self.world, forced), _format_gacha(self.world, ordinary))
        self.assertEqual(_gacha_menu(1, 101, forced), _gacha_menu(1, 101, ordinary))
        self.assert_hidden(_format_gacha(self.world, forced))

    def test_preview_does_not_display_remaining_override_count(self):
        ordinary = get_gacha_state(self.pid, self.db)
        private_state = dict(ordinary, forced_legendary=True, guaranteed_pulls_remaining=789)
        self.assertEqual(_format_gacha(self.world, private_state), _format_gacha(self.world, ordinary))
        self.assertEqual(_gacha_menu(1, 101, private_state), _gacha_menu(1, 101, ordinary))

    def test_visible_potion_odds_remain_unchanged_by_override(self):
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?, 'gacha_luck', 1)", (self.pid,))
        ordinary = get_gacha_state(self.pid, self.db)
        self.enable()
        forced = get_gacha_state(self.pid, self.db)
        self.assertTrue(forced['luck_active'])
        self.assertEqual(_format_gacha(self.world, forced), _format_gacha(self.world, ordinary))

    def test_forced_new_and_duplicate_results_have_ordinary_caption_and_buttons(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                self.enable()
                with patch('app.mini.gacha.secrets.choice', side_effect=lambda choices: choices[0]):
                    result = perform_gacha_pull(self.pid, 'ticket', self.db)
                self.assertEqual(result['is_duplicate'], duplicate)
                hero = get_player_hero(self.pid, result['id'], self.db)
                ordinary = dict(result, forced_legendary=False)
                caption = hero_caption(hero, pull_result=result)
                self.assertEqual(caption, hero_caption(hero, pull_result=ordinary))
                self.assert_hidden(caption)
                self.assert_hidden(hero_share_caption(hero, '@tester'))
                state = get_gacha_state(self.pid, self.db)
                menu = hero_card_menu(1, 101, hero, dict(state, forced_legendary=True), allow_share=True)
                self.assertEqual(menu, hero_card_menu(1, 101, hero, dict(state, forced_legendary=False), allow_share=True))

    def test_ephemeral_result_and_toast_do_not_explain_forced_pull(self):
        self.enable()
        result = perform_gacha_pull(self.pid, 'ticket', self.db)
        callback = SimpleNamespace(from_user=SimpleNamespace(id=101), answer=AsyncMock())
        hero = get_player_hero(self.pid, result['id'], self.db)
        with patch('app.mini.ui.hero_cards.get_gacha_state', return_value=get_gacha_state(self.pid, self.db)), patch('app.mini.ui.hero_cards.get_player_hero', return_value=hero), patch('app.mini.ui.hero_cards.send_hero_card', new_callable=AsyncMock) as send:
            asyncio.run(show_gacha_pull_result(callback, self.world, self.player, result))
        send.assert_awaited_once()
        self.assert_hidden(send.call_args.args[3])
        for row in send.call_args.args[4].inline_keyboard:
            for button in row:
                self.assert_hidden(button.text)
                self.assert_hidden(button.callback_data)
        self.assert_hidden(callback.answer.call_args.args[0])
        self.assertEqual(result['rarity'], 'legendary')
        self.assertFalse(get_gacha_state(self.pid, self.db)['forced_legendary'])


class SharedActiveHeroTests(MiniCase):
    def setUp(self):
        super().setUp()
        init_boss_db(self.db)
        self.first = self.hero()['id']
        self.sql("""INSERT INTO mini_heroes(code,name,rarity,race,class_name,attack,
            passive_key,passive_text,faction,damage_type,class_tag,attack_range,special_trait)
            VALUES('followup_hero','Snapshot hero','rare','human','guard',10,
            'strong_start','Усиленный первый удар','commoners','slashing','guard','melee','none')""")
        self.second = self.sql("SELECT id FROM mini_heroes WHERE code='followup_hero'")[0]['id']
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,2)', (self.pid,self.second))
        self.other = create_mini_player(self.world['id'], 900124, '@other', 'Other', self.db)
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)', (self.other['id'],self.first))
        set_active_hero(self.other['id'], self.first, self.db)
        self.boss = create_boss_event(self.world['id'], 'training_golem', self.owner, self.db)
        self.bid = self.boss['id']
        self.sql("UPDATE mini_bosses SET min_players=2,max_hp=1000,current_hp=1000,faction='commoners',ability_key='none',features_json='[]',ability_config_json='{}' WHERE id=?", (self.bid,))
        register_player(self.bid, self.pid, self.db)
        register_player(self.bid, self.other['id'], self.db)
        self.now = datetime(2026,10,7,12,tzinfo=timezone.utc)

    def participant(self):
        return self.sql('SELECT * FROM mini_boss_participants WHERE boss_id=? AND player_id=?', (self.bid,self.pid))[0]

    def start(self):
        close_registration(self.bid,self.db)
        return start_battle(self.bid,now=self.now,db_path=self.db)

    def test_collection_callback_updates_active_hero_and_boss_prestart(self):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=900123),answer=AsyncMock())
        with patch('app.mini.ui.heroes._load_extended_context', new_callable=AsyncMock, return_value=(self.world,self.player,str(self.second))), patch('app.mini.ui.heroes.set_active_hero', side_effect=lambda p,h: set_active_hero(p,h,self.db)) as select, patch('app.mini.ui.heroes.get_player_hero', side_effect=lambda p,h: get_player_hero(p,h,self.db)), patch('app.mini.ui.heroes.get_gacha_state', side_effect=lambda p: get_gacha_state(p,self.db)), patch('app.mini.ui.heroes.send_hero_card', new_callable=AsyncMock), patch('app.mini.ui.heroes.is_favorite', return_value=False):
            asyncio.run(hero_active_callback(callback))
        select.assert_called_once_with(self.pid,self.second)
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],self.second)
        participant = list_participants(self.bid,self.db)[0]
        self.assertEqual(participant['battle_hero_id'],self.second)
        self.assertEqual(participant['hero_name'],'Snapshot hero')
        self.assertIsNone(self.participant()['hero_id'])

    def test_boss_callback_updates_global_and_collection(self):
        callback = SimpleNamespace(answer=AsyncMock())
        with patch('app.mini.boss.selection._hero_selection_context', new_callable=AsyncMock, return_value=(self.world,self.player,self.boss,self.second)), patch('app.mini.boss.selection.select_battle_hero', side_effect=lambda b,p,h: select_battle_hero(b,p,h,self.db)), patch('app.mini.boss.selection.show_boss_home', new_callable=AsyncMock):
            asyncio.run(boss_select_hero_callback(callback))
        summary = get_collection_summary(self.pid,self.db)
        active = [h['id'] for h in summary['heroes'] if h['is_active']]
        self.assertEqual(active,[self.second])
        self.assertEqual(list_participants(self.bid,self.db)[0]['battle_hero_id'],self.second)
        self.assertIsNone(self.participant()['hero_id'])
        self.assertIn('Активный герой', callback.answer.call_args.args[0])

    def test_ready_boss_tracks_collection_change_and_freezes_latest_active(self):
        select_battle_hero(self.bid,self.pid,self.second,self.db)
        close_registration(self.bid,self.db)
        set_active_hero(self.pid,self.first,self.db)
        self.assertEqual(list_participants(self.bid,self.db)[0]['battle_hero_id'],self.first)
        start_battle(self.bid,now=self.now,db_path=self.db)
        self.assertEqual(self.participant()['hero_id'],self.first)

    def test_legacy_prestart_selection_is_ignored_even_after_restart(self):
        self.sql('UPDATE mini_boss_participants SET hero_id=? WHERE boss_id=? AND player_id=?', (self.first,self.bid,self.pid))
        set_active_hero(self.pid,self.second,self.db)
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],self.second)
        self.assertEqual(list_participants(self.bid,self.db)[0]['battle_hero_id'],self.second)
        self.start()
        self.assertEqual(self.participant()['hero_id'],self.second)

    def test_snapshot_attack_traits_and_passive_survive_global_change_and_restart(self):
        select_battle_hero(self.bid,self.pid,self.second,self.db)
        self.start()
        before = self.participant()
        self.assertEqual(before['hero_id'],self.second)
        self.assertEqual(before['attack'],22)  # Two stars: 10 -> 15 -> 22.
        snapshot = json.loads(before['hero_snapshot_json'])
        self.assertEqual(snapshot['passive_key'],'strong_start')
        self.assertEqual(snapshot['damage_type'],'slashing')
        set_active_hero(self.pid,self.first,self.db)
        self.sql("UPDATE mini_heroes SET attack=9999,passive_key='none',faction='dark' WHERE id=?", (self.second,))
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],self.first)
        self.assertEqual(self.participant(),before)
        state = get_combat_state(self.bid,db_path=self.db)
        self.assertEqual(state['participants'][0]['battle_hero_id'],self.second)
        result = hit_boss(self.bid,self.pid,now=self.now,db_path=self.db)
        self.assertEqual(result['base_damage'],22)
        self.assertEqual(result['damage'],44)

    def test_old_fighting_snapshot_never_falls_back_to_global_active(self):
        self.start()
        self.sql("UPDATE mini_boss_participants SET hero_snapshot_json='{}',attack=7 WHERE boss_id=? AND player_id=?", (self.bid,self.pid))
        set_active_hero(self.pid,self.second,self.db)
        init_mini_db(self.db)
        init_boss_db(self.db)
        recovered = self.participant()
        self.assertEqual(recovered['hero_id'],self.first)
        self.assertEqual(recovered['attack'],7)
        frozen = recovered['hero_snapshot_json']
        set_active_hero(self.pid,self.first,self.db)
        self.assertEqual(self.participant()['hero_snapshot_json'],frozen)

    def test_selection_error_rolls_back_global_choice(self):
        with self.assertRaisesRegex(ValueError,'коллекции'):
            select_battle_hero(self.bid,self.pid,999999,self.db)
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],self.first)
        self.start()
        before = self.participant()
        with self.assertRaisesRegex(ValueError,'начала'):
            select_battle_hero(self.bid,self.pid,self.second,self.db)
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],self.first)
        self.assertEqual(self.participant(),before)

    def test_admin_delete_uses_current_prestart_selection_and_frozen_fighting_hero(self):
        select_battle_hero(self.bid,self.pid,self.second,self.db)
        def delete(code):
            command = parse_command(f"/superdel {self.world['chat_id']}:{self.world['thread_id']} @tester -p {code}")
            return execute(self.owner,command,operation_key=f"delete:{code}",db_path=self.db)
        with self.assertRaisesRegex(ValueError,'Boss'):
            delete('followup_hero')
        self.start()
        set_active_hero(self.pid,self.first,self.db)
        with self.assertRaisesRegex(ValueError,'Boss'):
            delete('followup_hero')
        delete('Villager')
        self.assertEqual(self.participant()['hero_id'],self.second)

    def test_concurrent_boss_selection_and_start_use_one_atomic_choice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        close_registration(self.bid,self.db)
        barrier = Barrier(2)
        def choose():
            barrier.wait()
            try:
                select_battle_hero(self.bid,self.pid,self.second,self.db)
                return True
            except ValueError as error:
                self.assertIn('начала',str(error))
                return False
        def start():
            barrier.wait()
            return start_battle(self.bid,now=self.now,db_path=self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            selected = pool.submit(choose)
            started = pool.submit(start)
            started.result()
            expected = self.second if selected.result() else self.first
        self.assertEqual(get_active_hero(self.pid,self.db)['id'],expected)
        self.assertEqual(self.participant()['hero_id'],expected)
        frozen = self.participant()['hero_snapshot_json']
        set_active_hero(self.pid,self.second if expected == self.first else self.first,self.db)
        self.assertEqual(self.participant()['hero_snapshot_json'],frozen)
