import sqlite3
from concurrent.futures import ThreadPoolExecutor
from tests.v1_3.support import MiniCase
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.db import connect_mini_db


class OnboardingTests(MiniCase):
    def tickets(self,pid=None):
        rows=self.sql("SELECT v.quantity FROM mini_inventory v JOIN mini_items i ON i.id=v.item_id WHERE v.player_id=? AND i.code='summon_ticket'",(self.pid if pid is None else pid,))
        return rows[0]['quantity'] if rows else 0

    def test_first_creation_grants_exactly_three_existing_tickets(self):
        self.assertTrue(self.player['onboarding_granted'])
        self.assertEqual(self.tickets(),3)
        self.assertEqual(self.sql('SELECT tickets FROM mini_onboarding_claims')[0]['tickets'],3)
        self.assertEqual(self.sql("SELECT effect_key FROM mini_items WHERE code='summon_ticket'")[0]['effect_key'],'gacha_ticket')
        self.assertEqual(len(self.sql("SELECT * FROM mini_public_notifications WHERE kind='welcome'")),1)

    def test_duplicate_creation_and_restart_do_not_grant(self):
        for _ in range(2):
            init_mini_db(self.db)
            with self.assertRaises(ValueError):
                create_mini_player(self.world['id'],900123,'@tester','Иное имя',self.db)
        self.assertEqual(self.tickets(),3)
        self.assertEqual(len(self.sql('SELECT * FROM mini_onboarding_claims')),1)

    def test_rename_and_restore_do_not_grant(self):
        self.sql("UPDATE mini_players SET character_name='Новое имя' WHERE id=?",(self.pid,))
        with connect_mini_db(self.db) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('DELETE FROM mini_players WHERE id=?',(self.pid,))
        init_mini_db(self.db)
        restored=create_mini_player(self.world['id'],900123,'@tester','Восстановленный',self.db)
        self.assertFalse(restored['onboarding_granted'])
        self.assertEqual(self.tickets(restored['id']),0)
        self.assertEqual(self.sql('SELECT tickets FROM mini_onboarding_claims')[0]['tickets'],3)

    def test_invalid_creation_has_no_reward_or_announcement(self):
        before=self.sql('SELECT * FROM mini_onboarding_claims')
        with self.assertRaises(ValueError):create_mini_player(self.world['id'],900124,'@bad',' ',self.db)
        self.assertEqual(self.sql('SELECT * FROM mini_onboarding_claims'),before)
        self.assertEqual(len(self.sql('SELECT * FROM mini_players')),1)

    def test_reward_failure_rolls_back_character_claim_and_public_intent(self):
        self.sql("CREATE TRIGGER reject_gift BEFORE INSERT ON mini_inventory BEGIN SELECT RAISE(ABORT,'gift failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            create_mini_player(self.world['id'],900124,'@bad','Ошибка',self.db)
        self.assertEqual(len(self.sql('SELECT * FROM mini_players')),1)
        self.assertEqual(len(self.sql('SELECT * FROM mini_onboarding_claims')),1)
        self.assertEqual(len(self.sql('SELECT * FROM mini_public_notifications')),1)

    def test_concurrent_creation_grants_once(self):
        def create():
            try:return create_mini_player(self.world['id'],900125,'@race','Гонка',self.db)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:create(),range(2)))
        successes=[p for p in results if p]
        self.assertEqual(len(successes),1)
        self.assertEqual(self.tickets(successes[0]['id']),3)

    def test_upgrade_marks_existing_players_without_retroactive_gift(self):
        before=self.tickets()
        for table in ('mini_public_notifications','mini_titles','mini_onboarding_claims'):
            self.sql(f'DROP TABLE {table}')
        init_mini_db(self.db)
        self.assertEqual(self.tickets(),before)
        self.assertEqual(self.sql('SELECT tickets FROM mini_onboarding_claims')[0]['tickets'],0)
        self.assertEqual(self.sql('SELECT * FROM mini_public_notifications'),[])

    def test_starter_tickets_use_existing_gacha_without_coin_payment(self):
        from app.mini.gacha import perform_gacha_pull,GachaNoTicket
        from unittest.mock import patch
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            for remaining in (2,1,0):
                result=perform_gacha_pull(self.pid,'ticket',self.db)
                self.assertEqual(result['tickets'],remaining)
                self.assertEqual(result['cost_coins'],0)
            with self.assertRaises(GachaNoTicket):perform_gacha_pull(self.pid,'ticket',self.db)
        self.assertEqual(self.sql('SELECT coins FROM mini_players')[0]['coins'],0)
