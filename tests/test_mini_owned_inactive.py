"""Gacha eligibility must never hide or disable an owned exclusive hero."""
import json
import unittest
from contextlib import ExitStack
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.v1_3.support import MiniCase
from app.mini import favorites
from app.mini.admin_grants import grant_mini_hero
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    close_registration, create_boss_event, list_participants,
    register_player, select_battle_hero,
)
from app.mini.boss.combat import start_battle
from app.mini.catalog import load_hero_catalog
from app.mini.gacha import get_gacha_state, perform_gacha_pull
from app.mini.hero_upgrades import calculate_attack, sell_hero_shards
from app.mini.heroes import (
    get_active_hero, get_collection_summary, get_player_hero,
    get_player_heroes, set_active_hero,
)
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.superadmin.parser import parse_command
from app.mini.superadmin.service import execute
from app.mini.tower.service import (
    attack, get_state, list_heroes, select_hero, start_attempt, start_selected_attempt,
)
from app.mini.ui import heroes as hero_ui
from app.mini.ui.hero_selector import (
    DIGITS, FIELDS, filtered, options, render_selector,
)

ENGINEER = 'panic_dungeon_engineer'


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def token_for(hero, fields=FIELDS):
    values = options()
    return ''.join(DIGITS[values[n].index(hero[field])+1] if field in fields else '0'
                   for n, field in enumerate(FIELDS)) + '0'


class OwnedInactiveCase(MiniCase):
    def setUp(self):
        super().setUp()
        self.villager = grant_mini_hero(self.pid, 'Villager', self.db)
        command = parse_command(
            f'/superadd {self.world["chat_id"]}:{self.world["thread_id"]} 900123 -p {ENGINEER}'
        )
        execute(self.owner, command, operation_key='exclusive-grant', db_path=self.db)
        self.engineer = next(h for h in get_player_heroes(self.pid, self.db) if h['code'] == ENGINEER)
        self.eid = self.engineer['id']


class OwnedInactiveTests(OwnedInactiveCase):
    def test_admin_owned_hero_stays_inactive_and_collection_counts_only_public_pool(self):
        catalog = load_hero_catalog()['heroes']
        self.assertFalse(next(h for h in catalog if h['code'] == ENGINEER)['active'])
        summary = get_collection_summary(self.pid, self.db)
        state = get_gacha_state(self.pid, self.db)
        expected = sum(bool(h.get('active', True)) for h in catalog)
        self.assertEqual(summary['owned'], 2)
        self.assertEqual(summary['total_active'], expected)
        self.assertEqual(state['total'], expected)
        self.assertEqual(state['rarity_counts']['rare'],
                         sum(h['active'] and h['rarity'] == 'rare' for h in catalog))
        owned = next(h for h in summary['heroes'] if h['code'] == ENGINEER)
        self.assertEqual(owned['active'], 0)
        self.assertNotIn('inactive', hero_ui._format_collection(self.world, self.player, summary))

    def test_forced_legendary_pool_still_requires_gacha_active(self):
        price = load_hero_catalog()['settings']['pull_price']
        # Also exercise the active predicate within the Legendary tier itself.
        # The second subcase only changes a disposable DB fixture, never the catalog.
        for rarity in ('rare', 'legendary'):
            with self.subTest(rarity=rarity):
                self.sql('UPDATE mini_heroes SET rarity=? WHERE id=?', (rarity, self.eid))
                self.sql('UPDATE mini_players SET coins=? WHERE id=?', (price, self.pid))
                self.sql('INSERT OR REPLACE INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)', (self.pid,))
                expected = {h['code'] for h in self.sql("SELECT code FROM mini_heroes WHERE rarity='legendary' AND active=1")}
                def choose(pool):
                    codes = {row[0] for row in pool}
                    self.assertEqual(codes, expected)
                    self.assertNotIn(ENGINEER, codes)
                    return pool[-1]
                with patch('app.mini.gacha.sync_hero_catalog'), patch('app.mini.gacha.secrets.choice', side_effect=choose):
                    result = perform_gacha_pull(self.pid, db_path=self.db)
                self.assertEqual(result['rarity'], 'legendary')
                self.assertNotEqual(result['code'], ENGINEER)
                self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees WHERE player_id=?', (self.pid,))[0]['forced_legendary'], 0)

    def test_shared_selector_keeps_active_and_inactive_owned_heroes_in_every_mode(self):
        heroes = get_player_heroes(self.pid, self.db)
        for mode in ('c', 'b', 't'):
            for view in ('all', 'home', 'results'):
                with self.subTest(mode=mode, view=view):
                    _, markup = render_selector(heroes, [self.eid, self.villager['id']], mode,
                                                self.world['id'], 900123, 1, view=view)
                    labels = [b.text for b in buttons(markup)]
                    self.assertTrue(any(self.engineer['name'][:27] in label for label in labels))
                    self.assertTrue(any(self.villager['name'][:27] in label for label in labels))
                    self.assertTrue(all(len(b.callback_data.encode()) <= 64 for b in buttons(markup)))
                    self.assertFalse(any('inactive' in label for label in labels))

    def test_all_tag_filters_and_upgrade_filter_include_owned_inactive(self):
        self.sql('UPDATE mini_players SET shards=10000 WHERE id=?', (self.pid,))
        heroes = get_player_heroes(self.pid, self.db)
        for mode in ('c', 'b', 't'):
            for fields in [FIELDS] + [(field,) for field in FIELDS]:
                with self.subTest(mode=mode, fields=fields):
                    token = token_for(self.engineer, fields)
                    self.assertIn(self.eid, [h['id'] for h in filtered(heroes, token, mode)])
                    _, markup = render_selector(heroes, [], mode, self.world['id'], 900123, 1,
                                                view='results', token=token)
                    self.assertTrue(any(self.engineer['name'][:27] in b.text for b in buttons(markup)))
        self.assertIn(self.eid, [h['id'] for h in filtered(heroes, '0000001')])

    def test_inactive_favorites_add_remove_replace_and_restart(self):
        self.assertTrue(favorites.add_favorite(self.pid, self.eid, self.db))
        init_mini_db(self.db)
        self.assertEqual(favorites.get_favorites(self.pid, self.db), [self.eid])
        self.assertTrue(favorites.remove_favorite(self.pid, self.eid, self.db))
        favorites.add_favorite(self.pid, self.villager['id'], self.db)
        self.assertTrue(favorites.replace_favorite(self.pid, self.villager['id'], self.eid, self.db))
        self.assertEqual(favorites.get_favorites(self.pid, self.db), [self.eid])
        self.assertTrue(favorites.replace_favorite(self.pid, self.eid, self.villager['id'], self.db))

    def test_inactive_global_active_selection_and_collection_marker(self):
        set_active_hero(self.pid, self.eid, self.db)
        init_mini_db(self.db)
        self.assertEqual(get_active_hero(self.pid, self.db)['code'], ENGINEER)
        heroes = get_player_heroes(self.pid, self.db)
        for mode in ('c', 'b', 't'):
            _, markup = render_selector(heroes, [self.eid], mode, self.world['id'], 900123, 1)
            label = markup.inline_keyboard[0][0].text
            self.assertEqual(label.endswith(' ✅'), mode == 'c')
        self.assertEqual(get_player_hero(self.pid, self.eid, self.db)['active'], 0)

    def test_inactive_tower_selection_restart_and_frozen_snapshot(self):
        self.sql('UPDATE mini_player_heroes SET stars=2 WHERE player_id=? AND hero_id=?', (self.pid, self.eid))
        self.assertIn(self.eid, [h['id'] for h in list_heroes(self.pid, self.db)])
        selected = select_hero(self.pid, self.eid, self.db, expected_floor=1)
        self.assertEqual(selected['active'], 0)
        init_mini_db(self.db)
        self.assertEqual(get_state(self.pid, self.db)['selected_hero']['id'], self.eid)
        attempt = start_selected_attempt(self.pid, 1, self.eid, self.db)
        snapshot = json.loads(attempt['hero_json'])
        self.assertEqual((snapshot['code'], snapshot['active'], snapshot['stars']), (ENGINEER, 0, 2))
        self.assertEqual(snapshot['attack'], calculate_attack(self.engineer['base_attack'], 2))
        for field in FIELDS + ('passive_key', 'passive_text'):
            self.assertEqual(snapshot[field], self.engineer[field])
        set_active_hero(self.pid, self.villager['id'], self.db)
        select_hero(self.pid, self.villager['id'], self.db)
        self.sql('UPDATE mini_player_heroes SET stars=3 WHERE player_id=? AND hero_id=?', (self.pid, self.eid))
        init_mini_db(self.db)
        self.assertEqual(get_state(self.pid, self.db)['attempt']['hero_json'], attempt['hero_json'])

    def test_legacy_tower_start_accepts_inactive_owned_hero_and_passive_runs(self):
        attempt = start_attempt(self.pid, self.eid, 1, self.db)
        enemy = json.loads(attempt['enemy_json'])
        enemy.update(max_hp=100000, faction='neutral', response='attack', features=[])
        self.sql('UPDATE mini_tower_attempts SET current_hp=100000,enemy_json=? WHERE id=?',
                 (json.dumps(enemy), attempt['id']))
        result = attack(self.pid, attempt['id'], 0, self.db, roller=lambda _: True)
        runtime = json.loads(result['attempt']['runtime_json'])
        self.assertEqual(runtime['bonus_shards'], 2)
        self.assertIn('Аварийный ремонт', result['attempt']['events_json'])
        self.assertLess(result['attempt']['current_hp'], 100000)

    def test_inactive_owned_shards_can_be_sold(self):
        self.sql('UPDATE mini_players SET shards=10 WHERE id=?', (self.pid,))
        result = sell_hero_shards(self.pid, self.eid, 4, db_path=self.db, operation_key='exclusive-sale')
        self.assertEqual(result['coins_earned'], 4)
        self.assertEqual(get_player_hero(self.pid, self.eid, self.db)['shards'], 6)

    def test_unowned_inactive_cannot_bypass_collection_favorites_active_or_tower(self):
        other = create_mini_player(self.world['id'], 900124, '@other', 'Другой', self.db)['id']
        self.assertIsNone(get_player_hero(other, self.eid, self.db))
        self.assertNotIn(self.eid, [h['id'] for h in list_heroes(other, self.db)])
        for mode in ('c', 'b', 't'):
            _, markup = render_selector(get_player_heroes(other, self.db), [self.eid], mode,
                                        self.world['id'], 900124, 1, view='all')
            self.assertFalse(any(self.engineer['name'][:27] in b.text for b in buttons(markup)))
        for operation in (
            lambda: favorites.add_favorite(other, self.eid, self.db),
            lambda: set_active_hero(other, self.eid, self.db),
            lambda: select_hero(other, self.eid, self.db),
            lambda: start_attempt(other, self.eid, 1, self.db),
            lambda: start_selected_attempt(other, 1, self.eid, self.db),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertIsNone(get_state(other, self.db)['attempt'])

    def test_inactive_boss_choice_accepts_owner_rejects_nonowner_and_freezes(self):
        init_boss_db(self.db)
        boss = create_boss_event(self.world['id'], 'training_golem', self.owner, self.db)
        players = [self.pid]
        for n in range(max(1, boss['min_players'] - 1)):
            other = create_mini_player(self.world['id'], 900124 + n, f'@other_{n}', 'Другой', self.db)
            grant_mini_hero(other['id'], 'Villager', self.db)
            players.append(other['id'])
        for pid in players:
            register_player(boss['id'], pid, self.db)
        for ready in (False, True):
            if ready:
                close_registration(boss['id'], self.db)
            selected = select_battle_hero(boss['id'], self.pid, self.eid, self.db)
            self.assertEqual(selected['active'], 0)
            self.assertEqual(get_active_hero(self.pid, self.db)['id'], self.eid)
            with self.assertRaises(ValueError):
                select_battle_hero(boss['id'], players[1], self.eid, self.db)
        start_battle(boss['id'], db_path=self.db)
        participant = next(p for p in list_participants(boss['id'], self.db) if p['player_id'] == self.pid)
        snapshot = json.loads(participant['hero_snapshot_json'])
        self.assertEqual(participant['battle_hero_id'], self.eid)
        self.assertEqual(snapshot['passive_key'], 'emergency_salvage')
        set_active_hero(self.pid, self.villager['id'], self.db)
        init_mini_db(self.db)
        restored = next(p for p in list_participants(boss['id'], self.db) if p['player_id'] == self.pid)
        self.assertEqual(restored['hero_snapshot_json'], participant['hero_snapshot_json'])
        with self.assertRaises(ValueError):
            select_battle_hero(boss['id'], self.pid, self.villager['id'], self.db)

    def test_superadmin_all_still_grants_inactive_without_duplicates(self):
        other = create_mini_player(self.world['id'], 900124, '@other', 'Другой', self.db)['id']
        command = parse_command(f'/superadd {self.world["chat_id"]}:{self.world["thread_id"]} ALL -p {ENGINEER}')
        report = execute(self.owner, command, operation_key='all-exclusive', db_path=self.db)
        self.assertIn('Успешно: 1', report)
        self.assertIn('Пропущено: 1', report)
        self.assertEqual(get_player_hero(other, self.eid, self.db)['active'], 0)
        self.assertEqual(get_player_hero(self.pid, self.eid, self.db)['copies'], 1)


class OwnedInactiveUITests(OwnedInactiveCase, unittest.IsolatedAsyncioTestCase):
    def callback(self, action, tail):
        return SimpleNamespace(id='exclusive-ui', data=f'mini:{action}:{self.world["id"]}:900123:{tail}',
            from_user=SimpleNamespace(id=900123, username='tester'), answer=AsyncMock(),
            message=SimpleNamespace(ephemeral_message_id='old', delete_ephemeral=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock()))

    def services(self):
        stack = ExitStack()
        stack.enter_context(patch('app.mini.ui.heroes._load_extended_context',
            side_effect=lambda cb, prefix: (self.world, self.player, cb.data.split(':', 4)[4])))
        for name in ('get_favorites', 'add_favorite', 'remove_favorite', 'replace_favorite', 'is_favorite'):
            stack.enter_context(patch(f'app.mini.ui.heroes.{name}',
                side_effect=partial(getattr(favorites, name), db_path=self.db)))
        for name, function in (('get_player_hero', get_player_hero), ('get_gacha_state', get_gacha_state)):
            stack.enter_context(patch(f'app.mini.ui.heroes.{name}', side_effect=partial(function, db_path=self.db)))
        stack.enter_context(patch('app.mini.ui.hero_cards.get_hero_image', return_value=None))
        return stack

    async def test_inactive_owned_card_is_accessible_and_favorite_callback_adds_removes(self):
        cb = self.callback('hero', str(self.eid))
        with self.services():
            await hero_ui.hero_card_callback(cb)
        self.assertIn(self.engineer['name'], cb.bot.send_message.call_args.kwargs['text'])
        self.assertIn('⭐ Сделать избранным', [b.text for b in buttons(cb.bot.send_message.call_args.kwargs['reply_markup'])])
        for action in ('add', 'remove'):
            cb = self.callback('favorite', f'{action}.{self.eid}')
            with self.services():
                await hero_ui.favorite_callback(cb)
            cb.bot.send_message.assert_awaited_once()
            self.assertEqual(favorites.is_favorite(self.pid, self.eid, self.db), action == 'add')

    async def test_inactive_fourth_favorite_callback_replaces_existing(self):
        ids = [self.villager['id']]
        for row in self.sql('SELECT code FROM mini_heroes WHERE active=1 AND id!=? ORDER BY id LIMIT 2', (self.villager['id'],)):
            ids.append(grant_mini_hero(self.pid, row['code'], self.db)['id'])
        for identifier in ids:
            favorites.add_favorite(self.pid, identifier, self.db)
        cb = self.callback('favorite', f'add.{self.eid}')
        with self.services():
            await hero_ui.favorite_callback(cb)
        self.assertEqual(cb.bot.send_message.call_args.kwargs['text'], '⭐ Кого заменить?')
        cb = self.callback('favorite', f'replace.{self.eid}.{ids[1]}')
        with self.services():
            await hero_ui.favorite_callback(cb)
        self.assertEqual(favorites.get_favorites(self.pid, self.db), [ids[0], self.eid, ids[2]])

    async def test_unowned_inactive_card_and_favorite_callbacks_rejected(self):
        self.sql('DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?', (self.pid, self.eid))
        for action, tail, handler in (
            ('hero', str(self.eid), hero_ui.hero_card_callback),
            ('favorite', f'add.{self.eid}', hero_ui.favorite_callback),
        ):
            cb = self.callback(action, tail)
            with self.services():
                await handler(cb)
            cb.bot.send_message.assert_not_awaited()
            self.assertTrue(cb.answer.call_args.kwargs['show_alert'])

    async def test_inactive_owned_hero_can_be_shared(self):
        cb = self.callback('heroshare', str(self.eid))
        with self.services(), patch('app.mini.ui.heroes.format_player_mention', return_value='@tester'), \
             patch('app.mini.ui.heroes.send_public_hero_share', new_callable=AsyncMock) as share:
            await hero_ui.hero_share_callback(cb)
        share.assert_awaited_once()
        self.assertEqual(share.call_args.args[2]['code'], ENGINEER)
        self.assertEqual(share.call_args.args[2]['active'], 0)
