from app.mini.combat.modes import normalized_attack, freeze_hero
from app.mini.editorial import text as editorial_text
import json
import secrets
import time
import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.hero_upgrades import calculate_attack
from app.mini.shards import change_shards_in_transaction
from app.mini.equipment.service import aggregate_in_transaction, grant_in_transaction
from app.mini.tower import repository as repo
from app.mini.tower.catalog import get_floor
from app.mini.tower.balance import reward_band
from app.mini.tower.combat import resolve_turn


def get_state(player_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        best=repo.progress(conn,player_id)
        return {"highest_cleared":best,"floor":get_floor(best+1) if best<200 else None,
                "completed":best==200,"attempt":repo.active_attempt(conn,player_id),
                "selected_hero":_available_selection(conn,player_id),
                "last_attempt":repo.last_lost_attempt(conn,player_id,best+1),
                "equipment_bonus":aggregate_in_transaction(conn,player_id)}


def list_heroes(player_id,db_path=DB_PATH):
    # Read-only: catalog synchronization belongs to startup.
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        result=[dict(r) for r in conn.execute("""SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? ORDER BY h.name""",(player_id,))]
    for hero in result:
        hero["base_attack"]=hero["attack"]
        hero["attack"]=normalized_attack(hero)
    from app.mini.availability import filter_heroes
    return filter_heroes(result, player_id, db_path)


def _enrich(hero):
    if hero:
        hero['base_attack']=hero['attack']
        hero['attack']=normalized_attack(hero)
    return hero


def select_hero(player_id,hero_id,db_path=DB_PATH,*,expected_floor=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        if repo.active_attempt(conn,player_id):raise ValueError(editorial_text('errors.tower.service.select_hero.01'))
        if expected_floor is not None:
            if expected_floor!=repo.progress(conn,player_id)+1 or repo.active_attempt(conn,player_id):
                raise ValueError(editorial_text('errors.tower.service.select_hero.03'))
        row=conn.execute("""SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=? AND ph.hero_id=?""",(player_id,hero_id)).fetchone()
        if not row:raise ValueError(editorial_text('errors.tower.service.select_hero.02'))
        from app.mini.availability import assert_available
        assert_available(conn,player_id,hero_id)
        from app.mini.availability import assert_not_committed
        assert_not_committed(conn,player_id,hero_id,now=int(time.time()))
        repo.save_selection(conn,player_id,hero_id)
        return _enrich(dict(row))


def start_selected_attempt(player_id,expected_floor,expected_hero_id,db_path=DB_PATH):
    return start_attempt(player_id,expected_hero_id,expected_floor,db_path,require_selection=True)


def start_attempt(player_id,hero_id,expected_floor,db_path=DB_PATH,*,require_selection=False):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON");conn.execute("BEGIN IMMEDIATE")
        best=repo.progress(conn,player_id)
        if best==200 or expected_floor != best+1:
            raise ValueError(editorial_text('errors.tower.service.start_attempt.01'))
        if repo.active_attempt(conn,player_id):
            raise ValueError(editorial_text('errors.tower.service.start_attempt.02'))
        if require_selection:
            selected=repo.selected_hero(conn,player_id)
            if not selected or selected['id']!=hero_id:
                raise ValueError(editorial_text('errors.tower.service.start_attempt.04'))
        row=conn.execute("""SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=? AND ph.hero_id=?""",
            (player_id,hero_id)).fetchone()
        if row is None:
            raise ValueError(editorial_text('errors.tower.service.start_attempt.03'))
        from app.mini.availability import assert_available
        assert_available(conn,player_id,hero_id)
        from app.mini.availability import assert_not_committed
        assert_not_committed(conn,player_id,hero_id,now=int(time.time()))
        hero=dict(row);hero["base_attack"]=hero["attack"]
        hero["attack"]=normalized_attack(hero)
        attempt=repo.create_attempt(conn,player_id,hero,get_floor(best+1),aggregate_in_transaction(conn,player_id))
        return _initial_outcome(conn,attempt)


def grant_reward(conn,attempt,*,chooser=None):
    band=reward_band(attempt["floor"])
    chest=None
    if attempt["floor"]%5==0:
        pool=[dict(r) for r in conn.execute("""SELECT e.* FROM mini_equipment e WHERE attack_bonus BETWEEN ? AND ?
            ORDER BY code""",(band["min_bonus"],band["max_bonus"]))]
        if not pool:
            raise ValueError("Каталог экипировки не инициализирован.")
        owned={r[0] for r in conn.execute("SELECT code FROM mini_equipment_owned WHERE player_id=?",(attempt["player_id"],))}
        unseen=[x for x in pool if x["code"] not in owned]
        chest=(chooser or secrets.choice)(unseen or pool)
    bonus=json.loads(attempt["runtime_json"]).get("bonus_shards",0)
    shards=band['shards']+bonus
    values=json.loads(attempt['runtime_json']).get('team',{})
    lunar=values.get('lunar',{});arcana=lunar.get('arcana');mythic_amount=0
    if arcana==2:shards=shards*(300+20*lunar['level'])//100
    if arcana==9:shards=shards//10
    if arcana==10:shards=0;mythic_amount=5
    # The unique player/floor ledger is the reward claim. Any failure rolls everything back.
    conn.execute("""INSERT INTO mini_tower_rewards(player_id,floor,attempt_id,shards,equipment_code)
        VALUES(?,?,?,?,?)""",(attempt["player_id"],attempt["floor"],attempt["id"],shards,chest["code"] if chest else None))
    change_shards_in_transaction(conn,attempt["player_id"],shards)
    if mythic_amount:
        from app.mini.mythic.wallet import change
        change(conn,attempt['player_id'],mythic_amount,'tower_lunar',f"tower:{attempt['floor']}:lunar",{'floor':attempt['floor']})
    if chest: grant_in_transaction(conn,attempt["player_id"],chest["code"])
    from app.mini.combat.arise import sources,award
    runtime=json.loads(attempt['runtime_json'])
    arise=award(conn,'tower',attempt['id'],attempt['player_id'],json.loads(attempt['enemy_json']),runtime.get('hit_count',0)>0,sources(json.loads(attempt['hero_json']),team=values))
    conn.execute("""INSERT INTO mini_tower_progress(player_id,highest_cleared) VALUES(?,?)
        ON CONFLICT(player_id) DO UPDATE SET highest_cleared=excluded.highest_cleared""",
        (attempt["player_id"],attempt["floor"]))
    return {"shards":shards,"mythic_shards":mythic_amount,"equipment":chest,"arise":arise}


def attack(player_id,attempt_id,expected_turn,db_path=DB_PATH,*,roller=None,chooser=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON");conn.execute("BEGIN IMMEDIATE")
        attempt=repo.attempt_by_id(conn,player_id,attempt_id)
        if not attempt or attempt["status"] != "active" or attempt["turn"] != expected_turn:
            raise ValueError(editorial_text('errors.tower.service.attack.01'))
        if attempt["floor"] != repo.progress(conn,player_id)+1:
            raise ValueError(editorial_text('errors.tower.service.attack.02'))
        result=resolve_turn(attempt,roller=roller)
        repo.save_turn(conn,attempt_id,result)
        attempt.update(result)
        reward=grant_reward(conn,attempt,chooser=chooser) if result["status"]=="won" else None
        return {"attempt":attempt,"reward":reward}


def _available_selection(conn,player_id):
    from app.mini.availability import assert_available
    hero=repo.selected_hero(conn,player_id)
    if hero:
        try: assert_available(conn,player_id,hero['id'])
        except ValueError: return None
    return _enrich(hero)


def retry_attempt(player_id,source_attempt_id,db_path=DB_PATH):
    """One persisted retry per lost attempt; concurrent/stale callbacks replay it."""
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT r.new_attempt_id FROM mini_tower_retries r JOIN mini_tower_attempts a ON a.id=r.previous_attempt_id WHERE r.previous_attempt_id=? AND a.player_id=?',
                         (source_attempt_id,player_id)).fetchone()
        if old:return repo.attempt_by_id(conn,player_id,old['new_attempt_id'])
        source=repo.attempt_by_id(conn,player_id,source_attempt_id)
        if not source or source['status']!='lost':raise ValueError(editorial_text('errors.tower.service.retry_attempt.01'))
        if source['floor']!=repo.progress(conn,player_id)+1 or repo.active_attempt(conn,player_id):
            raise ValueError(editorial_text('errors.tower.service.retry_attempt.02'))
        selected=repo.selected_hero(conn,player_id)
        hero_id=selected['id'] if selected else source['hero_id']
        from app.mini.availability import assert_available,assert_not_committed
        assert_available(conn,player_id,hero_id)
        assert_not_committed(conn,player_id,hero_id,now=int(time.time()))
        row=conn.execute('SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND ph.hero_id=?',
                         (player_id,hero_id)).fetchone()
        hero=_enrich(dict(row))
        result=repo.create_attempt(conn,player_id,hero,get_floor(source['floor']),aggregate_in_transaction(conn,player_id))
        conn.execute('INSERT INTO mini_tower_retries(previous_attempt_id,new_attempt_id) VALUES(?,?)',(source_attempt_id,result['id']))
        return _initial_outcome(conn,result)


def _initial_outcome(conn,attempt):
    outcome=json.loads(attempt['runtime_json']).get('team',{}).get('initial_outcome')
    if outcome:
        result=resolve_turn(attempt)
        repo.save_turn(conn,attempt['id'],result);attempt.update(result)
        if outcome=='victory':attempt['initial_reward']=grant_reward(conn,attempt)
    return attempt
