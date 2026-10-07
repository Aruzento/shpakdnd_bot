"""Tower persistence helpers; never open/commit a transaction."""
import json


def progress(conn,player_id):
    row=conn.execute("SELECT highest_cleared FROM mini_tower_progress WHERE player_id=?",(player_id,)).fetchone()
    return int(row[0]) if row else 0


def active_attempt(conn,player_id):
    row=conn.execute("SELECT * FROM mini_tower_attempts WHERE player_id=? AND status='active'",(player_id,)).fetchone()
    return dict(row) if row else None


def attempt_by_id(conn,player_id,attempt_id):
    row=conn.execute("SELECT * FROM mini_tower_attempts WHERE player_id=? AND id=?",(player_id,attempt_id)).fetchone()
    return dict(row) if row else None


def create_attempt(conn,player_id,hero,enemy,bonus):
    # Armored/undead reward buffers become one guard charge, never a fourth shield.
    guard = 1 if hero.get("special_trait") in {"armored","undead"} else 0
    cursor=conn.execute("""INSERT INTO mini_tower_attempts
        (player_id,floor,hero_id,hero_json,enemy_json,equipment_bonus,current_hp,runtime_json)
        VALUES(?,?,?,?,?,?,?,?)""",(player_id,enemy["floor"],hero["id"],json.dumps(hero),
        json.dumps(enemy),bonus,enemy["max_hp"],json.dumps({"class_rules_version":1,"hero":{"reward_guard_charges":guard}})))
    return attempt_by_id(conn,player_id,cursor.lastrowid)


def save_turn(conn,attempt_id,result):
    conn.execute("""UPDATE mini_tower_attempts SET current_hp=?,shields=?,turn=?,status=?,runtime_json=?,events_json=?
        WHERE id=?""",tuple(result[k] for k in ("current_hp","shields","turn","status","runtime_json","events_json"))+(attempt_id,))


def selected_hero(conn,player_id):
    row=conn.execute("""SELECT h.*,ph.stars FROM mini_tower_selections s
        JOIN mini_player_heroes ph ON ph.player_id=s.player_id AND ph.hero_id=s.hero_id
        JOIN mini_heroes h ON h.id=s.hero_id WHERE s.player_id=?""",(player_id,)).fetchone()
    if row is None:
        # Also repairs legacy DBs where foreign keys were disabled during deletion.
        conn.execute("""DELETE FROM mini_tower_selections WHERE player_id=? AND NOT EXISTS (
            SELECT 1 FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=mini_tower_selections.player_id
            AND ph.hero_id=mini_tower_selections.hero_id)""",(player_id,))
    return dict(row) if row else None


def save_selection(conn,player_id,hero_id):
    conn.execute("""INSERT INTO mini_tower_selections(player_id,hero_id) VALUES(?,?)
        ON CONFLICT(player_id) DO UPDATE SET hero_id=excluded.hero_id""",(player_id,hero_id))
