"""Migration entrypoints against real game schema and persistent feature states."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from tests.v1_4_1.test_preflight import pref, data, backup_database, ROOT, NETWORK_GUARD
from tests.v1_4_1 import test_preflight as preflight_tests
from app.mini.boss.schema import init_boss_db
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.service import create_boss_event, register_player, close_registration
from app.mini.boss.combat import start_battle
from app.mini.equipment.service import grant_in_transaction, equip
from app.mini.db import connect_mini_db
from app.mini.players import create_mini_player
from app.mini.wallet import add_coins
from app.mini.events import service as events
from app.mini.duels import service as duels
from app.mini.village import service as village


class RealMigrationSnapshotTests(MiniCase):
    def seed_states(self):
        init_boss_db(self.db)
        hero = self.hero()
        self.floor(17)
        add_coins(self.pid, 1000, 'Snapshot fixture', db_path=self.db)
        players = [self.pid]
        for index in range(4):
            pid = create_mini_player(self.world['id'], 7000+index, '@fixture', 'Snapshot', self.db)['id']
            add_coins(pid, 100, 'Snapshot fixture', db_path=self.db)
            self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)', (pid,hero['id']))
            players.append(pid)
        resolved = events.start_session(self.pid,self.world['id'],'rps',5,'resolved-rps',self.db)
        with patch.object(events.secrets,'choice',return_value='demon'):
            events.resolve_rps(self.pid,self.world['id'],resolved['id'],'saint',self.db)
        resolved = events.start_session(self.pid,self.world['id'],'labyrinth',5,'resolved-labyrinth',self.db)
        wrong = 'right' if resolved['payload']['sequence'][0]=='left' else 'left'
        events.choose_direction(self.pid,self.world['id'],resolved['id'],0,wrong,self.db)
        events.start_session(players[1],self.world['id'],'rps',5,'active-rps',self.db)
        events.start_session(players[2],self.world['id'],'labyrinth',5,'active-labyrinth',self.db)
        village.get_state(players[2],self.db,now=1000)
        duels.challenge(players[3],players[4],hero['id'],'pending-duel',self.db,now=1000)
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('BEGIN IMMEDIATE'); grant_in_transaction(conn,self.pid,'eq_ring_042')
        equip(self.pid,'eq_ring_042',self.db)
        template=get_boss_template('simple_village_guy');template.update(min_players=1,max_hp=1000)
        with patch('app.mini.boss.service.get_boss_template',return_value=template):
            boss=create_boss_event(self.world['id'],'simple_village_guy',1,self.db)
        register_player(boss['id'],self.pid,self.db);close_registration(boss['id'],self.db)
        start_battle(boss['id'],db_path=self.db)
        self.sql('INSERT INTO mini_mythic_fragments VALUES(?,?,?)',(self.pid,'preserved-legacy-mythic',15))
        self.sql('INSERT INTO mini_mythic_grants VALUES(?,?,?,?,?)',(self.pid,'grant-1','preserved-legacy-mythic',15,'fixture'))
        self.sql('INSERT INTO mini_shadow_rolls VALUES(?,?,?,?,?,?)',(boss['id'],self.pid,hero['id'],50,'preserved-shadow',0))

    def checkout(self, name):
        project=Path(self.tmp.name)/name;project.mkdir()
        shutil.copytree(ROOT/'app',project/'app',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT/'check_bot.py',project/'check_bot.py')
        (project/'.gitignore').write_text('*.db*\n__pycache__/\n')
        for args in [('init','-q'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),
                     ('config','commit.gpgsign','false'),('add','.'),('commit','-qm','Isolated real sources')]:
            subprocess.run(['git','-C',str(project),*args],check=True,capture_output=True)
        sha=subprocess.check_output(['git','-C',str(project),'rev-parse','HEAD'],text=True).strip()
        return project,sha

    def probe(self, project, sha):
        network=Path(self.tmp.name)/'network';network.mkdir(exist_ok=True)
        (network/'sitecustomize.py').write_text(NETWORK_GUARD)
        env=pref.safe_environment(Path(self.tmp.name),network=network,project=project)
        result=subprocess.run([sys.executable,str(ROOT/'deploy/release_preflight.py'),'runtime','--project',str(project),
                               '--db',str(project/'shpakdnd.db'),'--forbid-db',str(self.db),'--sha',sha],
                              cwd=project,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_real_schema_events_boss_equipment_wallet_duels_village_shadow_mythic_survive(self):
        self.seed_states()
        live=Path(self.tmp.name)/'live';live.mkdir()
        destination=live/'shpakdnd.db';self.db.replace(destination);self.db=destination
        before_live=self.db.read_bytes()
        target,sha=self.checkout('target')
        copy=target/'shpakdnd.db';backup_database(self.db,copy)
        before=data.inventory_database(copy)
        self.probe(target,sha)
        result=data.compare_copy(before,copy)
        for table in ('mini_players','mini_event_sessions','mini_wallet_transactions','mini_bosses',
                      'mini_boss_participants','mini_equipment_owned','mini_equipment_slots','mini_tower_progress',
                      'mini_duels','mini_villages','mini_mythic_fragments','mini_mythic_grants','mini_shadow_rolls'):
            self.assertGreater(before[table]['count'],0,table)
            self.assertTrue(result['tables'][table]['preserved'])
        migrated=data.inventory_database(copy,strict=True)
        self.probe(target,sha);data.compare_copy(migrated,copy,strict=True)
        old,old_sha=self.checkout('old');backup_database(copy,old/'shpakdnd.db')
        self.probe(old,old_sha);data.compare_copy(migrated,old/'shpakdnd.db',strict=True)
        self.assertEqual(self.db.read_bytes(),before_live)

    def test_confirmed_distinct_schemas_preserve_real_game_data_and_allow_old_code(self):
        self.seed_states()
        live=Path(self.tmp.name)/'live-distinct';live.mkdir()
        destination=live/'shpakdnd.db';self.db.replace(destination);self.db=destination
        fixture=preflight_tests.DeploymentStateTests(methodName='test_success_durably_records_history_and_compatible_rollback')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.db.unlink();fixture.backup.unlink()
        backup_database(self.db,fixture.db);backup_database(self.db,fixture.backup)
        target=fixture.root/'different-game-target.db';backup_database(self.db,target)
        fixture.add_target(target)
        fixture.report.update(source_schema=data.schema_digest(fixture.db),target_schema=data.schema_digest(target))
        pref.save_evidence(fixture.evidence,fixture.report)
        self.assertNotEqual(fixture.report['source_schema'],fixture.report['target_schema'])
        before=data.inventory_database(fixture.db,strict=True)
        fixture.init();fixture.verify(expected_schema='source')
        fixture.migrate();fixture.verify(expected_schema='target');fixture.verify(rollback=True)
        data.compare_copy(before,fixture.db,strict=True)
        for table in ('mini_event_sessions','mini_bosses','mini_wallet_transactions','mini_tower_progress',
                      'mini_equipment_slots','mini_duels','mini_villages','mini_mythic_fragments','mini_shadow_rolls'):
            self.assertGreater(before[table]['count'],0,table)
        old_before=data.inventory_database(fixture.db)
        old,sha=self.checkout('old-compatible-distinct')
        backup_database(fixture.db,old/'shpakdnd.db')
        self.probe(old,sha)
        data.compare_copy(old_before,old/'shpakdnd.db')
