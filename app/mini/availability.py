"""Canonical ownership/residency check, shared by Boss, Tower and Duels."""
from app.config import DB_PATH
from app.mini.db import connect_mini_db


def assert_available(conn,player_id,hero_id):
    if not conn.execute('SELECT 1 FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(player_id,hero_id)).fetchone():
        raise ValueError('Этого героя нет в твоей коллекции.')
    if conn.execute('SELECT 1 FROM mini_village_residents WHERE player_id=? AND hero_id=?',(player_id,hero_id)).fetchone():
        raise ValueError('Житель деревни недоступен для боя. Сначала высели его.')


def available_ids(conn,player_id):
    return {r[0] for r in conn.execute('''SELECT ph.hero_id FROM mini_player_heroes ph
        WHERE ph.player_id=? AND NOT EXISTS (SELECT 1 FROM mini_village_residents v
        WHERE v.player_id=ph.player_id AND v.hero_id=ph.hero_id)''',(player_id,))}


def filter_heroes(heroes,player_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn: ids=available_ids(conn,player_id)
    return [h for h in heroes if h['id'] in ids]


def assert_not_committed(conn,player_id,hero_id,*,now):
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'mini_boss_participants' in tables:
        row=conn.execute('''SELECT 1 FROM mini_boss_participants bp JOIN mini_bosses b ON b.id=bp.boss_id
            JOIN mini_players p ON p.id=bp.player_id WHERE bp.player_id=?
            AND ((b.status='fighting' AND bp.hero_id=?) OR (b.status IN ('announced','ready') AND p.active_hero_id=?))''',
            (player_id,hero_id,hero_id)).fetchone()
        if row: raise ValueError('Герой уже участвует в бою с боссом.')
    if conn.execute("SELECT 1 FROM mini_tower_attempts WHERE player_id=? AND hero_id=? AND status='active'",(player_id,hero_id)).fetchone():
        raise ValueError('Герой уже участвует в Испытаниях.')
    if conn.execute('''SELECT 1 FROM mini_duels WHERE status='pending' AND expires_at>?
        AND ((challenger_id=? AND challenger_hero_id=?) OR (defender_id=? AND defender_hero_id=?))''',
        (now,player_id,hero_id,player_id,hero_id)).fetchone():
        raise ValueError('Герой выбран для дуэли.')
