"""Content finalization: real supplied art/records versus temporary author fixtures."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import patch

from app.mini import catalog
from app.mini.mythic import content
from app.mini.mythic.raid import catalog as raid_catalog
from app.mini.mythic.raid import mechanics
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_effect_matrix as effects
from tests.v1_5 import test_completion_audit as audit
from tests.v1_5 import test_raid_mechanics as raid_tests
from tools import import_v15_s5 as importer

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/v1.5/S5_IMPORT_MANIFEST.json"
S5 = json.loads(MANIFEST.read_text(encoding="utf-8"))["heroes"]


def image(color=2, pixels=None, **options):
    return effects.AssetMatrix.background_png(
        color, pixels or [(0, 0, 0)] * 4, **options
    )


class PngContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "fixture.png"

    def test_black_every_corner_channel_0_to_4_and_rejected_5(self):
        for corner in range(4):
            for channel in range(3):
                for value in range(6):
                    with self.subTest(corner=corner, channel=channel, value=value):
                        pixels = [[0, 0, 0] for _ in range(4)]
                        pixels[corner][channel] = value
                        self.path.write_bytes(image(pixels=pixels))
                        if value <= 4:
                            info = content.png(
                                self.path, background=0, include_corners=True
                            )
                            self.assertEqual(
                                info["corners"][corner]["rgba"], [*pixels[corner], 255]
                            )
                        else:
                            with self.assertRaises(ValueError):
                                content.png(self.path, background=0)

    def test_black_opacity_255_required_for_every_corner_and_format(self):
        for color, pixel in ((3, (0,)), (4, (4, 255)), (6, (1, 2, 4, 255))):
            for corner in range(4):
                with self.subTest(color=color, corner=corner):
                    pixels = [pixel] * 4
                    options = {}
                    if color == 3:
                        options = dict(
                            palette=bytes((1, 2, 4)) * 2, transparency=b"\xff\xfe"
                        )
                        pixels[corner] = (1,)
                    else:
                        pixels[corner] = (*pixel[:-1], 254)
                    self.path.write_bytes(image(color, pixels, **options))
                    with self.assertRaises(ValueError):
                        content.png(self.path, background=0)

    def test_trns_transparent_rgb_and_grayscale_cannot_pass_either_background(self):
        for background in (0, 255):
            for color in (0, 2):
                with self.subTest(background=background, color=color):
                    pixel = (background,) * (1 if color == 0 else 3)
                    trns = struct.pack(">H" if color == 0 else ">HHH", *pixel)
                    self.path.write_bytes(image(color, [pixel] * 4, transparency=trns))
                    with self.assertRaises(ValueError):
                        content.png(self.path, background=background)

    def test_crc_scanline_filter_signature_missing_and_square_remain_strict(self):
        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        def packed(raw, width=2, height=2, compressed=False):
            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", raw if compressed else zlib.compress(raw))
                + chunk(b"IEND", b"")
            )

        good = image()
        for value in (
            b"JPEG",
            good[:-1],
            good[:-1] + bytes([good[-1] ^ 1]),
            packed(b"\x05" + bytes(6) + b"\0" + bytes(6)),
            packed(b"\0" + bytes(6)),
            packed(b"bad zlib", compressed=True),
            packed(bytes(14), width=2, height=3),
        ):
            with self.subTest(size=len(value)):
                self.path.write_bytes(value)
                with self.assertRaises(ValueError):
                    content.png(self.path, background=0)
        self.path.unlink()
        with self.assertRaises(ValueError):
            content.png(self.path, background=0)

    def test_raid_catalog_requires_black_corners_and_safe_basename(self):
        data = {"schema_version": 1, "bosses": []}
        for index, kind in enumerate(mechanics.SPECS):
            boss = raid_tests.fixture_boss(kind, index)
            data["bosses"].append(boss)
            (self.path.parent / boss["image"]).write_bytes(
                image(pixels=[(4, 3, 2)] * 4)
            )
        self.assertEqual(len(raid_catalog.validate(data, images=self.path.parent)), 4)
        (self.path.parent / data["bosses"][0]["image"]).write_bytes(
            image(pixels=[(5, 0, 0)] * 4)
        )
        with self.assertRaises(ValueError):
            raid_catalog.validate(data, images=self.path.parent)
        for name in ("../fixture.png", "..\\fixture.png", "/tmp/fixture.png"):
            changed = copy.deepcopy(data)
            changed["bosses"][0]["image"] = name
            with self.assertRaises(ValueError):
                raid_catalog.validate(
                    changed, images=self.path.parent, require_images=False
                )


class ImporterContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cat = self.root / "heroes"
        self.images = self.root / "images"
        self.cat.mkdir()
        self.images.mkdir()
        self.source = self.root / "approved.json"
        self.source.write_bytes(MANIFEST.read_bytes())
        codes = {h["code"] for h in S5}
        for rarity in catalog.RARITIES:
            data = json.loads(
                (catalog.HERO_CATALOG_DIR / (rarity + ".json")).read_text(
                    encoding="utf-8"
                )
            )
            data["heroes"] = [h for h in data["heroes"] if h["code"] not in codes]
            (self.cat / (rarity + ".json")).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        for h in S5:
            (self.images / h["image"]).write_bytes(image(pixels=[(255, 255, 255)] * 4))
        for module, key, value in (
            (catalog, "HERO_CATALOG_DIR", self.cat),
            (importer, "HERO_CATALOG_DIR", self.cat),
            (importer, "HERO_IMAGES_DIR", self.images),
            (importer, "APPROVED_SOURCE", self.source),
        ):
            self.enterContext(patch.object(module, key, value))

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def prepare(self, images=None):
        return importer.prepare(self.source, images or self.images)

    def install(self):
        with importer.import_lock():
            plan = self.prepare()
            importer.apply(plan)
        return plan

    def change_record(self, record):
        path = self.cat / (record["rarity"] + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["heroes"].append(record)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_existing_same_path_png_reused_and_second_apply_is_byte_noop(self):
        original = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.images.iterdir()}
        before = len(catalog.load_hero_catalog()["heroes"])
        plan = self.install()
        self.assertEqual(len(plan["changes"]), 4)
        self.assertEqual(len(plan["reused_images"]), 17)
        self.assertEqual(len(catalog.load_hero_catalog()["heroes"]), before + 17)
        counts = {
            r: sum(
                h["rarity"] == r
                for h in catalog.load_hero_catalog()["heroes"]
                if h["code"] in {v["code"] for v in S5}
            )
            for r in catalog.GACHA_RARITIES
        }
        self.assertEqual(counts, dict(common=5, uncommon=6, rare=4, legendary=2))
        for p, mtime in mtimes.items():
            self.assertEqual(p.stat().st_mtime_ns, mtime)
            self.assertEqual(p.read_bytes(), original[str(p.relative_to(self.root))])
        snapshot = self.snapshot()
        second = self.install()
        self.assertTrue(second["already_imported"])
        self.assertEqual(second["changes"], {})
        self.assertEqual(self.snapshot(), snapshot)

    def test_identical_external_source_reused_but_different_valid_png_refused(self):
        origin = self.root / "input"
        shutil.copytree(self.images, origin)
        self.assertEqual(len(self.prepare(origin)["reused_images"]), 17)
        (origin / S5[0]["image"]).write_bytes(image(pixels=[(252, 252, 252)] * 4))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Conflicting existing S5 PNG"):
            self.prepare(origin)
        self.assertEqual(self.snapshot(), before)

    def test_missing_destination_copies_only_validated_selected_source(self):
        origin = self.root / "input"
        shutil.copytree(self.images, origin)
        target = self.images / S5[0]["image"]
        expected = target.read_bytes()
        target.unlink()
        plan = self.prepare(origin)
        self.assertEqual(len(plan["changes"]), 5)
        with importer.import_lock():
            importer.apply(plan)
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(self.prepare(origin)["changes"], {})

    def test_partial_catalog_rejected_before_any_write(self):
        self.change_record(copy.deepcopy(S5[0]))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Partial S5"):
            self.prepare()
        self.assertEqual(self.snapshot(), before)

    def test_full_conflicting_catalog_rejected_before_any_write(self):
        self.install()
        path = self.cat / (S5[0]["rarity"] + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        next(h for h in data["heroes"] if h["code"] == S5[0]["code"])[
            "name"
        ] = "Conflicting test-only name"
        path.write_text(json.dumps(data), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Conflicting S5"):
            self.prepare()
        self.assertEqual(self.snapshot(), before)

    def test_replace_failure_rolls_back_all_catalogs_and_keeps_png_bytes(self):
        before = self.snapshot()
        plan = self.prepare()
        replace = importer.os.replace
        calls = []

        def fail_second(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("injected second replacement failure")
            return replace(source, target)

        with importer.import_lock(), patch.object(
            importer.os, "replace", side_effect=fail_second
        ):
            with self.assertRaises(OSError):
                importer.apply(plan)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.glob(".s5-import-*")), [])

    def test_staging_failure_does_not_replace_any_catalog(self):
        before = self.snapshot()
        plan = self.prepare()
        with patch.object(
            importer, "_staged_write", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                importer.apply(plan)
        self.assertEqual(self.snapshot(), before)

    def test_changed_input_and_stale_concurrent_plan_cannot_write(self):
        first = self.prepare()
        second = self.prepare()
        importer.apply(first)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            importer.apply(second)
        self.assertEqual(self.snapshot(), before)
        plan = self.prepare()
        (self.images / S5[0]["image"]).write_bytes(b"changed after preflight")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            importer.apply(plan)
        self.assertEqual(self.snapshot(), before)

    def test_lock_excludes_second_invocation_and_releases_on_exception(self):
        with importer.import_lock():
            with self.assertRaisesRegex(ValueError, "already locked"):
                with importer.import_lock():
                    self.fail("lock allowed duplicate")
        with self.assertRaises(RuntimeError), importer.import_lock():
            raise RuntimeError("test failure")
        self.assertFalse((self.root / ".s5-import.lock").exists())

    def test_only_approved_manifest_and_valid_full_source_allowed(self):
        unknown = self.root / "unknown.json"
        unknown.write_bytes(self.source.read_bytes())
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Only docs"):
            importer.prepare(unknown, self.images)
        self.assertEqual(self.snapshot(), before)
        for mutate in ("wrong rarity", "bad path", "duplicate code"):
            values = copy.deepcopy(S5)
            if mutate == "wrong rarity":
                values[0]["rarity"] = "common"
            elif mutate == "bad path":
                values[0]["image"] = "../outside.png"
            else:
                values[0]["code"] = values[1]["code"]
            self.source.write_text(json.dumps({"heroes": values}), encoding="utf-8")
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(self.snapshot(), before)


class RealContent(unittest.TestCase):
    def test_all_45_real_png_hashes_corners_and_size_without_author_records(self):
        inventory = json.loads(
            (ROOT / "docs/v1.5/CONTENT_ASSET_SHA256.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(inventory["assets"]), 45)
        counts = dict(s5=0, mythic=0, raid=0)
        for asset in inventory["assets"]:
            with self.subTest(path=asset["path"]):
                path = ROOT / asset["path"]
                before = path.read_bytes()
                self.assertEqual(hashlib.sha256(before).hexdigest(), asset["sha256"])
                info = content.png(
                    path,
                    background=255 if asset["kind"] == "s5" else 0,
                    include_corners=True,
                )
                self.assertEqual(info["width"], info["height"])
                self.assertEqual(len(info["corners"]), 4)
                self.assertEqual(path.read_bytes(), before)
                counts[asset["kind"]] += 1
        self.assertEqual(counts, dict(s5=17, mythic=24, raid=4))

    def test_real_png_and_s5_status_do_not_claim_missing_author_content(self):
        with patch("app.stories.catalog.discover", return_value=({}, {})):
            result = content.report()
        self.assertEqual(result["components"]["PNG_PASS"], "PASS")
        self.assertEqual(result["components"]["S5_IMPORTED"], "PASS")
        self.assertEqual(
            result["components"]["STRICT_CONTENT_PASS"], "BLOCKED_BY_INPUT"
        )
        self.assertEqual(len(result["assets"]), 45)


class S5Integration(MiniCase):
    def setUp(self):
        super().setUp()
        self.sql(
            "UPDATE mini_players SET coins=100000,shards=100000 WHERE id=?", (self.pid,)
        )

    def test_all_17_real_s5_pulls_duplicates_shards_rank_passives_and_replay(self):
        from app.mini.gacha import perform_gacha_pull
        from app.mini.heroes import get_player_hero
        from app.mini.hero_upgrades import upgrade_hero
        from app.mini.combat.modes import freeze_hero

        price = catalog.load_hero_catalog()["settings"]["pull_price"]
        awarded = 0
        for entry in S5:
            with self.subTest(code=entry["code"]), patch(
                "app.mini.gacha._choose_hero_code", return_value=entry["code"]
            ), patch("app.mini.gacha.required_rarity", return_value=None):
                for index in range(3):
                    key = "s5:" + entry["code"] + ":" + str(index)
                    result = perform_gacha_pull(
                        self.pid, db_path=self.db, operation_key=key
                    )
                    replay = perform_gacha_pull(
                        self.pid, db_path=self.db, operation_key=key
                    )
                    self.assertTrue(replay.pop("repeated"))
                    self.assertEqual(result, replay)
                    self.assertEqual(result["copies"], index + 1)
                    awarded += result["shards_awarded"]
                hero = get_player_hero(self.pid, result["id"], self.db)
                for field in (
                    "name",
                    "faction",
                    "class_tag",
                    "damage_type",
                    "attack_range",
                    "special_trait",
                    "passive_key",
                ):
                    self.assertEqual(hero[field], entry[field])
                frozen = freeze_hero(hero)
                upgraded = upgrade_hero(self.pid, hero["id"], self.db, expected_stars=0)
                self.assertEqual(upgraded["stars"], 1)
                with self.assertRaises(ValueError):
                    upgrade_hero(self.pid, hero["id"], self.db, expected_stars=0)
                self.assertEqual(frozen["stars"], 0)
        self.assertEqual(len(self.sql("SELECT * FROM mini_gacha_pulls")), 51)
        self.assertEqual(
            self.sql("SELECT coins FROM mini_players WHERE id=?", (self.pid,))[0][
                "coins"
            ],
            100000 - 51 * price,
        )
        self.assertGreater(awarded, 0)

    def test_gacha_pool_excludes_shadow_and_mythic_and_contains_all_17_s5(self):
        from app.mini.gacha import _choose_hero_code

        settings = catalog.load_hero_catalog()["settings"]
        pool = set()
        cursor = 0
        for rarity, weight in settings["rarity_weights"].items():
            if rarity not in catalog.GACHA_RARITIES:
                continue

            def choose(values):
                self.assertTrue(
                    all(h["rarity"] in catalog.GACHA_RARITIES for h in values)
                )
                pool.update(h["code"] for h in values)
                return values[0]

            with patch("app.mini.gacha.secrets.randbelow", return_value=cursor), patch(
                "app.mini.gacha.secrets.choice", side_effect=choose
            ):
                _choose_hero_code()
            cursor += int(weight) * 100
        self.assertTrue({h["code"] for h in S5} <= pool)
        self.assertTrue(
            pool.isdisjoint(
                {
                    h["code"]
                    for h in catalog.load_hero_catalog()["heroes"]
                    if h["rarity"] in ("shadow", "mythic")
                }
            )
        )

    def test_concurrent_real_s5_pull_same_operation_commits_once(self):
        from app.mini.gacha import perform_gacha_pull

        with patch("app.mini.gacha._choose_hero_code", return_value=S5[0]["code"]):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda _: perform_gacha_pull(
                            self.pid, db_path=self.db, operation_key="s5-concurrent"
                        ),
                        range(2),
                    )
                )
        self.assertEqual(sum(bool(r.pop("repeated", False)) for r in results), 1)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.sql("SELECT * FROM mini_gacha_pulls")), 1)
        self.assertEqual(
            self.sql("SELECT copies FROM mini_player_heroes")[0]["copies"], 1
        )


class S5Telegram(MiniCase, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = audit.TelegramAudit.asyncSetUp
    callback = audit.TelegramAudit.callback
    message = audit.TelegramAudit.message

    async def test_all_17_handler_to_sqlite_to_real_png_and_duplicate_button(self):
        from app.mini.ui import heroes, hero_cards
        from app.mini import gacha
        from app.mini.heroes import get_player_hero

        self.sql("UPDATE mini_players SET coins=100000 WHERE id=?", (self.pid,))
        self.enterContext(
            patch.object(
                heroes,
                "perform_gacha_pull",
                side_effect=lambda pid, **kw: gacha.perform_gacha_pull(
                    pid, db_path=self.db, **kw
                ),
            )
        )
        self.enterContext(
            patch.object(
                hero_cards,
                "get_gacha_state",
                side_effect=lambda pid: gacha.get_gacha_state(pid, self.db),
            )
        )
        self.enterContext(
            patch.object(
                hero_cards,
                "get_player_hero",
                side_effect=lambda pid, hid: get_player_hero(pid, hid, self.db),
            )
        )
        transcript = []
        for index, entry in enumerate(S5):
            with self.subTest(code=entry["code"]), patch.object(
                heroes,
                "_load_extended_context",
                return_value=(self.world, self.player, f"coins.s5{index}"),
            ), patch(
                "app.mini.gacha._choose_hero_code", return_value=entry["code"]
            ), patch(
                "app.mini.gacha.required_rarity", return_value=None
            ):
                callback = self.callback("s5-fixture")
                await heroes.gacha_pull_callback(callback)
                await heroes.gacha_pull_callback(callback)
                self.assertEqual(
                    Path(self.photos.call_args.kwargs["photo"].path).name,
                    entry["image"],
                )
                self.assertLessEqual(
                    len(self.photos.call_args.kwargs["caption"].encode("utf-16-le"))
                    // 2,
                    1024,
                )
                transcript.append(
                    dict(
                        code=entry["code"],
                        png=entry["image"],
                        caption=self.photos.call_args.kwargs["caption"],
                    )
                )
        evidence = Path(os.getenv("DND_CHECK_SANDBOX", self.tmp.name))
        (evidence / "s5-telegram-transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_gacha_pulls")), 17)
        self.assertEqual(
            {r["copies"] for r in self.sql("SELECT copies FROM mini_player_heroes")},
            {1},
        )


class AuthorContracts(unittest.TestCase):
    def template(self, filename):
        text = (ROOT / "docs/v1.5" / filename).read_text(encoding="utf-8")
        return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])

    def test_mythic_template_known_fields_and_full_temporary_eight_contract(self):
        values = self.template("MYTHIC_AUTHOR_INPUT_REQUIRED.md")["heroes"]
        self.assertEqual(len(values), 8)
        self.assertTrue(all(v["code"] is None and v["name"] is None for v in values))
        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            for h in values:
                for key in [key for key, value in h.items() if value is None]:
                    h[key] = True if key == "active" else "Temporary author fixture"
                h.update(
                    code="fixture_mythic_" + h["mythic_element"],
                    name="Fixture " + h["mythic_element"],
                    faction="neutral",
                    damage_type="magic",
                    attack_range="ranged",
                    class_tag="mage",
                    special_trait="none",
                )
                for stage in content.STAGES:
                    (images / (h["mythic_element"] + "_" + stage + ".png")).write_bytes(
                        image()
                    )
            data = catalog.load_hero_catalog()
            data["heroes"] = [
                h for h in data["heroes"] if h["rarity"] != "mythic"
            ] + values
            with patch("app.mini.catalog._read_hero_catalog", return_value=data):
                validated = catalog.load_hero_catalog()["heroes"]
            self.assertEqual(content.validate_mythics(validated, images=images), [])
            values[0]["attack"] += 1
            with self.assertRaises(ValueError):
                content.validate_mythics(values, images=images)

    def test_raid_templates_require_only_codes_names_and_match_all_four_specs(self):
        data = self.template("RAID_BOSSES_AUTHOR_INPUT_REQUIRED.md")
        self.assertEqual(len(data["bosses"]), 4)
        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            for index, boss in enumerate(data["bosses"], 1):
                self.assertIsNone(boss["code"])
                self.assertIsNone(boss["name"])
                self.assertEqual(boss["image"], f"raid_boss_{index}.png")
                boss.update(
                    code=f"fixture_author_{index}", name="Temporary author fixture"
                )
                (images / boss["image"]).write_bytes(image())
            self.assertEqual(len(raid_catalog.validate(data, images=images)), 4)
            self.assertEqual(
                {v["mechanic"]["type"] for v in data["bosses"]}, set(mechanics.SPECS)
            )

    def test_story_template_dag_utf16_limits_and_external_readonly_cli(self):
        from app.stories import catalog as stories
        from tools import validate_story_content as cli

        template = json.loads(
            (ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stories.validate(template), template)
        for invalid in ("cycle", "missing", "utf16"):
            changed = copy.deepcopy(template)
            choice = changed["nodes"]["start"]["choices"][0]
            if invalid == "cycle":
                choice["next"] = "start"
            elif invalid == "missing":
                choice["next"] = "missing"
            else:
                choice["text"] = "\U0001f525" * 61
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stories.validate(changed)
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory)
            with patch("sys.argv", ["stories", "--directory", str(path)]):
                self.assertEqual(cli.main(), 2)
                (path / "temporary-template.json").write_text(
                    json.dumps(template), encoding="utf-8"
                )
                self.assertEqual(cli.main(), 0)
                (path / "duplicate.json").write_text(
                    json.dumps(template), encoding="utf-8"
                )
                self.assertEqual(cli.main(), 1)
            with patch("sys.argv", ["stories", "--directory", str(ROOT / "docs/v1.5")]):
                self.assertEqual(cli.main(), 1)
        with self.assertRaises(ValueError):
            stories.directory(ROOT / "docs/v1.5")


class S5RaidIntegration(raid_tests.RaidFixture, MiniCase):
    def setUp(self):
        super().setUp()
        self.setup_raid_fixtures()

    def test_all_17_real_s5_raid_attempts_ledger_replay_and_equipment(self):
        from app.mini.mythic.raid import service as raids
        from app.mini.equipment import service as equipment

        raid = self.start_raid(2)
        for index, entry in enumerate(S5):
            hero = self.hero(entry["code"])
            key = "real-s5-raid-" + entry["code"]
            now = 100 + index * 86400
            result = raids.attack(
                self.pid, raid["id"], hero["id"], key, self.db, now=now
            )
            self.assertEqual(
                raids.attack(self.pid, raid["id"], hero["id"], key, self.db, now=now),
                result,
            )
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 17)
        current = raids.state(self.world["id"], self.db)
        self.assertEqual(current["state"]["mechanic"]["confirmed_attacks"], 17)
        self.assertGreater(
            self.sql("SELECT damage FROM mini_mythic_raid_damage")[0]["damage"], 0
        )
        self.sql(
            "INSERT INTO mini_equipment_owned(player_id,code,quantity) VALUES(?,?,1)",
            (self.pid, "eq_helmet_001"),
        )
        equipped = equipment.equip(self.pid, "eq_helmet_001", self.db)
        self.assertEqual(equipped["attack_bonus"], 1)
        self.assertEqual(
            equipment.unequip(self.pid, "helmet", self.db)["attack_bonus"], 0
        )


class StoryTemplateIntegration(MiniCase, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = audit.TelegramAudit.asyncSetUp
    callback = audit.TelegramAudit.callback
    message = audit.TelegramAudit.message

    async def test_temporary_template_list_open_start_vote_restart_kick_cancel(self):
        from app.stories import service, handlers, catalog as stories
        from app.mini.players import create_mini_player

        directory = Path(self.tmp.name) / "external-stories"
        directory.mkdir()
        path = directory / "template-only.json"
        path.write_bytes((ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_bytes())
        self.enterContext(patch("app.context.get_topic_admin", return_value="@tester"))
        self.enterContext(patch.dict("os.environ", STORIES_DIR=str(directory)))
        self.assertEqual(set(stories.discover()[0]), {"template_not_production"})
        chat = self.world["chat_id"]
        thread = self.world["thread_id"] or 0
        before = self.sql(
            "SELECT coins,shards,mythic_shards FROM mini_players WHERE id=?",
            (self.pid,),
        )
        # Real command handler and offline Telegram publication use the template
        # only in this disposable external directory, never as author acceptance.
        message = self.message("/story list", mid=101, chat=chat, thread=thread)
        await handlers.command(message)
        message = self.message(
            "/story template_not_production", mid=102, chat=chat, thread=thread
        )
        await handlers.command(message)
        current = service.status(chat, thread, self.db)
        sid = current["id"]
        other = create_mini_player(self.world["id"], 900124, "other", "Other", self.db)[
            "id"
        ]
        service.join(
            sid,
            chat,
            thread,
            900123,
            "mini",
            {"id": self.pid},
            "template-join",
            self.db,
        )
        service.join(
            sid,
            chat,
            thread,
            900124,
            "mini",
            {"id": other},
            "template-join-other",
            self.db,
        )
        await handlers.command(
            self.message("/story start", mid=103, chat=chat, thread=thread)
        )
        frozen = service.status(chat, thread, self.db)
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(
            service.status(chat, thread, self.db)["snapshot_json"],
            frozen["snapshot_json"],
        )
        vote = service.vote(
            sid, chat, thread, 900123, 1, "continue", "template-vote", self.db
        )
        self.assertEqual(
            service.vote(
                sid, chat, thread, 900123, 1, "continue", "template-vote", self.db
            ),
            vote,
        )
        await handlers.command(
            self.message("/story kick 900124", mid=104, chat=chat, thread=thread)
        )
        self.assertEqual(
            service.status(chat, thread, self.db, session_id=sid)["status"], "finished"
        )
        path.write_bytes((ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_bytes())
        await handlers.command(
            self.message(
                "/story template_not_production", mid=105, chat=chat, thread=thread
            )
        )
        await handlers.command(
            self.message("/story cancel", mid=106, chat=chat, thread=thread)
        )
        self.assertEqual(
            self.sql("SELECT status FROM story_sessions ORDER BY id")[-1]["status"],
            "cancelled",
        )
        self.assertEqual(
            self.sql(
                "SELECT coins,shards,mythic_shards FROM mini_players WHERE id=?",
                (self.pid,),
            ),
            before,
        )
