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
