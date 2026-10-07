import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock,Mock
os.environ.setdefault('BOT_TOKEN','test-token')
from app.handlers import ROUTERS
from app.handlers.create import create_handler
from app.handlers.inventory import inventory_handler
from app.handlers import admin,characters
from app.mini.superadmin.handlers import superadmin_handler
from app.mini.commands import configure_mini_commands
from aiogram.types import BotCommand
from app.topic_guard import allow_dnd, is_mini_topic
from tests.topic_fixtures import (
    TEST_CHAT_ID, DND_THREAD_ID, MINI_THREAD_ID, isolated_topics, topic_settings,
)


def message(text,thread=DND_THREAD_ID,username='arukozento',user_id=777001):
    return SimpleNamespace(text=text,chat=SimpleNamespace(id=TEST_CHAT_ID),message_thread_id=thread,
        from_user=SimpleNamespace(id=user_id,username=username),answer=AsyncMock(),message_id=8)


class DndGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(isolated_topics())

    async def test_real_guards_follow_topic_type_when_flags_swap(self):
        settings=topic_settings()
        settings[TEST_CHAT_ID][DND_THREAD_ID]['mini']=True
        settings[TEST_CHAT_ID][MINI_THREAD_ID]['mini']=False
        with isolated_topics(settings):
            for thread, allowed in ((DND_THREAD_ID, False), (MINI_THREAD_ID, True)):
                with self.subTest(thread=thread):
                    m=message('/inv @someone',thread)
                    self.assertEqual(is_mini_topic(TEST_CHAT_ID,thread),not allowed)
                    self.assertEqual(await allow_dnd(m),allowed)
                    self.assertEqual(m.answer.await_count,0 if allowed else 1)

    async def test_create_dnd_and_mini(self):
        for thread,expected in [(DND_THREAD_ID,True),(MINI_THREAD_ID,False)]:
            m=message('/create @someone "New hero"',thread)
            with patch('app.handlers.create.check_topic_admin_permission',AsyncMock(return_value=True)),patch('app.handlers.create.get_character_name',return_value=None),patch('app.handlers.create.create_character') as write:
                await create_handler(m);self.assertEqual(write.called,expected)
            self.assertTrue(m.answer.called)

    async def test_inv_dnd_and_mini(self):
        for thread,expected in [(DND_THREAD_ID,True),(MINI_THREAD_ID,False)]:
            m=message('/inv @someone',thread)
            with patch('app.handlers.inventory.get_character_name',return_value='Hero'),patch('app.handlers.inventory.get_inventory',return_value=[('Sword',1,'')]) as read:
                await inventory_handler(m);self.assertEqual(read.called,expected)
            self.assertTrue(m.answer.called)

    async def test_inv_all_mini_does_not_read_dnd(self):
        m=message('/inv all',MINI_THREAD_ID)
        with patch('app.handlers.inventory.get_topic_characters') as read:
            await inventory_handler(m);read.assert_not_called()

    async def test_remote_admin_guards_target_not_current_topic(self):
        for fn,cmd,patch_name in [(admin.admadd_handler,'admadd','add_inventory_item'),
                                  (admin.admdel_handler,'admdel','delete_inventory_item'),
                                  (admin.adminv_handler,'adminv','get_inventory'),
                                  (admin.admcharset_handler,'admcharset','save_character_profile')]:
            for thread,expected in [(DND_THREAD_ID,True),(MINI_THREAD_ID,False)]:
                tail='' if cmd=='adminv' else (' 5 | Mage | Human' if cmd=='admcharset' else ' Sword')
                m=message(f'/{cmd} {TEST_CHAT_ID}:{thread} @someone{tail}',thread=MINI_THREAD_ID if thread==DND_THREAD_ID else DND_THREAD_ID)
                result=[] if cmd=='adminv' else None
                with patch('app.handlers.admin.check_global_admin_permission',AsyncMock(return_value=True)),patch('app.handlers.admin.get_character_name',return_value='Hero'),patch('app.handlers.admin.'+patch_name,return_value=result) as operation:
                    await fn(m);self.assertEqual(operation.called,expected,(cmd,thread))

    async def test_admdel_all_clears_only_requested_target(self):
        m=message(f'/admdel {TEST_CHAT_ID}:{DND_THREAD_ID} @someone aLl')
        with patch('app.handlers.admin.check_global_admin_permission',AsyncMock(return_value=True)),patch('app.handlers.admin.get_character_name',return_value='Hero'),patch('app.handlers.admin.clean_inventory') as clean,patch('app.handlers.admin.delete_inventory_item') as delete:
            await admin.admdel_handler(m)
            clean.assert_called_once_with(TEST_CHAT_ID,DND_THREAD_ID,'@someone');delete.assert_not_called()

    async def test_old_mini_admadd_no_longer_grants(self):
        m=message('/admadd @someone -c 10',MINI_THREAD_ID)
        with patch('app.handlers.admin.check_global_admin_permission',AsyncMock(return_value=True)),patch('app.handlers.admin.add_inventory_item') as write:
            await admin.admadd_handler(m);write.assert_not_called()

    async def test_character_handlers_reject_mini_before_dnd_data_access(self):
        for fn,text in [(characters.char_handler,'/char @someone'),(characters.charset_handler,'/charset @someone 1 | A | B'),(characters.lvlup_handler,'/lvlup')]:
            m=message(text,MINI_THREAD_ID)
            with patch('app.handlers.characters.get_character_name') as read,patch('app.handlers.characters.check_topic_admin_permission') as permission:
                await fn(m);read.assert_not_called();permission.assert_not_called()

    async def test_superadmin_username_never_authorizes_and_owner_needs_no_username(self):
        for user_id,username,allowed in [(777001,None,True),(999,'arukozento',False)]:
            m=message(f'/superchars {TEST_CHAT_ID}:{MINI_THREAD_ID}',username=username,user_id=user_id)
            with patch('app.mini.superadmin.access.SUPERADMIN_USER_ID',777001),patch('app.mini.superadmin.handlers.execute',return_value='done') as execute:
                await superadmin_handler(m);self.assertEqual(execute.called,allowed)

    async def test_command_configuration_removes_legacy_and_keeps_mini_hidden(self):
        bot=SimpleNamespace(get_my_commands=AsyncMock(return_value=[BotCommand(command=x,description='x') for x in ('add','del','clean','admclean','mini','minipanel','inv')]),set_my_commands=AsyncMock())
        await configure_mini_commands(bot)
        cmds={c.command for c in bot.set_my_commands.call_args.args[0]}
        self.assertTrue({'inv','minicreate','create','adminv'}<=cmds)
        self.assertFalse(cmds & {'add','del','clean','admclean','mini','minipanel'})

    def test_router_commands_registered_and_legacy_absent(self):
        commands=set()
        def walk(router):
            for handler in router.message.handlers:
                for f in handler.filters:
                    commands.update(getattr(f.callback,'commands',()))
            for sub in router.sub_routers:walk(sub)
        for router in ROUTERS:walk(router)
        self.assertTrue({'create','adminv','inv','superlook','superadd','superdel','superchars','superluck'}<=commands)
        self.assertFalse(commands & {'add','del','clean','admclean','superadminadd','superadminremove'})

    def test_confirmed_owner_id_is_the_only_pinned_identity(self):
        from app.mini.superadmin.access import SUPERADMIN_USER_ID,is_superadmin
        self.assertEqual(SUPERADMIN_USER_ID,694384548)
        self.assertTrue(is_superadmin(694384548))
        self.assertFalse(is_superadmin(694384549))
