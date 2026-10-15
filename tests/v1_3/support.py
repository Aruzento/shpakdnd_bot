import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("BOT_TOKEN","test-token")
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.equipment.service import sync_catalog
from app.mini.players import create_mini_player
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics


class MiniCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'mini.db'
        init_mini_db(self.db)
        sync_hero_catalog(self.db)
        sync_catalog(self.db)
        self.world=sync_configured_mini_worlds(self.db)[0]
        self.player=create_mini_player(self.world['id'],900123,'@tester','Герой',self.db)
        self.pid=self.player['id']
        self.owner=777001
        self.access=patch('app.mini.superadmin.access.SUPERADMIN_USER_ID',self.owner)
        self.access.start();self.addCleanup(self.access.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self,sql,args=()):
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(r) for r in conn.execute(sql,args)]

    def hero(self,code='Villager',stars=0):
        hero=self.sql('SELECT * FROM mini_heroes WHERE code=?',(code,))[0]
        self.sql('INSERT OR IGNORE INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,?)',(self.pid,hero['id'],stars))
        self.sql('UPDATE mini_players SET active_hero_id=? WHERE id=?',(hero['id'],self.pid))
        return hero

    def floor(self,best):
        self.sql('INSERT OR REPLACE INTO mini_tower_progress(player_id,highest_cleared) VALUES(?,?)',(self.pid,best))
