"""V1.5 effect matrix: all 16 families, arcana, persistence and content boundaries."""

import copy
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from app.mini.combat import team
from app.mini.combat.modes import freeze_hero, ordinary_damage
from app.mini.combat.arise import award, sources
from app.mini.mythic.raid.effects import resolve
from app.mini.mythic.raid.contracts import validate
from app.mini.mythic.content import png, validate_mythics
from app.mini.mythic.wallet import transaction
from tests.v1_3.support import MiniCase


def hero(kind, stars=1, attack=100):
    return freeze_hero(
        {
            "attack": attack,
            "stars": stars,
            "rarity": "mythic",
            "faction": "neutral",
            "class_tag": "mage",
            "attack_range": "ranged",
            "damage_type": "magic",
            "special_trait": "none",
            "passives": {m: {"key": "mythic_" + kind} for m in ("normal", "raid")},
        }
    )


def raid(state=None, hp=100000, **template):
    return {
        "state": state or {},
        "current_hp": hp,
        "max_hp": 100000,
        "original_max_hp": 100000,
        "snapshot": {
            "counter_elements": ["fire"],
            "regeneration_percent": 0,
            **template,
        },
    }


class NormalMatrix(unittest.TestCase):
    def test_fire_ten_actual_stacks_and_all_levels(self):
        for stars in range(1, 7):
            h = hero("fire", stars)
            s = team.begin([h], 100000, 100)
            result = team.after_hit(s, h, 1, 99900, 100000, 1, 100)
            s = result["state"]
            hp = result["current_hp"]
            for i in range(10):
                result = team.after_hit(
                    s,
                    hero("earth"),
                    2,
                    hp - 100,
                    100000,
                    1,
                    100,
                    roller=lambda _: False,
                )
                s = result["state"]
                hp = result["current_hp"]
            self.assertEqual(
                result["events"][0]["actual_hp_damage"],
                1000 * (20 + 4 * (stars - 1)) // 100,
            )
            self.assertNotIn("burn", s)

    def test_ice_two_separate_shields_and_current_hp(self):
        for stars in range(1, 7):
            s = team.begin([hero("ice", stars)], 10000, 100)
            one = team.reward_attack(s, 1000, 1, roller=lambda _: False)
            self.assertEqual(one["hp_damage"], 1000 * (10 + 2 * (stars - 1)) // 100)
            two = team.reward_attack(one["state"], 800, 1, roller=lambda _: False)
            self.assertTrue(two["blocked"])
            self.assertFalse(team.reward_attack(two["state"], 500, 1)["blocked"])

    def test_lightning_single_round_nonrecursive_and_reward(self):
        h = hero("lightning", 6)
        s = team.begin([h], 1000, 100)
        first = team.after_hit(
            s,
            h,
            1,
            900,
            1000,
            1,
            100,
            participants=[{"player_id": 1}, {"player_id": 2}],
            roller=lambda _: True,
        )
        self.assertEqual(first["extra_players"], [2])
        self.assertEqual(first["boss_skip"], 1)
        self.assertEqual(
            team.after_hit(
                first["state"],
                h,
                1,
                800,
                1000,
                1,
                100,
                is_extra=True,
                participants=[{"player_id": 2}],
            )["extra_players"],
            [],
        )
        self.assertEqual(team.coins(first["state"], 100, shields_intact=True), 107)
        self.assertEqual(team.coins(first["state"], 100, shields_intact=False), 100)

    def test_earth_uncapped_round_bonus_and_suppression(self):
        h = hero("earth", 6)
        s = team.begin([h], 1000, 100)
        self.assertEqual(
            ordinary_damage(100, bonus_bps=team.modifiers(s, h, 1, 100)["bonus_bps"]),
            1100,
        )
        self.assertEqual(
            team.after_hit(s, h, 1, 900, 1000, 7, 100, roller=lambda _: True)["state"][
                "suppressed_round"
            ],
            7,
        )

    def test_dark_missing_hp_and_overlay_expire(self):
        h = hero("dark", 6)
        s = team.begin([h], 1000, 100)
        result = team.after_hit(s, h, 1, 300, 1000, 1, 100)
        s = result["state"]
        self.assertIn("dark", s["elements"])
        self.assertEqual(team.modifiers(s, hero("ice"), 2, 1)["bonus_bps"], [7000, 0])
        self.assertEqual(team.modifiers(s, hero("ice"), 2, 2)["bonus_bps"], [])

    def test_star_frozen_star_sum_and_scaling(self):
        h = hero("star", 6)
        s = team.begin([h, hero("ice", 4)], 1000, 100)
        r = team.after_hit(s, h, 1, 800, 1000, 1, 100)
        self.assertEqual(r["state"]["round_buffs"]["star"], 2000)
        self.assertIn("star", r["state"]["elements"])

    def test_sun_uncapped_marks_neutral_and_blind(self):
        h = hero("sun", 6)
        s = team.begin([h], 100000, 100)
        for _ in range(70):
            s = team.after_hit(s, h, 1, 90000, 100000, 1, 1)["state"]
        self.assertEqual(team.modifiers(s, h, 1, 1)["multiplier"], 2**70)
        self.assertTrue(s["neutralized"])
        self.assertEqual(s["blind_chance"], 55)
        self.assertTrue(
            team.reward_attack(s, 90000, 1, roller=lambda _: True)["blocked"]
        )

    def test_all_twelve_lunar_arcana_and_weights(self):
        self.assertEqual(sum(team.NORMAL_ARCANA_WEIGHTS), 100)
        for arc in range(12):
            s = team.begin([hero("moon", 6)], 1000, 100, chooser=lambda _: arc)
            self.assertEqual(s["lunar"], {"arcana": arc, "level": 5})
            changes = team.start_changes(s)
            m = team.modifiers(s, hero("moon", 6), 1, 1)
            if arc == 0:
                self.assertEqual(m["reduction_percent"], 25)
            if arc == 1:
                self.assertIn(2500, m["bonus_bps"])
            if arc == 2:
                self.assertEqual(team.coins(s, 100), 400)
            if arc == 3:
                self.assertEqual(changes, {"max_hp": 1300, "current_hp": 1300})
            if arc == 4:
                self.assertEqual(changes["extra_shields"], 2)
            if arc == 5:
                self.assertEqual(m["additive"], 2000)
            if arc == 6:
                self.assertIn(-9000, m["bonus_bps"])
            if arc == 7:
                self.assertEqual(changes["instant"], "victory")
            if arc == 8:
                self.assertEqual(changes["instant"], "failure")
            if arc == 9:
                self.assertEqual(team.coins(s, 100), 10)
            if arc == 10:
                self.assertEqual(s["lunar"]["arcana"], 10)
            if arc == 11:
                self.assertIn(("lunar_shadows", 35), sources(hero("moon", 6), team=s))


class RaidMatrix(unittest.TestCase):
    def test_fire_window_and_clamped_explosion_all_levels(self):
        for stars in range(1, 7):
            r = raid({"counter_active": True})
            h = hero("fire", stars)
            r.update(resolve(r, h, 1))
            for i in range(5):
                result = resolve(r, {"attack": 100, "stars": 0}, 2)
                r.update(result)
            self.assertEqual(result["events"][-1]["kind"], "fire_explosion")
            self.assertEqual(
                result["events"][-1]["actual_hp_damage"],
                500 * (20 + 4 * (stars - 1)) // 100,
            )

    def test_ice_original_max_not_current_all_levels(self):
        for stars in range(1, 7):
            result = resolve(
                raid({"counter_active": True}, hp=90000), hero("ice", stars), 1
            )
            self.assertEqual(
                result["events"][1]["actual_hp_damage"], 1000 + 100 * (stars - 1)
            )

    def test_lightning_three_other_players_and_attribution(self):
        r = raid({"counter_active": True})
        r.update(resolve(r, hero("lightning", 6), 1))
        before = r["state"]["lightning"]["charges"]
        r.update(resolve(r, {"attack": 100}, 1))
        self.assertEqual(r["state"]["lightning"]["charges"], before)
        for i in range(3):
            result = resolve(r, {"attack": 100}, 2)
            r.update(result)
            self.assertEqual(result["events"][1]["actor_id"], 2)
            self.assertEqual(
                result["events"][1]["actual_hp_damage"],
                result["events"][0]["hp_after"] // 100,
            )
        self.assertEqual(r["state"]["lightning"]["charges"], 0)

    def test_earth_equal_other_bonus_retained_and_overkill(self):
        s = {
            "counter_active": True,
            "earth": {"charges": 3, "owner": 1, "level": 0},
            "star": {"charges": 5, "bonus_bps": 1500},
        }
        r = resolve(raid(s), {"attack": 100}, 2)
        self.assertEqual([e["actual_hp_damage"] for e in r["events"]], [130, 17])
        r = resolve(raid(s, hp=120), {"attack": 100}, 2)
        self.assertEqual([e["actual_hp_damage"] for e in r["events"]], [120, 0])

    def test_dark_and_star_levels_history(self):
        for lv in range(6):
            s = {"counter_active": True, "dark": {"charges": 3, "level": lv}}
            r = resolve(raid(s, hp=50000), {"attack": 100}, 1)
            self.assertEqual(r["events"][0]["actual_hp_damage"], 150 + 5 * lv)
            r = resolve(
                raid({"counter_active": True, "last_stars": [1, 2, 3, 4, 5]}),
                hero("star", lv + 1),
                1,
            )
            self.assertEqual(
                r["state"]["star"]["bonus_bps"],
                sum([2, 3, 4, 5, lv + 1]) * (100 + 20 * lv),
            )

    def test_sun_all_three_attacks_suppress_armor_regeneration(self):
        r = raid(
            {"counter_active": True, "sun": {"charges": 3, "level": 0}},
            hp=90000,
            features=["armored"],
            regeneration_percent=10,
        )
        h = {"attack": 100, "attack_range": "melee", "damage_type": "slashing"}
        for _ in range(3):
            result = resolve(r, h, 2)
            r.update(result)
            self.assertEqual(result["events"][0]["actual_hp_damage"], 120)
            self.assertNotIn("regeneration", [e["type"] for e in result["audit"]])
        result = resolve(r, h, 2)
        self.assertEqual(result["events"][0]["actual_hp_damage"], 80)
        self.assertIn("regeneration", [e["type"] for e in result["audit"]])

    def test_moon_all_four_no_reroll_and_other_player_rule(self):
        for arc in range(4):
            r = raid({"counter_active": True})
            r.update(resolve(r, hero("moon", 6, 1), 1, chooser=lambda _: arc))
            again = resolve(
                r,
                hero("moon", 1, 1),
                1,
                chooser=lambda _: (_ for _ in ()).throw(AssertionError("reroll")),
            )
            self.assertEqual(again["state"]["lunar"]["arcana"], arc)
            if arc == 1:
                self.assertEqual(again["events"][0]["actual_hp_damage"], 451)
            if arc == 2:
                self.assertEqual(again["events"][0]["actual_hp_damage"], 1)
                self.assertEqual(
                    resolve(r, {"attack": 1}, 2)["events"][0]["actual_hp_damage"], 451
                )
            if arc == 3:
                self.assertEqual(again["max_hp"], 115000)

    def test_explicit_ordinary_raid_contract_no_normal_leak(self):
        definition = {
            "effects": [
                {"type": "damage_bonus_bps", "trigger": "attack", "value": 2000},
                {"type": "current_hp_percent", "trigger": "after_attack", "percent": 1},
            ]
        }
        h = {
            "attack": 100,
            "passive_key": "holy_smite",
            "passives": {
                "normal": {"key": "holy_smite"},
                "raid": {"key": "fixture_raid", "definition": definition},
            },
            "rules_version": 15,
        }
        r = resolve(raid({"counter_active": True}), h, 1)
        self.assertEqual([e["actual_hp_damage"] for e in r["events"]], [120, 998])
        with self.assertRaises(ValueError):
            validate({"effects": [{"type": "holy_smite"}]})

    def test_zero_does_not_activate_counter_or_trigger_effects(self):
        r = resolve(
            raid(features=["flying"]), {**hero("fire"), "attack_range": "melee"}, 1
        )
        self.assertEqual(r["events"][0]["actual_hp_damage"], 0)
        self.assertNotIn("counter_active", r["state"])
        self.assertNotIn("fire", r["state"])


class PersistenceMatrix(MiniCase):
    def test_additive_migration_replay_preserves_balances_and_integrity(self):
        from app.mini.migrations.v1_5 import apply
        from app.mini.boss.schema import init_boss_db

        init_boss_db(self.db)
        self.sql(
            "UPDATE mini_players SET coins=123,shards=456,mythic_shards=7 WHERE id=?",
            (self.pid,),
        )
        with transaction(self.db) as conn:
            for _ in range(3):
                apply(conn)
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT coins,shards,mythic_shards FROM mini_players WHERE id=?",
                        (self.pid,),
                    ).fetchone()
                ),
                (123, 456, 7),
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_arise_independent_rolls_until_first_success_and_replay(self):
        shadow = self.sql("SELECT * FROM mini_heroes WHERE rarity='shadow' LIMIT 1")[0]
        calls = []

        def roller(chance):
            calls.append(chance)
            return chance == 25

        with transaction(self.db) as conn:
            target = {"shadow_extractable": True, "shadow_hero_code": shadow["code"]}
            result = award(
                conn,
                "fixture",
                1,
                self.pid,
                target,
                True,
                [("a", 1), ("b", 40), ("c", 25), ("d", 100)],
                roller=roller,
            )
            self.assertEqual(calls, [1, 40, 25])
            self.assertTrue(result[-1]["success"])
            self.assertEqual(
                award(
                    conn,
                    "fixture",
                    1,
                    self.pid,
                    target,
                    True,
                    [("a", 100)],
                    roller=roller,
                ),
                result,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT copies FROM mini_player_heroes WHERE player_id=? AND hero_id=?",
                    (self.pid, shadow["id"]),
                ).fetchone()[0],
                1,
            )

    def test_arise_no_positive_contribution_no_target_no_roll(self):
        with transaction(self.db) as conn:
            with patch(
                "app.mini.combat.arise._roll_success",
                side_effect=AssertionError("must not roll"),
            ):
                self.assertEqual(
                    award(
                        conn,
                        "raid",
                        1,
                        self.pid,
                        {"shadow_extractable": True},
                        False,
                        [("a", 100)],
                    ),
                    [],
                )
                self.assertEqual(
                    award(
                        conn,
                        "raid",
                        1,
                        self.pid,
                        {"shadow_extractable": False},
                        True,
                        [("a", 100)],
                    ),
                    [],
                )

    def test_first_egg_required_before_purchase(self):
        from app.mini.mythic.pets import buy

        self.sql("UPDATE mini_players SET mythic_shards=100 WHERE id=?", (self.pid,))
        with self.assertRaisesRegex(ValueError, "Первое бесплатное"):
            buy(self.pid, "early", self.db, now=100)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players WHERE id=?", (self.pid,))[
                0
            ]["mythic_shards"],
            100,
        )

    def test_raid_delivery_recovery_never_resends_uncertain(self):
        from app.mini.mythic.raid.service import recover_publications

        self.assertEqual(recover_publications(self.db), 0)

    def test_tower_instant_victory_and_no_extra_attack_required(self):
        from app.mini.tower.service import start_attempt

        h = self.hero()
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',passive_key='mythic_moon',passives_json=?,mythic_element='moon' WHERE id=?",
            (
                json.dumps(
                    {"normal": {"key": "mythic_moon"}, "raid": {"key": "mythic_moon"}}
                ),
                h["id"],
            ),
        )
        self.sql("UPDATE mini_player_heroes SET stars=1 WHERE player_id=?", (self.pid,))
        with patch("app.mini.combat.team.secrets.choice", return_value=7):
            result = start_attempt(self.pid, h["id"], 1, self.db)
        self.assertEqual(result["status"], "won")
        self.assertIn("initial_reward", result)
        self.assertEqual(len(self.sql("SELECT * FROM mini_tower_rewards")), 1)


class AssetMatrix(unittest.TestCase):
    def test_png_dimensions_crc_and_background(self):
        def image(width, height, rgb):
            def chunk(kind, data):
                return (
                    struct.pack(">I", len(data))
                    + kind
                    + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
                )

            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgb) * width) * height))
                + chunk(b"IEND", b"")
            )

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fixture.png"
            p.write_bytes(image(2, 2, (0, 0, 0)))
            self.assertEqual(png(p, background=0)["width"], 2)
            with self.assertRaises(ValueError):
                png(p, background=255)
            p.write_bytes(image(2, 1, (255, 255, 255)))
            with self.assertRaises(ValueError):
                png(p)

    @staticmethod
    def background_png(color, pixels, *, palette=b"", transparency=b""):
        """Four independently specified corner pixels, with valid PNG framing."""

        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        rows = b"".join(
            b"\0" + bytes(pixels[y * 2]) + bytes(pixels[y * 2 + 1]) for y in range(2)
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, color, 0, 0, 0))
            + (chunk(b"PLTE", palette) if palette else b"")
            + (chunk(b"tRNS", transparency) if transparency else b"")
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )

    def test_white_background_accepts_each_channel_252_through_255(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "white.png"
            for value in range(252, 256):
                with self.subTest(value=value):
                    pixels = [
                        (value, 255, 255),
                        (255, value, 255),
                        (255, 255, value),
                        (value, value, value),
                    ]
                    path.write_bytes(self.background_png(2, pixels))
                    self.assertEqual(
                        png(path, background=255),
                        {"width": 2, "height": 2, "background": 255},
                    )

    def test_white_background_rejects_251_in_every_corner_and_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "white.png"
            for corner in range(4):
                for channel in range(3):
                    with self.subTest(corner=corner, channel=channel):
                        pixels = [[255, 255, 255] for _ in range(4)]
                        pixels[corner][channel] = 251
                        path.write_bytes(self.background_png(2, pixels))
                        with self.assertRaises(ValueError):
                            png(path, background=255)

    def test_white_tolerance_in_all_decoded_formats_and_exact_opacity(self):
        formats = {
            0: (252,),
            2: (252, 253, 254),
            3: (0,),
            4: (252, 255),
            6: (252, 253, 254, 255),
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "white.png"
            for color, pixel in formats.items():
                with self.subTest(color=color):
                    options = (
                        {"palette": bytes((252, 253, 254)), "transparency": b"\xff"}
                        if color == 3
                        else {}
                    )
                    path.write_bytes(self.background_png(color, [pixel] * 4, **options))
                    png(path, background=255)
                    invalid = (251,) if color == 0 else (251,) + pixel[1:]
                    if color == 3:
                        options["palette"] = bytes((252, 251, 254))
                        invalid = pixel
                    path.write_bytes(
                        self.background_png(color, [invalid] * 4, **options)
                    )
                    with self.assertRaises(ValueError):
                        png(path, background=255)
            for color in (3, 4, 6):
                for corner in range(4):
                    with self.subTest(color=color, corner=corner, alpha=254):
                        pixels = [formats[color]] * 4
                        options = {}
                        if color == 3:
                            pixels[corner] = (1,)
                            options = {
                                "palette": bytes((252, 253, 254)) * 2,
                                "transparency": b"\xff\xfe",
                            }
                        else:
                            pixels[corner] = pixels[corner][:-1] + (254,)
                        path.write_bytes(self.background_png(color, pixels, **options))
                        with self.assertRaises(ValueError):
                            png(path, background=255)

    def test_black_background_still_requires_exact_zero_in_all_formats(self):
        formats = {0: (0,), 2: (0, 0, 0), 3: (0,), 4: (0, 255), 6: (0, 0, 0, 255)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "black.png"
            for color, pixel in formats.items():
                with self.subTest(color=color):
                    options = {"palette": bytes((0, 0, 0))} if color == 3 else {}
                    path.write_bytes(self.background_png(color, [pixel] * 4, **options))
                    png(path, background=0)
                    for value in (1, 3):
                        if color == 3:
                            options["palette"] = bytes((0, value, 0))
                            invalid = pixel
                        else:
                            invalid = (value,) + pixel[1:]
                        path.write_bytes(
                            self.background_png(color, [invalid] * 4, **options)
                        )
                        with self.assertRaises(ValueError):
                            png(path, background=0)

    def test_background_validation_still_rejects_missing_palette_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "palette.png"
            path.write_bytes(
                self.background_png(3, [(1,)] * 4, palette=bytes((255, 255, 255)))
            )
            for background in (0, 255):
                with self.subTest(background=background), self.assertRaises(ValueError):
                    png(path, background=background)

    def test_empty_mythic_catalog_reports_blocker(self):
        self.assertEqual(len(validate_mythics([])), 1)
