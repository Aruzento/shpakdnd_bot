import unittest
from contextlib import ExitStack
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.v1_3.support import MiniCase
from app.mini.handlers import _player_menu, submenu, submenu_callback
from app.mini.ui.hero_selector import render_selector, filtered, options, DIGITS, EMPTY, FIELDS, selector_callback
from app.mini.ui.hero_cards import hero_caption, hero_card_menu
from app.mini.ui.heroes import favorite_callback, hero_card_callback
from app.mini import favorites
from app.mini.heroes import get_player_heroes, get_player_hero
from app.mini.gacha import get_gacha_state
from app.mini.hero_upgrades import upgrade_hero, HeroUpgradeError


def buttons(markup): return [b for row in markup.inline_keyboard for b in row]
def token_for(**filters):
    opts=options()
    return ''.join(DIGITS[opts[n].index(filters[field])+1] if field in filters else '0' for n,field in enumerate(FIELDS))+'0'


class SelectorViewTests(unittest.TestCase):
    def setUp(self):
        self.heroes=[dict(id=n,name=f'Герой {n}',rarity='rare',stars=n%5,attack=42,shards=0,
                         faction='commoners',class_tag='guardian',damage_type='slashing',attack_range='melee',special_trait='none',active=1)
                     for n in range(1,14)]

    def test_favorites_first_same_selector_and_ordinary_choice_in_all_modes(self):
        for mode,expected in [('c','mini:hero:1:42:3'),('b','miniboss:hero:1:42:12:3'),('t','mini:tower:1:42:pick.12.3')]:
            text,markup=render_selector(self.heroes,[3,1],mode,1,42,12)
            self.assertEqual(text,'⭐ Избранные')
            self.assertEqual(markup.inline_keyboard[0][0].text,'🟣 Герой 3 ★3')
            self.assertEqual(markup.inline_keyboard[0][0].callback_data,expected)
            self.assertEqual([r[0].text for r in markup.inline_keyboard[2:4]],['👥 Показать всех','🔎 Поиск по фильтру'])

    def test_empty_and_missing_favorites_keep_both_paths(self):
        text,markup=render_selector(self.heroes,[999],'c',1,42)
        self.assertEqual(text,'⭐ Избранные\nПока никого нет.')
        self.assertEqual([r[0].text for r in markup.inline_keyboard[:2]],['👥 Показать всех','🔎 Поиск по фильтру'])

    def test_all_six_per_page_two_per_row_no_details(self):
        text,markup=render_selector(self.heroes,[],'b',1,42,12,view='all')
        self.assertEqual(text,'👥 Все герои')
        self.assertEqual([len(row) for row in markup.inline_keyboard[:3]],[2,2,2])
        self.assertEqual(markup.inline_keyboard[3][0].text,'1 / 3')
        self.assertIn('.all.1.',markup.inline_keyboard[3][1].callback_data)
        for b in buttons(markup)[:6]:
            self.assertRegex(b.text,r'^🟣 Герой \d+ ★\d$')
            self.assertNotIn('42',b.text)
        _,last=render_selector(self.heroes,[],'b',1,42,12,view='all',page=99)
        self.assertEqual(last.inline_keyboard[0][0].text,'🟣 Герой 13 ★3')
        self.assertIn('3 / 3',[b.text for b in buttons(last)])

    def test_every_real_catalog_field_filters_owned_list(self):
        for field in FIELDS:
            value=self.heroes[0][field]
            other=dict(self.heroes[1],id=99)
            other[field]=next(v for v in options()[FIELDS.index(field)] if v!=value)
            selected=filtered([self.heroes[0],other],token_for(**{field:value}))
            self.assertEqual([h['id'] for h in selected],[1])

    def test_combined_filter_and_empty_results_have_navigation(self):
        token=token_for(faction='dark',class_tag='guardian',attack_range='ranged')
        text,markup=render_selector(self.heroes,[],'t',1,42,2,view='results',token=token)
        self.assertIn('Подходящих героев нет',text)
        self.assertTrue(any('Изменить фильтр' in b.text for b in buttons(markup)))
        self.assertTrue(any('Назад' in b.text for b in buttons(markup)))

    def test_upgrade_filter_only_collection(self):
        funded=dict(self.heroes[0],shards=10000)
        self.assertEqual([h['id'] for h in filtered([funded,self.heroes[1]],'0000001')],[1])
        for mode in ('b','t'):
            _,markup=render_selector(self.heroes,[],mode,1,42,2,view='filter')
            self.assertFalse(any('Можно улучшить' in b.text for b in buttons(markup)))
            with self.assertRaises(ValueError):filtered(self.heroes,'0000001',mode)

    def test_filters_and_values_use_human_labels_and_have_reset_results_back(self):
        _,markup=render_selector(self.heroes,[],'c',1,42,view='filter',token=token_for(class_tag='guardian'))
        labels=[b.text for b in buttons(markup)]
        self.assertIn('Класс: Страж',labels)
        for phrase in ('Посмотреть результаты','Сбросить','Назад'):
            self.assertTrue(any(phrase in label for label in labels))
        for n in range(6):
            _,values=render_selector(self.heroes,[],'c',1,42,view=f'f{n}')
            self.assertEqual(values.inline_keyboard[0][0].text,'Любое')
            self.assertTrue(any('К фильтрам' in b.text for b in buttons(values)))
            self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(values)))

    def test_main_exact_five_buttons_and_supported_green_style(self):
        menu=_player_menu(1,42)
        self.assertEqual([len(row) for row in menu.inline_keyboard],[2,1,2])
        self.assertEqual([b.text for b in buttons(menu)],['⚔️ Приключения','🧙 Герой','✨ Призвать героя','🎪 Ярмарка','📖 Ещё'])
        summon=menu.inline_keyboard[1][0]
        self.assertEqual(summon.style,'success')
        self.assertEqual(summon.callback_data,'mini:gacha:1:42')

    def test_submenu_entries_and_back_no_old_upgrades(self):
        expected={'adventures':['🔥 Босс','🏰 Испытания','⚔️ Ежедневный бой'],
            'heroarea':['👤 Профиль','📚 Коллекция','🛡 Экипировка','🎒 Инвентарь'],
            'fair':['🛒 Магазин','🎪 События'],'more':['📖 Правила','🔄 Обновить']}
        for action,labels in expected.items():
            _,markup=submenu(action,1,42)
            self.assertEqual([b.text for b in buttons(markup)],labels+['⬅️ Назад'])
            self.assertEqual(buttons(markup)[-1].callback_data,'mini:home:1:42')
            self.assertFalse(any(b.text in ('Герои','Улучшения') for b in buttons(markup)))


class CardAndFavoriteUITests(MiniCase,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.ids=[r['id'] for r in self.sql('SELECT id FROM mini_heroes WHERE active=1 ORDER BY id LIMIT 4')]
        for h in self.ids:self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.pid,h))

    def callback(self,action,tail):
        return SimpleNamespace(id='ui-new',data=f"mini:{action}:{self.world['id']}:900123:{tail}",
            from_user=SimpleNamespace(id=900123,username='tester'),answer=AsyncMock(),
            message=SimpleNamespace(ephemeral_message_id='old',delete_ephemeral=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),send_photo=AsyncMock()))

    def services(self):
        stack=ExitStack()
        stack.enter_context(patch('app.mini.ui.heroes._load_extended_context',side_effect=lambda c,p: self.context(c)))
        for name in ('get_favorites','add_favorite','remove_favorite','replace_favorite','is_favorite'):
            stack.enter_context(patch(f'app.mini.ui.heroes.{name}',side_effect=partial(getattr(favorites,name),db_path=self.db)))
        stack.enter_context(patch('app.mini.ui.heroes.get_player_hero',side_effect=partial(get_player_hero,db_path=self.db)))
        stack.enter_context(patch('app.mini.ui.heroes.get_gacha_state',side_effect=partial(get_gacha_state,db_path=self.db)))
        stack.enter_context(patch('app.mini.ui.hero_cards.get_hero_image',return_value=None))
        return stack

    def context(self,callback):return dict(self.world,enabled=True),self.player,callback.data.split(':',4)[4]

    async def test_fourth_favorite_opens_replacement_then_replaces_and_deletes_old_menu(self):
        for h in self.ids[:3]:favorites.add_favorite(self.pid,h,self.db)
        callback=self.callback('favorite',f'add.{self.ids[3]}')
        with self.services():await favorite_callback(callback)
        sent=callback.bot.send_message.call_args.kwargs
        self.assertEqual(sent['text'],'⭐ Кого заменить?')
        self.assertEqual(len(sent['reply_markup'].inline_keyboard),4)
        self.assertIn(f'replace.{self.ids[3]}.{self.ids[1]}',sent['reply_markup'].inline_keyboard[1][0].callback_data)
        self.assertEqual(favorites.get_favorites(self.pid,self.db),self.ids[:3])
        callback.message.delete_ephemeral.assert_awaited_once()
        self.assertEqual(sent['ephemeral_message_parameters'].receiver_user_id,900123)
        replacement=self.callback('favorite',f'replace.{self.ids[3]}.{self.ids[1]}')
        with self.services():
            await favorite_callback(replacement)
            await favorite_callback(replacement)
        self.assertEqual(favorites.get_favorites(self.pid,self.db),[self.ids[0],self.ids[3],self.ids[2]])
        self.assertIn('☆ Убрать из избранного',[b.text for b in buttons(replacement.bot.send_message.call_args.kwargs['reply_markup'])])

    async def test_repeat_add_and_removed_ownership_callbacks_are_safe(self):
        callback=self.callback('favorite',f'add.{self.ids[0]}')
        with self.services():
            await favorite_callback(callback);await favorite_callback(callback)
        self.assertEqual(favorites.get_favorites(self.pid,self.db),self.ids[:1])
        self.sql('DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(self.pid,self.ids[0]))
        stale=self.callback('favorite',f'add.{self.ids[0]}')
        with self.services():await favorite_callback(stale)
        stale.bot.send_message.assert_not_awaited()
        self.assertTrue(stale.answer.call_args.kwargs['show_alert'])
        self.assertEqual(favorites.get_favorites(self.pid,self.db),[])

    async def test_collection_hero_opens_full_card_with_upgrade_and_favorite_toggle(self):
        callback=self.callback('hero',str(self.ids[0]))
        with self.services():await hero_card_callback(callback)
        sent=callback.bot.send_message.call_args.kwargs
        for phrase in ('Редкость:','Копий:','Осколки:','Особый эффект:','Улучшение до'):
            self.assertIn(phrase,sent['text'])
        labels=[b.text for b in buttons(sent['reply_markup'])]
        self.assertTrue(any('Улучшить' in label for label in labels))
        self.assertIn('⭐ Сделать избранным',labels)
        self.assertFalse(any('Ещё призыв' in label for label in labels))
        callback.message.delete_ephemeral.assert_awaited_once()

    def test_full_caption_keeps_progress_and_tags_with_long_description(self):
        hero=get_player_hero(self.pid,self.ids[0],self.db)
        hero.update(description='Описание '*150,passive_text='Способность '*120,damage_type='slashing',attack_range='melee')
        text=hero_caption(hero,full=True)
        self.assertLessEqual(len(text),1020)
        for phrase in ('Редкость:','Копий:','Осколки:','Улучшение до','Режущий','Ближний бой'):
            self.assertIn(phrase,text)

    def test_gacha_card_retains_repeat_share_and_no_favorite_actions(self):
        hero=get_player_hero(self.pid,self.ids[0],self.db)
        menu=hero_card_menu(1,42,hero,get_gacha_state(self.pid,self.db),allow_share=True)
        labels=[b.text for b in buttons(menu)]
        self.assertTrue(any('Ещё призыв' in label for label in labels))
        self.assertIn('📣 Похвастаться',labels)
        self.assertFalse(any('избран' in label for label in labels))

    def test_stale_upgrade_cannot_charge_or_advance_again(self):
        self.sql('UPDATE mini_players SET shards=1000 WHERE id=?',(self.pid,))
        upgrade_hero(self.pid,self.ids[0],self.db,expected_stars=0)
        before=self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))
        with self.assertRaises(HeroUpgradeError):upgrade_hero(self.pid,self.ids[0],self.db,expected_stars=0)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,)),before)
        self.assertEqual(self.sql('SELECT stars FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(self.pid,self.ids[0]))[0]['stars'],1)


class SelectorCallbackTests(unittest.IsolatedAsyncioTestCase):
    def callback(self,tail,owner=42):
        return SimpleNamespace(id='select',data=f'mini:select:1:{owner}:{tail}',from_user=SimpleNamespace(id=42),answer=AsyncMock(),
            bot=SimpleNamespace(send_message=AsyncMock()),message=SimpleNamespace(ephemeral_message_id='old',delete_ephemeral=AsyncMock()))

    async def test_foreign_owner_rejected_before_database_or_rendering(self):
        c=self.callback('c0.all.0.0000000',owner=99)
        with patch('app.mini.ui.context.get_mini_world_by_id',side_effect=AssertionError('must reject first')):
            await selector_callback(c)
        self.assertEqual(c.answer.call_args.args[0],'Это меню другого игрока.')
        c.bot.send_message.assert_not_awaited()

    async def test_stale_boss_and_tower_selector_rejected(self):
        for mode in ('b','t'):
            c=self.callback(f'{mode}12.all.0.0000000')
            with patch('app.mini.ui.hero_selector.load_extended_context',return_value=({'id':1},{'id':2},c.data.split(':',4)[4])), \
                 patch('app.mini.boss.service.get_boss',return_value={'world_id':1,'status':'fighting'}), \
                 patch('app.mini.tower.service.get_state',return_value={'completed':False,'attempt':{'id':4},'floor':{'floor':12}}):
                await selector_callback(c)
            self.assertTrue(c.answer.call_args.kwargs['show_alert'])
            c.bot.send_message.assert_not_awaited()

    async def test_filter_change_reset_and_pagination_replace_ephemeral(self):
        hero=dict(id=1,name='Герой',rarity='rare',stars=1,attack=3,shards=0,class_tag='guardian',active=1)
        value=DIGITS[options()[2].index('guardian')+1]
        for tail in (f'c0.s2{value}.0.0000000','c0.reset.0.0000001','c0.all.0.0000000'):
            c=self.callback(tail)
            with patch('app.mini.ui.hero_selector.load_extended_context',return_value=({'id':1,'chat_id':-1,'thread_id':1},{'id':2},tail)), \
                 patch('app.mini.ui.hero_selector.get_player_heroes',return_value=[hero]), \
                 patch('app.mini.ui.hero_selector.get_favorites',return_value=[]):
                await selector_callback(c)
            c.bot.send_message.assert_awaited_once();c.message.delete_ephemeral.assert_awaited_once()
            if '.s2' in tail:self.assertIn('Класс: Страж',[b.text for b in buttons(c.bot.send_message.call_args.kwargs['reply_markup'])])
            if '.reset.' in tail:self.assertIn('⬆️ Можно улучшить: нет',[b.text for b in buttons(c.bot.send_message.call_args.kwargs['reply_markup'])])

    async def test_submenu_replaces_personal_ephemeral(self):
        c=self.callback('unused');c.data='mini:heroarea:1:42'
        with patch('app.mini.handlers._load_personal_context',return_value=({'id':1,'chat_id':-1,'thread_id':1},{'id':2})):
            await submenu_callback(c)
        self.assertEqual(c.bot.send_message.call_args.kwargs['text'],'🧙 Герой')
        c.message.delete_ephemeral.assert_awaited_once()
