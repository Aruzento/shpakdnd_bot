from tests.v1_3.support import MiniCase
from app.mini.heroes import get_player_heroes,set_active_hero
from app.mini.favorites import add_favorite,get_favorites
from app.mini.tower.service import select_hero
from app.mini.ui.hero_selector import render_selector,hero_label,options,DIGITS


class ActiveMarkerTests(MiniCase):
    def setUp(self):
        super().setUp();self.first=self.hero('Lasar')['id'];self.second=self.hero('Villager')['id']
        for hero in (self.first,self.second):add_favorite(self.pid,hero,self.db)

    def hero_buttons(self,mode='c',view='home'):
        heroes=get_player_heroes(self.pid,self.db,sync_catalog=False)
        _,markup=render_selector(heroes,get_favorites(self.pid,self.db),mode,self.world['id'],900123,1 if mode!='c' else 0,view=view)
        return [b for row in markup.inline_keyboard for b in row if b.callback_data.startswith(('mini:hero:','miniboss:hero:')) or ':pick.' in b.callback_data]

    def test_active_marker_in_favorites_all_and_filter_results(self):
        for view in ('home','all','results'):
            buttons=self.hero_buttons(view=view)
            self.assertEqual(sum(b.text.endswith(' ✅') for b in buttons),1)
            active=next(b for b in buttons if b.text.endswith(' ✅'))
            self.assertTrue(active.callback_data.endswith(':'+str(self.second)))
            self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons))

    def test_marker_moves_immediately_after_global_selection(self):
        set_active_hero(self.pid,self.first,self.db)
        for view in ('home','all','results'):
            active=[b for b in self.hero_buttons(view=view) if b.text.endswith(' ✅')]
            self.assertEqual(len(active),1);self.assertTrue(active[0].callback_data.endswith(':'+str(self.first)))
        self.assertEqual(get_favorites(self.pid,self.db),[self.first,self.second])

    def test_tower_choice_and_boss_do_not_receive_global_marker(self):
        select_hero(self.pid,self.first,self.db)
        for mode in ('t','b'):
            for view in ('home','all','results'):
                self.assertTrue(all(not b.text.endswith(' ✅') for b in self.hero_buttons(mode,view)))
        hero=next(h for h in get_player_heroes(self.pid,self.db,sync_catalog=False) if h['id']==self.second)
        self.assertFalse(hero_label(hero).endswith(' ✅'))

    def test_pagination_keeps_only_active_marker_on_its_page(self):
        heroes=[dict(id=i,name=str(i),rarity='common',stars=0,is_active=i==7) for i in range(1,9)]
        for page,expected in ((0,0),(1,1)):
            _,markup=render_selector(heroes,[],'c',1,900123,view='all',page=page)
            self.assertEqual(sum(b.text.endswith(' ✅') for row in markup.inline_keyboard for b in row),expected)

    def test_real_filter_preserves_active_marker_and_favorite_status(self):
        heroes=get_player_heroes(self.pid,self.db,sync_catalog=False)
        active=next(h for h in heroes if h['is_active'])
        token='0'+DIGITS[options()[1].index(active['faction'])+1]+'00000'
        _,markup=render_selector(heroes,get_favorites(self.pid,self.db),'c',1,900123,view='results',token=token)
        buttons=[b for row in markup.inline_keyboard for b in row if b.callback_data.startswith('mini:hero:')]
        self.assertEqual(sum(b.text.endswith(' ✅') for b in buttons),1)
        self.assertTrue(any(b.callback_data.endswith(':'+str(active['id'])) and b.text.endswith(' ✅') for b in buttons))
        self.assertIn(active['id'],get_favorites(self.pid,self.db))
