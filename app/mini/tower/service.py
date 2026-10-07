import json
import secrets
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
                "selected_hero":_enrich(repo.selected_hero(conn,player_id)),
                "equipment_bonus":aggregate_in_transaction(conn,player_id)}


def list_heroes(player_id,db_path=DB_PATH):
    # Read-only: catalog synchronization belongs to startup.
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        result=[dict(r) for r in conn.execute("""SELECT h.*,ph.stars FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND h.active=1 ORDER BY h.name""",(player_id,))]
    for hero in result:
        hero["base_attack"]=hero["attack"]
        hero["attack"]=calculate_attack(hero["attack"],hero["stars"])
    return result


def _enrich(hero):
    if hero:
        hero['base_attack']=hero['attack']
        hero['attack']=calculate_attack(hero['attack'],hero['stars'])
    return hero


def select_hero(player_id,hero_id,db_path=DB_PATH,*,expected_floor=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        if expected_floor is not None:
            if expected_floor!=repo.progress(conn,player_id)+1 or repo.active_attempt(conn,player_id):
                raise ValueError('Кнопка выбора героя устарела. Открой текущий бой.')
        row=conn.execute("""SELECT h.*,ph.stars FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=? AND ph.hero_id=? AND h.active=1""",(player_id,hero_id)).fetchone()
        if not row:raise ValueError('Этот герой больше недоступен в коллекции.')
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
            raise ValueError("Эта кнопка этажа устарела. Открой Испытания заново.")
        if repo.active_attempt(conn,player_id):
            raise ValueError("Попытка уже началась. Продолжи текущий бой.")
        if require_selection:
            selected=repo.selected_hero(conn,player_id)
            if not selected or selected['id']!=hero_id:
                raise ValueError('Выбор героя изменился. Открой Испытания заново.')
        row=conn.execute("""SELECT h.*,ph.stars FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=? AND ph.hero_id=? AND (?=0 OR h.active=1)""",
            (player_id,hero_id,int(require_selection))).fetchone()
        if row is None:
            raise ValueError("Этого героя нет в твоей коллекции.")
        hero=dict(row);hero["base_attack"]=hero["attack"]
        hero["attack"]=calculate_attack(hero["attack"],hero["stars"])
        return repo.create_attempt(conn,player_id,hero,get_floor(best+1),aggregate_in_transaction(conn,player_id))


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
    shards=band["shards"]+bonus
    # The unique player/floor ledger is the reward claim. Any failure rolls everything back.
    conn.execute("""INSERT INTO mini_tower_rewards(player_id,floor,attempt_id,shards,equipment_code)
        VALUES(?,?,?,?,?)""",(attempt["player_id"],attempt["floor"],attempt["id"],shards,chest["code"] if chest else None))
    change_shards_in_transaction(conn,attempt["player_id"],shards)
    if chest: grant_in_transaction(conn,attempt["player_id"],chest["code"])
    conn.execute("""INSERT INTO mini_tower_progress(player_id,highest_cleared) VALUES(?,?)
        ON CONFLICT(player_id) DO UPDATE SET highest_cleared=excluded.highest_cleared""",
        (attempt["player_id"],attempt["floor"]))
    return {"shards":shards,"equipment":chest}


def attack(player_id,attempt_id,expected_turn,db_path=DB_PATH,*,roller=None,chooser=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON");conn.execute("BEGIN IMMEDIATE")
        attempt=repo.attempt_by_id(conn,player_id,attempt_id)
        if not attempt or attempt["status"] != "active" or attempt["turn"] != expected_turn:
            raise ValueError("Эта кнопка атаки устарела. Открой текущий бой.")
        if attempt["floor"] != repo.progress(conn,player_id)+1:
            raise ValueError("Некорректный прогресс Испытаний.")
        result=resolve_turn(attempt,roller=roller)
        repo.save_turn(conn,attempt_id,result)
        attempt.update(result)
        reward=grant_reward(conn,attempt,chooser=chooser) if result["status"]=="won" else None
        return {"attempt":attempt,"reward":reward}
