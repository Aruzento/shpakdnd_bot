import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from app.mini.rules import RULES_TEXT,SECTIONS
from app.mini.ui.activities import rules_menu,rules_callback,rules_section_callback
from app.mini.tower.handlers import render_state,render_hero_selection
from app.mini.tower.catalog import get_floor
from app.mini.equipment.handlers import render_equipment
from app.mini.ui.inventory import _inventory_menu


class ReleaseUIContractTests(unittest.TestCase):
    def test_rules_main_menu_all_sections_and_back(self):
        self.assertTrue(RULES_TEXT.startswith('📖 D&D Mini — как играть'))
        buttons=[b for row in rules_menu(1,20).inline_keyboard for b in row]
        self.assertEqual([b.text for b in buttons[:-1]],['🚀 Быстрый старт','🎴 Герои и гача','👹 Боссы','🔄 Фракции и особенности','🏰 Испытания','🛡 Экипировка','💰 Монеты и осколки','🎪 События'])
        self.assertEqual(buttons[-1].callback_data,'mini:home:1:20')
        for key in SECTIONS:
            self.assertTrue(any(b.callback_data==f'mini:rulespage:1:20:{key}' for b in buttons))
            self.assertEqual(rules_menu(1,20,section=True).inline_keyboard[-1][0].callback_data,'mini:rules:1:20')

    def test_tower_card_format_and_trait_order(self):
        enemy=dict(get_floor(11),name='Моб',max_hp=24,faction='commoners',damage_type='slashing',class_tag='guardian',attack_range='melee',special_trait='none',features=[])
        state=dict(floor=enemy,attempt=None,completed=False,highest_cleared=10,equipment_bonus=5)
        text,_=render_state(state,1,20)
        self.assertTrue(text.startswith('🏰 Испытания: 11/200\n\nМоб ● Простолюдины\n\nHP: 24'))
        self.assertIn('Режущий | Страж\nБлижний бой | Нет особого свойства\n\nОсобенности: нет',text)
        for value in ('commoners','guardian','Equipment','matchup'):self.assertNotIn(value,text)

    def test_selection_uses_buttons_and_separate_gear_bonus(self):
        enemy=dict(get_floor(1),name='Скелет-вахтёр: смена 1',features=[])
        state=dict(floor=enemy,equipment_bonus=5)
        heroes=[dict(id=1,name='Азраэль',faction='dark',attack=1135),dict(id=2,name='Багбир-засадник',faction='monsters',attack=9)]
        text,markup=render_hero_selection(state,heroes,1,20)
        self.assertIn('🎴 Выбор героя\n\nСкелет-вахтёр: смена 1 · Простолюдины',text)
        self.assertIn('Особенности: нет',text)
        self.assertNotIn('Азраэль',text);self.assertNotIn('Багбир',text)
        self.assertEqual(markup.inline_keyboard[0][0].text,'Азраэль · Тьма · ATK 1135 +5')
        self.assertEqual(markup.inline_keyboard[1][0].text,'Багбир-засадник · Монстры · ATK 9 +5')
        state['equipment_bonus']=0
        _,markup=render_hero_selection(state,heroes,1,20)
        self.assertNotIn('+0',markup.inline_keyboard[0][0].text)

    def test_selection_paginates_and_can_return(self):
        heroes=[dict(id=i,name=str(i),faction='dark',attack=3) for i in range(17)]
        _,markup=render_hero_selection(dict(floor=get_floor(1),equipment_bonus=0),heroes,1,20,1)
        self.assertEqual(len([b for row in markup.inline_keyboard for b in row if ':pick.' in b.callback_data]),8)
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data,'mini:tower:1:20')
        self.assertTrue(all(len(b.callback_data.encode())<=64 for row in markup.inline_keyboard for b in row))

    def test_equipment_three_slots_and_requested_buttons(self):
        state=dict(attack_bonus=5,owned=[],equipped=dict(helmet=dict(name='Ведро паладина на больничном',attack_bonus=1),ring=dict(name='Кольцо некроманта-вегетарианца',attack_bonus=4)))
        text,markup=render_equipment(state,1,20)
        for value in ('Надетая экипировка даёт бонусы в «Испытаниях».','Шлем: Ведро паладина на больничном +1','Кольцо: Кольцо некроманта-вегетарианца +4','Плащ: пусто','Ненадетые предметы можно посмотреть в «Инвентаре».'):self.assertIn(value,text)
        self.assertEqual([row[0].text for row in markup.inline_keyboard],['Надеть...','Снять...','Назад'])
        for value in ('Tower','Equipment','Во владении'):self.assertNotIn(value,text)

    def test_inventory_links_to_owned_equipment(self):
        markup=_inventory_menu(1,20,{})
        self.assertTrue(any(b.callback_data=='mini:equipment:1:20:inventory.0' for row in markup.inline_keyboard for b in row))

    def test_equipment_picker_and_remove_menu(self):
        item=dict(code='eq_helmet_001',slot='helmet',name='Шлем',attack_bonus=1,quantity=1)
        state=dict(owned=[item],equipped={},attack_bonus=0)
        _,markup=render_equipment(state,1,20,mode='choose_equip')
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,'mini:equipment:1:20:equip.eq_helmet_001')
        state['equipped']={'helmet':item}
        _,markup=render_equipment(state,1,20,mode='choose_remove')
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,'mini:equipment:1:20:unequip.helmet')


class RulesNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_and_each_section_navigation(self):
        cb=SimpleNamespace(from_user=SimpleNamespace(id=20),answer=AsyncMock())
        with patch('app.mini.ui.activities._load_personal_context',AsyncMock(return_value=({'id':1},{}))),patch('app.mini.ui.activities._edit_private',AsyncMock()) as edit:
            await rules_callback(cb);self.assertEqual(edit.call_args.args[1],RULES_TEXT)
        for key in SECTIONS:
            with patch('app.mini.ui.activities.load_extended_context',AsyncMock(return_value=({'id':1},{},key))),patch('app.mini.ui.activities._edit_private',AsyncMock()) as edit:
                await rules_section_callback(cb)
                self.assertEqual(edit.call_args.args[1],SECTIONS[key][1])
                self.assertEqual(edit.call_args.args[2].inline_keyboard[-1][0].callback_data,'mini:rules:1:20')

    async def test_unknown_rule_section_is_alerted(self):
        cb=SimpleNamespace(from_user=SimpleNamespace(id=20),answer=AsyncMock())
        with patch('app.mini.ui.activities.load_extended_context',AsyncMock(return_value=({'id':1},{},'bad'))),patch('app.mini.ui.activities._edit_private',AsyncMock()) as edit:
            await rules_section_callback(cb);edit.assert_not_awaited()
        self.assertTrue(cb.answer.call_args.kwargs['show_alert'])
