import sqlite3
from concurrent.futures import ThreadPoolExecutor
from tests.v1_3.support import MiniCase
from app.mini.favorites import get_favorites,add_favorite,remove_favorite,replace_favorite,save_order,is_favorite
from app.mini.schema import init_mini_db


class FavoriteTests(MiniCase):
    def setUp(self):
        super().setUp()
        self.ids=[]
        for code in ('Villager','CityBlacksmith','FrostKnight','Azrael'):
            rows=self.sql('SELECT id FROM mini_heroes WHERE code=?',(code,))
            if not rows:
                rows=self.sql('SELECT id FROM mini_heroes WHERE active=1 AND id NOT IN (%s) ORDER BY id LIMIT 1'%(','.join(map(str,self.ids)) or '0'))
            h=rows[0]['id'];self.ids.append(h)
            self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.pid,h))

    def test_new_player_starts_empty(self): self.assertEqual(get_favorites(self.pid,self.db),[])

    def test_add_first_second_third_in_stable_order(self):
        for n,h in enumerate(self.ids[:3]):
            self.assertTrue(add_favorite(self.pid,h,self.db))
            self.assertEqual(get_favorites(self.pid,self.db),self.ids[:n+1])

    def test_duplicate_is_idempotent(self):
        add_favorite(self.pid,self.ids[0],self.db)
        self.assertFalse(add_favorite(self.pid,self.ids[0],self.db))
        self.assertTrue(is_favorite(self.pid,self.ids[0],self.db))
        self.assertEqual(get_favorites(self.pid,self.db),self.ids[:1])

    def test_fourth_is_rejected_without_mutation(self):
        for h in self.ids[:3]:add_favorite(self.pid,h,self.db)
        with self.assertRaisesRegex(ValueError,'заменить'):add_favorite(self.pid,self.ids[3],self.db)
        self.assertEqual(get_favorites(self.pid,self.db),self.ids[:3])

    def test_schema_itself_caps_at_three(self):
        for h in self.ids[:3]:add_favorite(self.pid,h,self.db)
        with self.assertRaises(sqlite3.IntegrityError):self.sql('INSERT INTO mini_hero_favorites VALUES(?,?,3)',(self.pid,self.ids[3]))

    def test_replace_retains_slot_and_repeated_callback_does_not_duplicate(self):
        for h in self.ids[:3]:add_favorite(self.pid,h,self.db)
        self.assertTrue(replace_favorite(self.pid,self.ids[1],self.ids[3],self.db))
        self.assertFalse(replace_favorite(self.pid,self.ids[1],self.ids[3],self.db))
        self.assertEqual(get_favorites(self.pid,self.db),[self.ids[0],self.ids[3],self.ids[2]])

    def test_stale_replacement_rejects_missing_old_hero(self):
        add_favorite(self.pid,self.ids[0],self.db)
        with self.assertRaisesRegex(ValueError,'устарела'):replace_favorite(self.pid,self.ids[1],self.ids[2],self.db)

    def test_delete_and_order_persist_after_restart(self):
        for h in self.ids[:3]:add_favorite(self.pid,h,self.db)
        save_order(self.pid,list(reversed(self.ids[:3])),self.db)
        init_mini_db(self.db)
        self.assertEqual(get_favorites(self.pid,self.db),list(reversed(self.ids[:3])))
        self.assertTrue(remove_favorite(self.pid,self.ids[1],self.db))
        self.assertFalse(remove_favorite(self.pid,self.ids[1],self.db))
        self.assertEqual(get_favorites(self.pid,self.db),[self.ids[2],self.ids[0]])

    def test_invalid_order_cannot_add_duplicate_or_unknown(self):
        add_favorite(self.pid,self.ids[0],self.db)
        for values in ([self.ids[0],self.ids[0]],[self.ids[1]]):
            with self.assertRaises(ValueError):save_order(self.pid,values,self.db)
        self.assertEqual(get_favorites(self.pid,self.db),self.ids[:1])

    def test_unowned_and_other_player_cannot_add(self):
        from app.mini.players import create_mini_player
        other=create_mini_player(self.world['id'],123456,'@other','Other',self.db)
        with self.assertRaises(ValueError):add_favorite(other['id'],self.ids[0],self.db)
        with self.assertRaises(ValueError):add_favorite(self.pid,999999,self.db)

    def test_gacha_inactive_favorite_remains_and_lost_ownership_is_cleaned(self):
        for h in self.ids[:3]:add_favorite(self.pid,h,self.db)
        self.sql('UPDATE mini_heroes SET active=0 WHERE id=?',(self.ids[0],))
        self.sql('DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(self.pid,self.ids[1]))
        self.assertEqual(get_favorites(self.pid,self.db),[self.ids[0],self.ids[2]])
        self.assertFalse(add_favorite(self.pid,self.ids[0],self.db))
        with self.assertRaises(ValueError):add_favorite(self.pid,self.ids[1],self.db)

    def test_missing_catalog_hero_is_cleaned_even_with_legacy_fk_disabled(self):
        add_favorite(self.pid,self.ids[0],self.db)
        self.sql('DELETE FROM mini_heroes WHERE id=?',(self.ids[0],))
        self.assertEqual(get_favorites(self.pid,self.db),[])

    def test_concurrent_additions_never_exceed_three(self):
        def add(h):
            try: return add_favorite(self.pid,h,self.db)
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(add,self.ids))
        self.assertEqual(len(get_favorites(self.pid,self.db)),3)
