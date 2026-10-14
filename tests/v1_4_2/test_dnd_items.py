import tempfile
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from app.db import schema,inventory
from app.db.dnd_items import create_currency,parse_currency_command,denomination,migrate


class DndItemsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'old.db'
        for module in (schema,inventory):
            p=patch.object(module,'DB_PATH',self.db);p.start();self.addCleanup(p.stop)
        p=patch('app.db.dnd_items.TOPIC_SETTINGS',{-111:{0:{},2:{},3:{'mini':True}}});p.start();self.addCleanup(p.stop)
        schema.init_db()
    def sql(self,s,args=()):
        with closing(sqlite3.connect(self.db)) as conn,conn:return conn.execute(s,args).fetchall()
    def create(self,name='Золотая монета',qty=1,value='1',base=True,thread=0):
        return create_currency(-111,thread,name,qty,value,is_base=base,db_path=self.db)

    def test_currency_issue_delete_clean_scope_and_restart(self):
        self.create()
        for name,value in (('Медная монета','0.01'),('Серебряная монета','0.1'),('Платиновая монета','10')):
            self.create(name,1,value,False);inventory.add_inventory_item(-111,0,'@player',name,50)
        inventory.add_inventory_item(-111,0,'@player','Золотая монета',15)
        self.assertEqual(len(inventory.get_inventory(-111,0,'@player')),4)
        self.assertTrue(all(r[0] is not None for r in self.sql('SELECT item_id FROM inventory')))
        self.assertEqual(inventory.delete_inventory_item(-111,0,'@player','Золотая монета',3)[2],12)
        self.create(thread=2);inventory.add_inventory_item(-111,2,'@player','Золотая монета',7)
        schema.init_db();inventory.clean_inventory(-111,0,'@player')
        self.assertEqual(inventory.get_inventory(-111,2,'@player')[0][1],7)
        inventory.clean_all_inventory(-111,2);self.assertEqual(self.sql('SELECT * FROM inventory'),[])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_items')[0][0],5)

    def test_free_item_registration_and_ambiguous_legacy_rows_unchanged(self):
        for name in ('Меч','меч'):
            self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description) VALUES(-111,0,'@player',?,3,'История')",(name,))
        before=self.sql('SELECT * FROM inventory');schema.init_db();schema.init_db()
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertTrue(all(row[-1] is None for row in before))
        inventory.add_inventory_item(-111,0,'@player','Верёвка',2,'Длинная')
        inventory.add_inventory_item(-111,0,'@player','верёвка',1)
        self.assertEqual(inventory.get_inventory(-111,0,'@player')[-1],('Верёвка',3,'Длинная'))
        self.assertEqual(self.sql('SELECT category FROM dnd_items'),[('item',)])

    def test_exact_decimal_and_repeating_ratio(self):
        self.create();self.create('Треть',3,'1',False)
        self.assertEqual(denomination(2,'1'),'0.5');self.assertEqual(denomination(100,'0.1'),'0.001')
        self.assertEqual(self.sql("SELECT base_value,currency_quantity FROM dnd_currencies c JOIN dnd_items i ON i.id=c.item_id WHERE i.name='Треть'"),[('1',3)])

    def test_invalid_values_names_mini_and_duplicate_base_no_partial_records(self):
        self.create()
        invalid=[('',1,'1',False,0),('золотая монета',1,'1',False,0),('Вторая',1,'1',True,0),('Mini',1,'1',True,3)]
        invalid += [('Ошибка',1,x,False,0) for x in ('0','-1','NaN','Infinity','1e999')]
        invalid += [('Ошибка',x,'1',False,0) for x in (0,-1,True,1.5,2**63)]
        for args in invalid:
            with self.subTest(args=args),self.assertRaises(ValueError):self.create(*args)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_items')[0][0],1)

    def test_secondary_requires_base_and_concurrent_base_is_single(self):
        with self.assertRaises(ValueError):self.create('Серебро',1,'0.1',False)
        def attempt(_):
            try:return self.create()
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_constraints_and_foreign_theme_inventory(self):
        item=self.create()['item_id']
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,item_id) VALUES(-111,2,'@p','Монета',?)",(item,))
        with self.assertRaises(sqlite3.IntegrityError):self.sql("UPDATE dnd_currencies SET base_value='NaN'")
        with self.assertRaises(sqlite3.IntegrityError):self.sql("UPDATE dnd_items SET category='item'")
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])

    def test_concurrent_currency_deletion_and_invalid_quantities(self):
        self.create();inventory.add_inventory_item(-111,0,'@player','Золотая монета',3)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:inventory.delete_inventory_item(-111,0,'@player','Золотая монета',1),range(3)))
        self.assertEqual(sorted(r[2] for r in results),[0,1,2])
        self.assertEqual(inventory.get_inventory(-111,0,'@player'),[])
        for qty in (0,-1,True,1.5):
            for fn in (inventory.add_inventory_item,inventory.delete_inventory_item):
                with self.assertRaises(ValueError):fn(-111,0,'@player','Золотая монета',qty)

    def test_fault_rolls_back_item_definition_and_currency(self):
        self.sql("CREATE TRIGGER fault BEFORE INSERT ON dnd_currencies BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.create()
        self.assertEqual(self.sql('SELECT * FROM dnd_items'),[])

    def test_old_schema_backup_migrated_without_merging(self):
        old=self.db.with_name('legacy.db');target=self.db.with_name('copy.db')
        with closing(sqlite3.connect(old)) as conn,conn:
            schema.create_inventory_table(conn)
            for name in ('Меч','меч'):
                conn.execute("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description) VALUES(-111,0,'@p',?,3,'Старое')",(name,))
            before=conn.execute('SELECT * FROM inventory').fetchall()
        with closing(sqlite3.connect(old)) as source,closing(sqlite3.connect(target)) as copied:source.backup(copied)
        with closing(sqlite3.connect(target)) as conn,conn:
            for _ in range(3):migrate(conn)
            self.assertEqual(conn.execute('SELECT id,chat_id,thread_id,username,name,quantity,description FROM inventory').fetchall(),before)
            self.assertEqual(conn.execute('SELECT item_id FROM inventory').fetchall(),[(None,),(None,)])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_additive_ddl_failure_rolls_back_all_new_dnd_state(self):
        target=self.db.with_name('migration-fault.db')
        with closing(sqlite3.connect(target)) as conn:
            schema.create_inventory_table(conn);conn.commit()
            conn.set_authorizer(lambda action,*_:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError),conn:migrate(conn)
            conn.set_authorizer(None)
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'dnd_%'").fetchall(),[])
            self.assertNotIn('item_id',{r[1] for r in conn.execute('PRAGMA table_info(inventory)')})

    def test_parser_quotes_bot_suffix_and_rejected_values(self):
        self.assertEqual(parse_currency_command('/admcoin@bot -111:0 "Золотая монета" 1'),('-111:0','Золотая монета',1,'1'))
        self.assertEqual(parse_currency_command('/admscoin -111 "Медь" 100 = 1',secondary=True),('-111','Медь',100,'1'))
        for raw in ('/admcoin -111 "" 1','/admcoin -111 "Медь 1','/admcoin -111 Медь 0','/admcoin -111 Медь 2'):
            with self.assertRaises(ValueError):parse_currency_command(raw)
        for value in ('NaN','Infinity','-0.1','0','1e999','1,5'):
            with self.assertRaises(ValueError):parse_currency_command(f'/admscoin -111 Медь 1 = {value}',secondary=True)


class CurrencyPermissionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_admin_guard_precedes_any_writes(self):
        from app.handlers import admin
        message=SimpleNamespace(text='/admcoin -111:3 "Золото" 1',answer=AsyncMock())
        with patch.object(admin,'check_global_admin_permission',AsyncMock(return_value=False)),patch('app.db.dnd_items.create_currency') as create:
            await admin.currency_handler(message)
        create.assert_not_called()

    async def test_mini_and_invalid_scope_denied(self):
        from app.handlers import admin
        for scope in (None,(-111,3)):
            message=SimpleNamespace(text='/admcoin -111:3 "Золото" 1',answer=AsyncMock())
            with patch.object(admin,'check_global_admin_permission',AsyncMock(return_value=True)),patch.object(admin,'parse_target_scope',return_value=scope),patch.object(admin,'allow_dnd',AsyncMock(return_value=False)),patch('app.db.dnd_items.create_currency') as create:
                await admin.currency_handler(message)
            create.assert_not_called()
