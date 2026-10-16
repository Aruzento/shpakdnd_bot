"""Live builders for the additional V1.4.3 matrix; no economic mutations."""
import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.v1_4_2.test_screen_contracts import sample_heroes,button_handlers,WORLD,USER
from app.mini.ui import hero_cards
from app.mini.ui.combat_guide import guide_screen
from app.mini.events import pvp_handlers as pvp

IDS=("hero_resident_collection","hero_resident_profile","pvp_lobby","pvp_open_private",
     "pvp_choice_private","pvp_wait_private","pvp_public_open","pvp_public_choosing",
     "pvp_public_win","pvp_public_draw","pvp_public_expired",
     *(f"combat_guide_{i}" for i in range(5)))

def render_additional(sid):
    hero=dict(sample_heroes()[0],village_resident={"hero_id":sample_heroes()[0]["id"],"revision":3,"building":"mine"})
    if sid.startswith("hero_resident"):
        profile=sid.endswith("profile")
        return hero_cards.hero_caption(hero,full=not profile),hero_cards.hero_card_menu(
            WORLD,USER,hero,{},collection=not profile,profile=profile)
    if sid.startswith("combat_guide_"):return guide_screen(WORLD,USER,int(sid.rsplit("_",1)[1]))
    members=[dict(player_id=1,character_name="Первый",telegram_user_id=USER,choice=None),
             dict(player_id=2,character_name="Второй",telegram_user_id=USER+1,choice=None)]
    session=dict(id=77,world_id=WORLD,creator_id=1,acceptor_id=2,status="choosing",
                 members=members,winner_id=None,outcome=None)
    if sid=="pvp_lobby":return pvp.lobby_screen(WORLD,USER,1,None,[])
    if sid in {"pvp_open_private","pvp_public_open"}:
        session.update(status="open",acceptor_id=None,members=members[:1])
    if sid=="pvp_wait_private":members[0]["choice"]="saint"
    if sid.endswith("_win"):
        session.update(status="finished",outcome="win",winner_id=1)
        members[0]["choice"]="saint";members[1]["choice"]="demon"
    if sid.endswith("_draw"):
        session.update(status="finished",outcome="draw")
        members[0]["choice"]=members[1]["choice"]="saint"
    if sid.endswith("_expired"):session.update(status="expired")
    if sid.startswith("pvp_public"):return pvp.public_screen(session)
    return pvp.personal_screen(WORLD,USER,1,session)

class FollowupScreenTests(unittest.TestCase):
    def test_additional_matrix_matches_actual_builders_and_registered_handlers(self):
        path=Path(__file__).resolve().parents[2]/"docs/ui/v1.4.3-matrix.json"
        matrix=json.loads(path.read_text(encoding="utf-8"))["additional_screens"]
        self.assertEqual([r["screen"] for r in matrix],list(IDS))
        for record in matrix:
            with self.subTest(screen=record["screen"]),patch("secrets.token_hex",return_value="11223344"):
                body,markup=render_additional(record["screen"])
                self.assertTrue(body)
                self.assertLessEqual(len(body.encode("utf-16-le"))//2,4096)
                self.assertEqual([[b.text for b in row] for row in markup.inline_keyboard],
                                 [[b["text"] for b in row] for row in record["buttons"]])
                self.assertEqual(asyncio.run(button_handlers(markup)),
                                 [[b["handlers"] for b in row] for row in record["buttons"]])
                for row in markup.inline_keyboard:
                    for b in row:self.assertLessEqual(len(b.callback_data.encode()),64)
