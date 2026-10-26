"""Author package integration: use shipped cards, catalogs and art without replacements."""
import copy
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.mini import catalog
from app.mini.combat.modes import freeze_hero
from app.mini.heroes import get_player_hero, sync_hero_catalog
from app.mini.mythic import content, pets
from app.mini.mythic.raid import catalog as bosses, service as raids
from app.mini.mythic.upgrades import upgrade, COSTS
from app.mini.mythic.wallet import transaction
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_completion_audit as audit

ROOT = Path(__file__).resolve().parents[2]
START = "407577640d3141cbd4cb9cc0ad3fdf3b9b28aeaa"
MYTHICS = json.loads((catalog.HERO_CATALOG_DIR / "mythic.json").read_text(encoding="utf-8"))["heroes"]
BOSSES = json.loads(bosses.CATALOG.read_text(encoding="utf-8"))


class AuthorCatalog(unittest.TestCase):
    def test_payload_exact_bytes_and_old_71_semantically_unchanged(self):
        manifest = json.loads((ROOT / "docs/v1.5/AUTHOR_CONTENT_MANIFEST.json").read_text(encoding="utf-8"))
        for record in manifest["payload"].values():
            path = ROOT / record["path"].removeprefix("payload/")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
        for rarity in catalog.RARITIES:
            if rarity == "mythic":
                continue
            relative = f"app/mini/content/heroes/{rarity}.json"
            before = json.loads(subprocess.check_output(["git", "show", START + ":" + relative], cwd=ROOT))
            self.assertEqual(json.loads((ROOT / relative).read_text(encoding="utf-8")), before)
        data = catalog.load_hero_catalog()["heroes"]
        self.assertEqual(len(data), 79)
        self.assertEqual(len({h["code"] for h in data}), 79)
        self.assertEqual(content.validate_mythics(data), [])
        self.assertEqual(bosses.load(), BOSSES["bosses"])

    def test_mythic_negative_fields_rejected_before_legacy_fallback(self):
        data = catalog.load_hero_catalog()
        index = next(i for i,h in enumerate(data["heroes"]) if h["rarity"] == "mythic")
        changes = [("code", ""), ("name", ""), ("rarity", "invalid"), ("attack", -1),
                   ("attack", True), ("attack", 1.0), ("mythic_element", "invalid"),
                   ("faction", "invalid"), ("attack_range", "invalid"),
                   ("image", "../ice_adult.png"), ("passive_key", "mythic_sun"),
                   ("passives", {"normal": {"key": "mythic_ice"}}),
                   ("passives", {m: {"key": "mythic_sun"} for m in ("normal", "raid")}),
                   ("active", "true")]
        for field,value in changes:
            changed = copy.deepcopy(data);changed["heroes"][index][field] = value
            with self.subTest(field=field,value=value), patch.object(catalog,"_read_hero_catalog",return_value=changed), self.assertRaises(ValueError):
                catalog.load_hero_catalog()
        for field in ("code", "name", "mythic_element"):
            changed=copy.deepcopy(MYTHICS);changed[1][field]=changed[0][field]
            with self.subTest(duplicate=field), self.assertRaises(ValueError):
                content.validate_mythics(changed)
        with tempfile.TemporaryDirectory() as folder:
            missing=content.validate_mythics(MYTHICS,images=Path(folder))
            self.assertEqual(len(missing),24)

    def test_real_boss_contract_negative_fields_duplicates_and_profiles(self):
        for change in (lambda b:b.update(max_hp=True),lambda b:b.update(max_hp=99999),
                       lambda b:b.update(unknown=1),lambda b:b.update(shadow_extractable=True),
                       lambda b:b.update(image="../raid_boss_1.png"),lambda b:b.update(counter_elements=["sun"]),
                       lambda b:b["mechanic"].update(armored_attacks=11)):
            data=copy.deepcopy(BOSSES);change(data["bosses"][0])
            with self.subTest(boss=data["bosses"][0]),self.assertRaises(ValueError):
                bosses.validate(data,require_images=False)
        for field in ("code","image"):
            data=copy.deepcopy(BOSSES);data["bosses"][1][field]=data["bosses"][0][field]
            with self.assertRaises(ValueError):bosses.validate(data,require_images=False)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"duplicate.json";p.write_text('{"schema_version":1,"bosses":[],"bosses":[]}',encoding="utf-8")
            with self.assertRaises(ValueError):bosses.load(p)

    def test_only_missing_stories_block_strict_acceptance(self):
        with patch("app.stories.catalog.discover",return_value=({},{})):
            result=content.report()
            self.assertEqual(result["hero_count"],79)
            self.assertEqual(result["raid_bosses"],4)
            self.assertEqual(result["blockers"],["No real stories in external STORIES_DIR"])
            for name in ("PNG_PASS","S5_IMPORTED","MYTHIC_RECORDS_READY","RAID_RECORDS_READY"):
                self.assertEqual(result["components"][name],"PASS")
            with patch("sys.argv",["content","--strict-content"]),redirect_stdout(io.StringIO()):
                self.assertEqual(content.main(),2)


class AuthorGame(MiniCase):
    def real(self,element):
        return self.hero(next(h["code"] for h in MYTHICS if h["mythic_element"]==element),stars=1)

    def test_all_eight_evolve_pending_resume_once_and_upgrade_six(self):
        for entry in MYTHICS:
            with self.subTest(element=entry["mythic_element"]):
                self.sql("DELETE FROM mini_mythic_pets")
                with transaction(self.db) as conn:
                    pet=conn.execute("INSERT INTO mini_mythic_pets(player_id,element,settled_at) VALUES(?,?,100)",(self.pid,entry["mythic_element"])).lastrowid
                pets.activate(self.pid,pet,"active:"+entry["code"],self.db,now=100)
                self.sql("UPDATE mini_mythic_pets SET mood=100,protect_until=99999999 WHERE id=?",(pet,))
                young=pets.state(self.pid,self.db,now=259300)["pets"][0]
                self.assertEqual(young["stage"],"wonder")
                self.assertEqual(content.pet_image(entry["mythic_element"],"wonder").name,entry["mythic_element"]+"_young.png")
                with patch("app.mini.mythic.content.ready_hero",return_value=False):
                    pending=pets.state(self.pid,self.db,now=604900)["pets"][0]
                self.assertEqual(pending["evolution_pending"],1)
                adult=pets.state(self.pid,self.db,now=604900)["pets"][0]
                self.assertEqual((adult["stage"],adult["evolution_pending"]),("adult",0))
                pets.state(self.pid,self.db,now=604901)
                owned=self.sql("SELECT * FROM mini_player_heroes WHERE player_id=? AND hero_id=?",(self.pid,adult["hero_id"]))
                self.assertEqual((len(owned),owned[0]["copies"],owned[0]["stars"]),(1,1,1))
                self.sql("UPDATE mini_players SET mythic_shards=300 WHERE id=?",(self.pid,))
                for star,cost in enumerate(COSTS,1):
                    result=upgrade(self.pid,adult["hero_id"],star,self.db,now=604901)
                    self.assertEqual((result["shards_spent"],result["attack"]),(cost,entry["attack"]))
                    self.assertEqual(upgrade(self.pid,adult["hero_id"],star,self.db,now=604902),result)
                self.assertEqual(self.sql("SELECT mythic_shards FROM mini_players WHERE id=?",(self.pid,))[0]["mythic_shards"],0)
                with self.assertRaises(ValueError):upgrade(self.pid,adult["hero_id"],6,self.db)

    def test_all_eight_real_mode_effects_and_gacha_craft_exclusion(self):
        from app.mini.combat import team
        from app.mini.mythic.raid.effects import resolve
        from app.mini.combat.hero_abilities.catalog import get_ability
        from app.mini.mythic.service import craft as craft_hero
        from app.mini.gacha import _choose_hero_code
        for entry in MYTHICS:
            h=self.real(entry["mythic_element"])
            owned=get_player_hero(self.pid,h["id"],self.db)
            for mode in ("normal","raid"):
                frozen=freeze_hero(owned,mode=mode)
                self.assertTrue(get_ability(entry["passive_key"],mode=mode)["configured"])
                self.assertEqual(frozen["passives"][mode]["key"],entry["passive_key"])
                self.assertEqual(frozen["attack"],entry["attack"])
            normal=freeze_hero(owned); state=team.begin([normal],100000,100,chooser=lambda p:p[0])
            after=team.after_hit(state,normal,self.pid,99900,100000,1,100,roller=lambda _:False)
            self.assertIn("state",after)
            template=BOSSES["bosses"][2]
            raid={"snapshot":template,"state":{},"original_max_hp":130000,"max_hp":130000,"current_hp":130000}
            result=resolve(raid,freeze_hero(owned,mode="raid"),self.pid,chooser=lambda p:p[0])
            self.assertTrue(result["passive_configured"])
            with self.assertRaises(ValueError):craft_hero(self.pid,h["id"],self.db)
        for _ in range(100):
            code=_choose_hero_code()
            self.assertNotIn(code,{h["code"] for h in MYTHICS})

    def test_each_real_boss_counter_reward_restart_replay_and_atomic_ledger(self):
        for i,entry in enumerate(BOSSES["bosses"]):
            h=self.real(entry["counter_elements"][0]);now=100+i*86400
            raid=raids.start(self.pid,entry["code"],f"start:{i}",self.db,now=now)
            self.assertEqual(raid["snapshot"]["name"],entry["name"])
            hit=raids.attack(self.pid,raid["id"],h["id"],f"first:{i}",self.db,now=now,chooser=lambda p:p[0])
            if i in (0,1):self.assertEqual(hit["actual_hp_damage"],0)
            saved=raids.state(self.world["id"],self.db,raid_id=raid["id"])
            self.assertTrue(saved["state"]["counter_active"])
            self.assertEqual(saved["state"]["mechanic"]["confirmed_attacks"],1)
            self.assertEqual(raids.attack(self.pid,raid["id"],h["id"],f"first:{i}",self.db,now=now),hit)
            self.sql("UPDATE mini_mythic_raids SET current_hp=1 WHERE id=?",(raid["id"],))
            killer=self.real("dark")
            before={table:self.sql("SELECT * FROM "+table+" ORDER BY 1") for table in ("mini_mythic_raids","mini_mythic_raid_attacks","mini_mythic_raid_damage","mini_mythic_raid_audit","mini_mythic_ledger","mini_mythic_raid_rewards")}
            self.sql("CREATE TRIGGER reject_author_reward BEFORE INSERT ON mini_mythic_ledger BEGIN SELECT RAISE(ABORT,'test ledger failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                raids.attack(self.pid,raid["id"],killer["id"],f"kill:{i}",self.db,now=now+86400,chooser=lambda p:p[0])
            for table,rows in before.items():self.assertEqual(self.sql("SELECT * FROM "+table+" ORDER BY 1"),rows)
            self.sql("DROP TRIGGER reject_author_reward")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:raids.attack(self.pid,raid["id"],killer["id"],f"kill:{i}",self.db,now=now+86400,chooser=lambda p:p[0]),range(2)))
            self.assertEqual(results[0],results[1])
            self.assertEqual(raids.state(self.world["id"],self.db,raid_id=raid["id"])["status"],"rewarded")
            self.assertEqual(results[0]["final"]["fund"],(20,23,26,30)[i])
            rewards=self.sql("SELECT * FROM mini_mythic_raid_rewards WHERE raid_id=?",(raid["id"],))
            self.assertEqual(len(rewards),1)
            self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=?",(raid["id"],))),2)
            self.assertEqual(self.sql("PRAGMA integrity_check")[0]["integrity_check"],"ok")
            self.assertEqual(self.sql("PRAGMA foreign_key_check"),[])


    def test_real_boss_cycles_heal_and_all_hp_phases_persist(self):
        attacker=self.real("dark")
        for i,entry in enumerate(BOSSES["bosses"]):
            raid=raids.start(self.pid,entry["code"],f"phase-start:{i}",self.db,now=100)
            kind=entry["mechanic"]["type"]
            counter=self.real(entry["counter_elements"][0])
            if kind=="periodic_heal":self.sql("UPDATE mini_mythic_raids SET current_hp=max_hp-1000 WHERE id=?",(raid["id"],))
            total=30 if kind=="periodic_heal" else (16 if kind=="armor_cycle" else 11)
            if kind=="hp_defense_phases": total=4
            for number in range(1,total+1):
                if kind=="hp_defense_phases":
                    self.sql("UPDATE mini_mythic_raids SET current_hp=? WHERE id=?",((150000,112500,75000,37500)[number-1],raid["id"]))
                result=raids.attack(self.pid,raid["id"],(attacker if number>1 else counter)["id"],f"phase:{i}:{number}",self.db,now=100+number*86400)
                receipt=result["mechanic"]
                if kind=="armor_cycle":self.assertEqual(receipt["features_used"],["armored"] if (number-1)%15<10 else [])
                elif kind=="flight_cycle":self.assertEqual(receipt["features_used"],["flying"] if (number-1)%10<8 else [])
                elif kind=="hp_defense_phases":self.assertEqual(receipt["features_used"],entry["mechanic"]["phase_features"][number-1])
                elif number in (15,30):
                    self.assertEqual(receipt["heal"]["healed_hp"],min(325,entry["max_hp"]-receipt["heal"]["hp_before"]))
                saved=raids.state(self.world["id"],self.db,raid_id=raid["id"])
                self.assertEqual(saved["state"]["mechanic"]["confirmed_attacks"],number)
                self.assertEqual(saved["snapshot"]["mechanic"],entry["mechanic"])
            raids.cancel(raid["id"],self.world["id"],self.owner,f"phases-cancel:{i}",self.db,confirmed=True,expected_revision=total,now=100+(total+1)*86400)

    def test_real_sun_last_charge_suppresses_heal_and_eclipse_uses_original_hp(self):
        sun=self.real("sun");dark=self.real("dark")
        raid=raids.start(self.pid,BOSSES["bosses"][2]["code"],"sun-start",self.db,now=100)
        raids.attack(self.pid,raid["id"],sun["id"],"sun-charge",self.db,now=100,chooser=lambda pool:pool[0])
        self.sql("UPDATE mini_mythic_raids SET current_hp=129000,state_json=json_set(state_json,'$.mechanic.confirmed_attacks',14,'$.sun.charges',1) WHERE id=?",(raid["id"],))
        result=raids.attack(self.pid,raid["id"],dark["id"],"sun-last",self.db,now=86500,chooser=lambda pool:pool[0])
        self.assertTrue(result["mechanic"]["heal"]["sun_suppressed"])
        self.assertEqual(result["mechanic"]["heal"]["healed_hp"],0)
        self.sql("UPDATE mini_mythic_raids SET current_hp=1,state_json=json_set(state_json,'$.mechanic.confirmed_attacks',29,'$.sun.charges',1) WHERE id=?",(raid["id"],))
        killed=raids.attack(self.pid,raid["id"],sun["id"],"sun-kill",self.db,now=172900,chooser=lambda pool:pool[0])
        self.assertTrue(killed["mechanic"]["heal"]["fatal_hit"])
        self.assertEqual(killed["mechanic"]["heal"]["healed_hp"],0)
        self.assertEqual(raids.state(self.world["id"],self.db)["status"],"rewarded")
        moon=self.real("moon")
        self.sql("UPDATE mini_player_heroes SET stars=6 WHERE player_id=? AND hero_id=?",(self.pid,moon["id"]))
        angel=raids.start(self.pid,BOSSES["bosses"][3]["code"],"eclipse-start",self.db,now=172901)
        result=raids.attack(self.pid,angel["id"],moon["id"],"eclipse",self.db,now=172901,chooser=lambda pool:3 if isinstance(pool[0],int) else pool[0])
        saved=raids.state(self.world["id"],self.db)
        self.assertEqual((saved["original_max_hp"],saved["max_hp"]),(150000,172500))
        self.assertEqual(saved["state"]["lunar"]["gain"],22500)
        self.assertEqual(raids.attack(self.pid,angel["id"],moon["id"],"eclipse",self.db,now=172902),result)

    def test_real_catalog_sync_three_times_preserves_ownership_wallet_and_legacy(self):
        from app.mini.schema import init_mini_db
        for entry in MYTHICS:self.hero(entry["code"],stars=4)
        self.sql("UPDATE mini_players SET coins=321,shards=123,mythic_shards=17 WHERE id=?",(self.pid,))
        self.sql("INSERT INTO mini_mythic_fragments VALUES(?,'legacy_author_import',7)",(self.pid,))
        before={table:self.sql("SELECT * FROM "+table+" ORDER BY 1,2") for table in ("mini_players","mini_player_heroes","mini_mythic_fragments","mini_heroes")}
        for _ in range(3):
            init_mini_db(self.db);sync_hero_catalog(self.db)
            for table,rows in before.items():self.assertEqual(self.sql("SELECT * FROM "+table+" ORDER BY 1,2"),rows)
        self.assertEqual(self.sql("PRAGMA integrity_check")[0]["integrity_check"],"ok")
        self.assertEqual(self.sql("PRAGMA foreign_key_check"),[])

    def test_all_eight_real_normal_battle_loadouts_and_attack(self):
        from datetime import datetime,timezone
        from app.mini.players import create_mini_player
        from app.mini.boss import service,combat
        ally=create_mini_player(self.world["id"],900124,"author_ally","Ally",self.db)["id"]
        villager=self.sql("SELECT id FROM mini_heroes WHERE code='Villager'")[0]["id"]
        self.sql("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",(ally,villager))
        self.sql("UPDATE mini_players SET active_hero_id=? WHERE id=?",(villager,ally))
        now=datetime(2026,10,26,12,tzinfo=timezone.utc)
        for entry in MYTHICS:
            self.hero(entry["code"],stars=1)
            boss=service.create_boss_event(self.world["id"],"simple_village_guy",900123,self.db)
            for pid in (self.pid,ally):service.register_player(boss["id"],pid,self.db)
            service.close_registration(boss["id"],self.db)
            with patch("app.mini.combat.team.secrets.choice",side_effect=lambda pool:pool[0]):
                combat.start_battle(boss["id"],now=now,db_path=self.db)
            rows=self.sql("SELECT * FROM mini_boss_participants WHERE boss_id=? AND player_id=?",(boss["id"],self.pid))
            frozen=json.loads(rows[0]["hero_snapshot_json"])
            self.assertEqual(frozen["passives"]["normal"]["key"],entry["passive_key"])
            current=combat.get_combat_state(boss["id"],db_path=self.db)["current"]
            if current["player_id"]!=self.pid:combat.hit_boss(boss["id"],ally,now=now,db_path=self.db)
            outcome=combat.hit_boss(boss["id"],self.pid,now=now,db_path=self.db)
            self.assertTrue(outcome["applied"])
            combat.force_finish_battle(boss["id"],now=now,db_path=self.db)


class AuthorTelegram(MiniCase,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=audit.TelegramAudit.asyncSetUp
    callback=audit.TelegramAudit.callback
    message=audit.TelegramAudit.message

    async def test_all_eight_collection_photos_and_four_real_raid_handlers(self):
        from app.mini.ui import hero_cards
        from app.mini.mythic.raid import handlers
        from app.mini.combat import team
        for entry in MYTHICS:
            h=self.hero(entry["code"],stars=1);owned=get_player_hero(self.pid,h["id"],self.db)
            caption=hero_cards.hero_caption(owned,full=True)
            await hero_cards.send_hero_card(self.callback("card"),self.world,owned,caption,None)
            self.assertEqual(Path(self.photos.call_args.kwargs["photo"].path).name,entry["image"])
            self.assertIn(entry["name"],self.photos.call_args.kwargs["caption"])
        context=self.enterContext(patch.object(handlers,"load_extended_context",new_callable=AsyncMock))
        for i,entry in enumerate(BOSSES["bosses"]):
            hero=self.hero(next(h["code"] for h in MYTHICS if h["mythic_element"]==entry["counter_elements"][0]),stars=1)
            raid=raids.start(self.pid,entry["code"],f"telegram-start:{i}",self.db)
            context.return_value=(self.world,self.player,f"hit.{raid['id']}.{hero['id']}.real{i}")
            await handlers.action(self.callback("real"));await handlers.action(self.callback("real"))
            self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=?",(raid["id"],))),1)
            self.assertIn(entry["name"],handlers.public_text(raids.state(self.world["id"],self.db)))
            raids.cancel(raid["id"],self.world["id"],self.owner,f"cancel:{i}",self.db,confirmed=True,expected_revision=1)
