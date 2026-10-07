"""ALL targets reuse single-player rules, scoped to one Mini world."""
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from app.mini.db import connect_mini_db
from app.mini.players import create_mini_player
from app.mini.worlds import register_mini_world
from app.mini.schema import init_mini_db
from app.mini.shop import sync_shop_catalog
from app.mini.superadmin.parser import parse_command
from app.mini.superadmin import service
from app.mini.superadmin.handlers import superadmin_handler
from app.mini.tower.service import select_hero,start_attempt
from app.mini.favorites import add_favorite


class AllParserTests(unittest.TestCase):
    def test_all_normalized_for_every_target_command_and_resource(self):
        commands=[('superlook',tail) for tail in ('','-c','-c -s','-p','-i')]
        commands += [(action,tail) for action in ('superadd','superdel')
                     for tail in ('-c 100','-s 100','-p Villager','-i summon_ticket')]
        commands += [('superluck','')]
        for action,tail in commands:
            for target in ('ALL','all','All'):
                with self.subTest(action=action,tail=tail,target=target):
                    command=parse_command(f'/{action} -100123:2684 {target} {tail}')
                    self.assertEqual(command.target,'all')
                    self.assertEqual((command.chat_id,command.thread_id),(-100123,2684))

    def test_username_and_positive_numeric_targets_unchanged(self):
        for target,expected in (('@TeStEr','@tester'),('900123','900123'),('@ALL','@all')):
            self.assertEqual(parse_command(f'/superlook 123:0 {target}').target,expected)

    def test_superchars_and_malformed_targets_scopes_or_extra_arguments_rejected(self):
        for text in ('/superchars 123:0 ALL','/superchars 123:0 all','/superluck 123:0 ALL extra',
                     '/superlook 123 ALL','/superlook 123:abc ALL','/superlook 0:0 ALL'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_command(text)
        for target in ('ALLx','all_','username','0','-1','+1','1.5'):
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'ALL'):
                parse_command(f'/superlook 123:0 {target}')
        self.assertEqual(parse_command('/superchars 123:0').target,'')


class AllSuperadminTests(MiniCase):
    def setUp(self):
        super().setUp();self.sequence=0
        second=create_mini_player(self.world['id'],900124,'@second','Второй',self.db)
        third=create_mini_player(self.world['id'],900125,'@third','Третий',self.db)
        self.pids=(self.pid,second['id'],third['id'])
        other_world=register_mini_world(-100900001,44,'Other',self.db)
        self.other=create_mini_player(other_world,900123,'@tester','Другой мир',self.db)
        sync_shop_catalog(self.world['id'],self.db)

    def command(self,action,tail='',*,key=None,target='ALL'):
        self.sequence+=1
        command=parse_command(f'/{action} {self.world["chat_id"]}:{self.world["thread_id"]} {target} {tail}')
        return service.execute(self.owner,command,operation_key=key or f'all-test:{self.sequence}',db_path=self.db)

    def balances(self,field):
        rows=self.sql(f'SELECT id,{field} FROM mini_players')
        return {row['id']:row[field] for row in rows}

    def own(self,player_id,code='Villager',stars=0):
        hero=self.sql('SELECT id FROM mini_heroes WHERE code=?',(code,))[0]['id']
        self.sql('INSERT OR IGNORE INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,?)',(player_id,hero,stars))
        self.sql('UPDATE mini_players SET active_hero_id=? WHERE id=?',(hero,player_id))
        return hero

    def quantity(self,player_id,code='summon_ticket'):
        rows=self.sql('''SELECT v.quantity FROM mini_inventory v JOIN mini_items i ON i.id=v.item_id
            WHERE v.player_id=? AND i.code=?''',(player_id,code))
        return rows[0]['quantity'] if rows else 0

    def test_all_permissions_and_mini_world_guard_unchanged(self):
        for action,tail in (('superlook',''),('superadd','-c 1'),('superdel','-c 1'),('superluck','')):
            command=parse_command(f'/{action} {self.world["chat_id"]}:{self.world["thread_id"]} ALL {tail}')
            with self.subTest(action=action),self.assertRaises(ValueError):
                service.execute(self.owner+1,command,operation_key='denied',db_path=self.db)
            ordinary=parse_command(f'/{action} -1003376315265:4 ALL {tail}')
            with self.assertRaisesRegex(ValueError,'Mini'):
                service.execute(self.owner,ordinary,operation_key='ordinary',db_path=self.db)
        self.sql('UPDATE mini_worlds SET enabled=0 WHERE id=?',(self.world['id'],))
        with self.assertRaisesRegex(ValueError,'отключён'):self.command('superadd','-c 1')
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),[])

    def test_look_reuses_single_formatter_flags_and_world_scope(self):
        self.command('superadd','-c 12');self.command('superadd','-s 5');self.command('superadd','-p Villager')
        for flags in ('','-c','-c -s','-p','-i'):
            with self.subTest(flags=flags):
                expected='\n\n'.join(self.command('superlook',flags,target=str(user)) for user in (900123,900124,900125))
                with patch.object(service,'_look',wraps=service._look) as formatter:
                    text=self.command('superlook',flags)
                self.assertEqual(formatter.call_count,3);self.assertEqual(text,expected)
                self.assertNotIn('Другой мир',text)
                if flags=='-c -s':self.assertNotIn('Герои:',text);self.assertNotIn('Расходуемые',text)

    def test_empty_world_returns_clear_responses_without_audit(self):
        with connect_mini_db(self.db) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('DELETE FROM mini_players WHERE world_id=?',(self.world['id'],))
        self.assertEqual(self.command('superlook'),'ℹ️ В этом Mini-мире нет игроков.')
        for action,tail in (('superadd','-c 1'),('superdel','-p Villager'),('superluck','')):
            self.assertEqual(self.command(action,tail),'ℹ️ Нет игроков для обработки.')
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),[])
        self.assertEqual(self.balances('coins')[self.other['id']],0)

    def test_coin_add_retry_after_restart_keeps_stable_child_audit_and_wallet_keys(self):
        response=self.command('superadd','-c 100',key='parent')
        self.assertIn('Успешно: 3',response);self.assertIn('Ошибок: 0',response)
        for pid in self.pids:self.assertEqual(self.balances('coins')[pid],100)
        self.assertEqual(self.balances('coins')[self.other['id']],0)
        keys={f'parent:player:{pid}' for pid in self.pids}
        rows=self.sql('SELECT * FROM mini_superadmin_audit')
        self.assertEqual({r['operation_key'] for r in rows},keys)
        self.assertEqual({r['operation_key'] for r in self.sql('SELECT * FROM mini_wallet_transactions')},keys)
        for row in rows:
            self.assertEqual(row['admin_user_id'],self.owner);self.assertEqual(row['action'],'superadd')
            self.assertEqual(row['world_id'],self.world['id']);self.assertEqual(row['resource'],'coins')
            self.assertEqual(row['entity_code'],'100');self.assertEqual(row['amount'],100)
            self.assertEqual(row['target_user_id'],self.sql('SELECT telegram_user_id FROM mini_players WHERE id=?',(row['player_id'],))[0]['telegram_user_id'])
            self.assertTrue(row['created_at'])
        init_mini_db(self.db)
        self.assertIn('Пропущено: 3',self.command('superadd','-c 100',key='parent'))
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),rows)
        for pid in self.pids:self.assertEqual(self.balances('coins')[pid],100)
        self.assertEqual(len(self.sql('SELECT * FROM mini_wallet_transactions')),3)

    def test_coin_delete_error_isolated_and_retry_only_processes_failed_player(self):
        self.sql('UPDATE mini_players SET coins=120 WHERE world_id=?',(self.world['id'],))
        failed=self.pids[1];self.sql('UPDATE mini_players SET coins=0 WHERE id=?',(failed,))
        response=self.command('superdel','-c 100',key='delete')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response);self.assertIn('❌ Второй: Недостаточно монет',response)
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[20,0,20])
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),2)
        self.sql('UPDATE mini_players SET coins=120 WHERE id=?',(failed,))
        response=self.command('superdel','-c 100',key='delete')
        self.assertIn('Успешно: 1',response);self.assertIn('Пропущено: 2',response)
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[20,20,20])
        self.assertEqual(self.balances('coins')[self.other['id']],0)
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),3)

    def test_shard_add_delete_and_insufficient_player_isolation(self):
        self.command('superadd','-s 100',key='shards:add')
        self.command('superadd','-s 100',key='shards:add')
        self.assertEqual([self.balances('shards')[pid] for pid in self.pids],[100,100,100])
        self.sql('UPDATE mini_players SET shards=5 WHERE id=?',(self.pids[1],))
        response=self.command('superdel','-s 100',key='shards:del')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response)
        self.assertEqual([self.balances('shards')[pid] for pid in self.pids],[0,5,0])
        self.assertEqual(self.balances('shards')[self.other['id']],0)

    def test_hero_add_duplicate_is_audited_info_without_copies_shards_or_gacha(self):
        hero=self.own(self.pid,stars=3)
        response=self.command('superadd','-p Villager',key='heroes:add')
        self.assertIn('Успешно: 2',response);self.assertIn('Пропущено: 1',response);self.assertIn('ℹ️ Герой: Герой уже есть.',response)
        self.assertEqual(len(self.sql('SELECT * FROM mini_player_heroes')),3)
        self.assertEqual(self.sql('SELECT copies,stars FROM mini_player_heroes WHERE player_id=?',(self.pid,))[0],dict(copies=1,stars=3))
        self.assertEqual(self.balances('shards')[self.pid],0);self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
        self.assertEqual([self.balances('active_hero_id')[pid] for pid in self.pids],[hero]*3)
        self.assertIsNone(self.balances('active_hero_id')[self.other['id']])
        self.command('superadd','-p Villager',key='heroes:add')
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),3)

    def test_hero_delete_keeps_global_active_valid_and_cascades_tower_and_favorites(self):
        for pid in self.pids:
            fallback=self.own(pid,'CityBlacksmith');hero=self.own(pid)
            select_hero(pid,hero,self.db);add_favorite(pid,hero,self.db)
        response=self.command('superdel','-p Villager')
        self.assertIn('Успешно: 3',response)
        self.assertEqual([self.balances('active_hero_id')[pid] for pid in self.pids],[fallback]*3)
        self.assertEqual(self.sql('SELECT * FROM mini_tower_selections'),[])
        self.assertEqual(self.sql('SELECT * FROM mini_hero_favorites'),[])
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])

    def test_missing_hero_does_not_block_other_players_or_other_world(self):
        for pid in (*self.pids[1:],self.other['id']):hero=self.own(pid)
        response=self.command('superdel','-p Villager')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response)
        self.assertIn('❌ Герой: У игрока нет этого героя.',response)
        self.assertEqual(self.sql('SELECT player_id,hero_id FROM mini_player_heroes'),[dict(player_id=self.other['id'],hero_id=hero)])
        self.assertTrue(all(self.balances('active_hero_id')[pid] is None for pid in self.pids))

    def test_active_tower_hero_rejected_only_for_its_player_and_retry_keeps_snapshot(self):
        for pid in self.pids:hero=self.own(pid)
        select_hero(self.pid,hero,self.db);attempt=start_attempt(self.pid,hero,1,self.db)
        response=self.command('superdel','-p Villager',key='tower:del')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response);self.assertIn('Tower',response)
        self.assertEqual(self.sql('SELECT hero_json,runtime_json FROM mini_tower_attempts')[0],
                         {key:attempt[key] for key in ('hero_json','runtime_json')})
        self.sql("UPDATE mini_tower_attempts SET status='lost' WHERE id=?",(attempt['id'],))
        response=self.command('superdel','-p Villager',key='tower:del')
        self.assertIn('Успешно: 1',response);self.assertIn('Пропущено: 2',response)
        self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])
        self.assertEqual(self.sql('SELECT * FROM mini_tower_selections'),[])
        self.assertEqual(self.sql('SELECT hero_json FROM mini_tower_attempts')[0]['hero_json'],attempt['hero_json'])

    def test_active_boss_restriction_isolated_for_prestart_and_fighting(self):
        from app.mini.boss.schema import init_boss_db
        from app.mini.boss.service import create_boss_event,register_player,close_registration
        from app.mini.boss.combat import start_battle
        from app.mini.boss.catalog import list_boss_templates
        init_boss_db(self.db)
        for pid in self.pids:self.own(pid)
        boss=create_boss_event(self.world['id'],list_boss_templates()[0]['code'],self.owner,self.db)
        register_player(boss['id'],self.pids[1],self.db)
        self.sql('UPDATE mini_bosses SET min_players=1 WHERE id=?',(boss['id'],))
        for status in ('announced','ready','fighting'):
            with self.subTest(status=status):
                for pid in self.pids:self.own(pid)
                if status=='ready':close_registration(boss['id'],self.db)
                elif status=='fighting':start_battle(boss['id'],db_path=self.db)
                response=self.command('superdel','-p Villager')
                self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response);self.assertIn('Boss',response)
                self.assertEqual([r['player_id'] for r in self.sql('SELECT player_id FROM mini_player_heroes')],[self.pids[1]])

    def test_item_add_delete_and_missing_item_failure_isolated(self):
        self.command('superadd','-i summon_ticket',key='item:add')
        self.command('superadd','-i summon_ticket',key='item:add')
        self.assertEqual([self.quantity(pid) for pid in self.pids],[4,4,4])
        self.sql('UPDATE mini_inventory SET quantity=0 WHERE player_id=?',(self.pids[1],))
        response=self.command('superdel','-i summon_ticket')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response)
        self.assertEqual([self.quantity(pid) for pid in self.pids],[3,0,3])
        self.assertEqual(self.quantity(self.other['id']),3)

    def test_nonstackable_item_failure_isolated(self):
        # Exercise the generic nonstackable branch with an existing fixture item.
        self.sql("UPDATE mini_items SET stackable=0 WHERE code='summon_ticket'")
        for pid in self.pids[1:]:self.sql('UPDATE mini_inventory SET quantity=0 WHERE player_id=?',(pid,))
        response=self.command('superadd','-i summon_ticket')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response)
        self.assertIn('Нестакируемый',response)
        self.assertEqual([self.quantity(pid) for pid in self.pids],[3,1,1])
        self.assertEqual(self.quantity(self.other['id']),3)
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),2)

    def test_equipment_uses_existing_add_delete_logic_for_each_player(self):
        self.command('superadd','-i eq_ring_010')
        self.command('superdel','-i eq_ring_010')
        self.assertEqual(self.sql('SELECT * FROM mini_equipment_owned'),[])
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),6)

    def test_luck_all_already_set_is_skipped_audited_and_world_isolated(self):
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)',(self.pid,))
        response=self.command('superluck',key='luck')
        self.assertIn('Успешно: 2',response);self.assertIn('Пропущено: 1',response);self.assertIn('ℹ️ Герой:',response)
        self.assertEqual({r['player_id'] for r in self.sql('SELECT * FROM mini_gacha_guarantees')},set(self.pids))
        rows=self.sql('SELECT * FROM mini_superadmin_audit')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['resource']=='gacha_guarantee' and r['entity_code']=='legendary' and r['amount'] is None for r in rows))
        self.command('superluck',key='luck')
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),rows)

    def test_expected_error_after_writes_rolls_back_only_failed_player_including_audit(self):
        real=service._mutate_player
        def fail_after_write(conn,admin,command,world,player,key):
            result=real(conn,admin,command,world,player,key)
            if player['id']==self.pids[1]:raise ValueError('late expected error')
            return result
        with patch.object(service,'_mutate_player',side_effect=fail_after_write):
            response=self.command('superadd','-c 10',key='late')
        self.assertIn('Успешно: 2',response);self.assertIn('Ошибок: 1',response)
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[10,0,10])
        self.assertEqual(len(self.sql('SELECT * FROM mini_wallet_transactions')),2)
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),2)
        self.command('superadd','-c 10',key='late')
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[10,10,10])
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),3)

    def test_unexpected_sqlite_error_propagates_and_rolls_back_entire_batch(self):
        self.sql(f'''CREATE TRIGGER reject_one_audit BEFORE INSERT ON mini_superadmin_audit
            WHEN NEW.player_id={self.pids[1]} BEGIN SELECT RAISE(ABORT,'unexpected audit failure'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'unexpected audit failure'):
            self.command('superadd','-c 10')
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[0,0,0])
        self.assertEqual(self.sql('SELECT * FROM mini_wallet_transactions'),[])
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),[])

    def test_unexpected_programming_error_not_converted_to_per_player_report(self):
        real=service._mutate_player
        def fail(conn,admin,command,world,player,key):
            if player['id']==self.pids[1]:raise TypeError('programming failure')
            return real(conn,admin,command,world,player,key)
        with patch.object(service,'_mutate_player',side_effect=fail),self.assertRaises(TypeError):
            self.command('superadd','-s 10')
        self.assertEqual([self.balances('shards')[pid] for pid in self.pids],[0,0,0])
        self.assertEqual(self.sql('SELECT * FROM mini_superadmin_audit'),[])

    def test_concurrent_same_parent_operation_is_not_applied_twice(self):
        command=parse_command(f'/superadd {self.world["chat_id"]}:{self.world["thread_id"]} ALL -c 10')
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(service.execute,self.owner,command,operation_key='concurrent',db_path=self.db) for _ in range(2)]
            for job in jobs:self.assertIn('ALL: операция завершена',job.result())
        self.assertEqual([self.balances('coins')[pid] for pid in self.pids],[10,10,10])
        self.assertEqual(len(self.sql('SELECT * FROM mini_superadmin_audit')),3)

    def test_mutations_still_require_stable_parent_key(self):
        for action,tail in (('superadd','-c 1'),('superdel','-s 1'),('superluck','')):
            command=parse_command(f'/{action} {self.world["chat_id"]}:{self.world["thread_id"]} ALL {tail}')
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'operation key'):
                service.execute(self.owner,command,db_path=self.db)


class AllHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_report_uses_existing_telegram_chunking_and_message_key(self):
        response='\n'.join(f'✅ Игрок {i}: coins +100' for i in range(400))
        message=SimpleNamespace(text='/superadd -100123:2684 ALL -c 100',
            from_user=SimpleNamespace(id=777),chat=SimpleNamespace(id=-100999),message_id=42,answer=AsyncMock())
        with patch('app.mini.superadmin.handlers.is_superadmin',return_value=True), \
             patch('app.mini.superadmin.handlers.execute',return_value=response) as execute:
            await superadmin_handler(message)
        self.assertEqual(execute.call_args.args[1].target,'all')
        self.assertEqual(execute.call_args.kwargs['operation_key'],'super:-100999:42')
        chunks=[call.args[0].rstrip() for call in message.answer.call_args_list]
        self.assertGreater(len(chunks),1);self.assertTrue(all(len(chunk)<=3500 for chunk in chunks))
        self.assertEqual('\n'.join(chunks),response)
