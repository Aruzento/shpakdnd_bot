import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.combat import (
    BossNotYourTurn,
    advance_expired_turns,
    force_finish_battle,
    hit_boss,
    start_battle,
)
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    close_registration,
    create_boss_event,
    get_boss,
    list_participants,
    register_player,
)
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player, get_mini_player
from app.mini.items import (
    EFFECT_BOSS_DAMAGE,
    EFFECT_BOSS_PHANTOM,
    get_effect_charges,
    use_inventory_item,
)
from app.mini.schema import init_mini_db
from app.mini.shop import get_player_goods
from app.mini.worlds import sync_configured_mini_worlds


UTC = timezone.utc


class MiniBossCombatTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.world_id = sync_configured_mini_worlds(self.db)[0]["id"]
        sync_hero_catalog(self.db)
        self.player1 = self._create_player(801, "@fighter_one", "Первый")
        self.player2 = self._create_player(802, "@fighter_two", "Второй")
        self.boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        # Боевая фикстура не зависит от баланса живого bosses.json.
        with connect_mini_db(self.db) as conn:
            conn.execute(
                """
                UPDATE mini_bosses
                SET min_players = 2,
                    reward_coins = 60,
                    reward_items_json = ?,
                    reward_shields = 3,
                    reward_shields_max = 3,
                    reward_decay_percent = 10,
                    faction = 'commoners',
                    ability_key = 'none',
                    features_json = '[]',
                    ability_config_json = '{}'
                WHERE id = ?
                """,
                (
                    json.dumps(
                        [
                            {"code": "boss_coin_pouch", "quantity": 1},
                            {"code": "boss_shard_casket", "quantity": 1},
                        ],
                        ensure_ascii=False,
                    ),
                    self.boss["id"],
                ),
            )
            conn.commit()
        register_player(self.boss["id"], self.player1["id"], self.db)
        register_player(self.boss["id"], self.player2["id"], self.db)
        close_registration(self.boss["id"], self.db)
        self.start_time = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_player(self, user_id: int, username: str, name: str) -> dict:
        player = create_mini_player(
            self.world_id, user_id, username, name, self.db
        )
        with connect_mini_db(self.db) as conn:
            hero_id = int(
                conn.execute(
                    "SELECT id FROM mini_heroes WHERE code = 'Villager'"
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO mini_player_heroes (player_id, hero_id, copies, shards, stars)
                VALUES (?, ?, 1, 0, 0)
                """,
                (player["id"], hero_id),
            )
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                (hero_id, player["id"]),
            )
            conn.commit()
        return player

    def _grant_item(self, player_id: int, code: str, quantity: int = 1) -> int:
        with connect_mini_db(self.db) as conn:
            row = conn.execute(
                "SELECT id FROM mini_items WHERE code = ?", (code,)
            ).fetchone()
            self.assertIsNotNone(row, code)
            item_id = int(row[0])
            conn.execute(
                """
                INSERT INTO mini_inventory (player_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id, item_id) DO UPDATE SET
                    quantity = mini_inventory.quantity + excluded.quantity
                """,
                (player_id, item_id, quantity),
            )
            conn.commit()
        return item_id

    def _start(self):
        return start_battle(
            self.boss["id"], now=self.start_time, db_path=self.db
        )

    def test_start_snapshots_attack_and_first_turn(self):
        state = self._start()
        self.assertEqual(state["boss"]["status"], "fighting")
        self.assertEqual(state["boss"]["current_turn_position"], 1)
        self.assertEqual(state["current"]["player_id"], self.player1["id"])
        participants = list_participants(self.boss["id"], self.db)
        self.assertEqual([p["battle_attack"] for p in participants], [3, 3])
        self.assertTrue(all(p["battle_hero_id"] for p in participants))

    def test_turn_order_and_end_of_round_breaks_shield(self):
        self._start()
        first = hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        self.assertEqual(first["damage"], 3)
        self.assertEqual(first["state"]["boss"]["current_turn_position"], 2)
        self.assertEqual(first["state"]["boss"]["reward_shields"], 3)

        second = hit_boss(
            self.boss["id"], self.player2["id"],
            now=self.start_time + timedelta(minutes=2), db_path=self.db,
        )
        self.assertEqual(second["state"]["boss"]["current_round"], 2)
        self.assertEqual(second["state"]["boss"]["current_turn_position"], 1)
        self.assertEqual(second["state"]["boss"]["reward_shields"], 2)
        self.assertEqual(second["reward_event"]["type"], "shield")

    def test_wrong_player_cannot_hit(self):
        self._start()
        with self.assertRaises(BossNotYourTurn):
            hit_boss(
                self.boss["id"], self.player2["id"],
                now=self.start_time + timedelta(minutes=1), db_path=self.db,
            )

    def test_four_hour_timeout_skips_player(self):
        self._start()
        result = advance_expired_turns(
            self.boss["id"],
            now=self.start_time + timedelta(hours=4, seconds=1),
            db_path=self.db,
        )
        self.assertTrue(result["changed"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["player_id"], self.player1["id"])
        self.assertEqual(result["state"]["current"]["player_id"], self.player2["id"])

    def test_timeout_can_advance_whole_round(self):
        self._start()
        result = advance_expired_turns(
            self.boss["id"],
            now=self.start_time + timedelta(hours=8, seconds=1),
            db_path=self.db,
        )
        self.assertEqual(len(result["skipped"]), 2)
        self.assertEqual(result["state"]["boss"]["current_round"], 2)
        self.assertEqual(result["state"]["boss"]["reward_shields"], 2)
        self.assertEqual(result["state"]["current"]["player_id"], self.player1["id"])

    def test_victory_rewards_only_players_who_hit_by_default(self):
        self._start()
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_bosses SET current_hp = 1 WHERE id = ?",
                (self.boss["id"],),
            )
            conn.commit()

        result = hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        self.assertTrue(result["battle_ended"])
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(result["rewards"]["coins_each"], 60)
        self.assertEqual(result["rewards"]["players"], 1)
        self.assertEqual(result["rewards"]["missed_players"], 1)

        fighter = get_mini_player(
            self.world_id, self.player1["telegram_user_id"], self.db
        )
        self.assertEqual(fighter["coins"], 60)
        goods = get_player_goods(self.player1["id"], self.db)
        names = {item["name"]: item["quantity"] for item in goods["inventory"]}
        self.assertEqual(names["Кошель с монетами"], 1)
        self.assertEqual(names["Шкатулка с осколками"], 1)

        spectator = get_mini_player(
            self.world_id, self.player2["telegram_user_id"], self.db
        )
        self.assertEqual(spectator["coins"], 0)
        self.assertEqual(get_player_goods(self.player2["id"], self.db)["inventory"], [])

    def test_phantom_potion_rewards_registered_player_without_a_hit(self):
        item_id = self._grant_item(
            self.player2["id"], "phantom_participation_potion"
        )
        use_inventory_item(
            self.player2["id"], item_id,
            operation_key="phantom-before-battle", db_path=self.db,
        )
        self.assertEqual(
            get_effect_charges(
                self.player2["id"], EFFECT_BOSS_PHANTOM, self.db
            ),
            1,
        )

        self._start()
        self.assertEqual(
            get_effect_charges(
                self.player2["id"], EFFECT_BOSS_PHANTOM, self.db
            ),
            0,
        )
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_bosses SET current_hp = 1 WHERE id = ?",
                (self.boss["id"],),
            )
            conn.commit()

        result = hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        self.assertEqual(result["rewards"]["players"], 2)
        self.assertEqual(result["rewards"]["missed_players"], 0)
        player2 = get_mini_player(
            self.world_id, self.player2["telegram_user_id"], self.db
        )
        self.assertEqual(player2["coins"], 60)
        self.assertEqual(
            len(get_player_goods(self.player2["id"], self.db)["inventory"]), 2
        )

    def test_damage_potion_applies_after_other_damage_modifiers(self):
        item_id = self._grant_item(self.player1["id"], "damage_potion")
        use_inventory_item(
            self.player1["id"], item_id,
            operation_key="damage-before-battle", db_path=self.db,
        )
        self._start()
        self.assertEqual(
            get_effect_charges(
                self.player1["id"], EFFECT_BOSS_DAMAGE, self.db
            ),
            0,
        )
        result = hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        self.assertEqual(result["ability_damage"], 3)
        self.assertEqual(result["damage_bonus_percent"], 10)
        self.assertEqual(result["damage"], 4)
        self.assertTrue(
            any(
                event.get("type") == "item_damage_boost"
                for event in result["passive_events"]
            )
        )

    def test_damaged_reward_scales_coins_but_keeps_item_reward(self):
        self._start()
        with connect_mini_db(self.db) as conn:
            conn.execute(
                """
                UPDATE mini_bosses
                SET current_hp = 4, reward_shields = 0, reward_percent = 50
                WHERE id = ?
                """,
                (self.boss["id"],),
            )
            conn.commit()

        hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        result = hit_boss(
            self.boss["id"], self.player2["id"],
            now=self.start_time + timedelta(minutes=2), db_path=self.db,
        )
        self.assertEqual(result["rewards"]["coins_each"], 30)
        for original in (self.player1, self.player2):
            player = get_mini_player(self.world_id, original["telegram_user_id"], self.db)
            self.assertEqual(player["coins"], 30)
            self.assertEqual(len(get_player_goods(original["id"], self.db)["inventory"]), 2)

    def test_admin_force_finish_rewards_every_registered_player(self):
        self._start()
        with connect_mini_db(self.db) as conn:
            conn.execute(
                """
                UPDATE mini_bosses
                SET reward_percent = 50
                WHERE id = ?
                """,
                (self.boss["id"],),
            )
            conn.commit()

        result = force_finish_battle(
            self.boss["id"],
            now=self.start_time + timedelta(minutes=1),
            db_path=self.db,
        )

        self.assertTrue(result["battle_ended"])
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(
            result["state"]["boss"]["battle_result"],
            "admin_victory",
        )
        self.assertEqual(result["state"]["boss"]["current_hp"], 0)
        self.assertEqual(result["rewards"]["players"], 2)
        self.assertEqual(result["rewards"]["missed_players"], 0)
        self.assertEqual(result["rewards"]["coins_each"], 30)

        for original in (self.player1, self.player2):
            player = get_mini_player(
                self.world_id,
                original["telegram_user_id"],
                self.db,
            )
            self.assertEqual(player["coins"], 30)
            goods = get_player_goods(original["id"], self.db)
            names = {
                item["name"]: item["quantity"]
                for item in goods["inventory"]
            }
            self.assertEqual(names["Кошель с монетами"], 1)
            self.assertEqual(names["Шкатулка с осколками"], 1)

    def test_reward_zero_loses_battle_and_grants_consolation_shards(self):
        self._start()
        with connect_mini_db(self.db) as conn:
            conn.execute(
                """
                UPDATE mini_bosses
                SET reward_shields = 0, reward_percent = 10
                WHERE id = ?
                """,
                (self.boss["id"],),
            )
            conn.commit()

        hit_boss(
            self.boss["id"], self.player1["id"],
            now=self.start_time + timedelta(minutes=1), db_path=self.db,
        )
        result = hit_boss(
            self.boss["id"], self.player2["id"],
            now=self.start_time + timedelta(minutes=2), db_path=self.db,
        )
        self.assertTrue(result["battle_ended"])
        self.assertEqual(result["state"]["boss"]["status"], "failed")
        self.assertEqual(result["rewards"]["shards_each"], 6)

        for original in (self.player1, self.player2):
            player = get_mini_player(self.world_id, original["telegram_user_id"], self.db)
            self.assertEqual(player["shards"], 6)
            self.assertEqual(player["coins"], 0)
            self.assertEqual(get_player_goods(original["id"], self.db)["inventory"], [])

    def test_reward_wallet_failure_rolls_back_all_players_and_battle(self):
        from unittest.mock import patch
        from app.mini.boss import rewards
        start_battle(self.boss["id"], now=self.start_time, db_path=self.db)
        def snapshot():
            with connect_mini_db(self.db) as conn:
                tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mini_%' ORDER BY name")]
                return {t:conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
        before=snapshot()
        original=rewards.change_balance_in_transaction
        count=0
        def fail_after_second_payment(*args, **kwargs):
            nonlocal count
            result=original(*args, **kwargs)
            count+=1
            if count==2:
                raise RuntimeError("Wallet failure")
            return result
        with patch.object(rewards, "change_balance_in_transaction", side_effect=fail_after_second_payment):
            with self.assertRaises(RuntimeError):
                force_finish_battle(self.boss["id"], now=self.start_time, db_path=self.db)
        self.assertEqual(count,2)
        self.assertEqual(snapshot(),before)


if __name__ == "__main__":
    unittest.main()
