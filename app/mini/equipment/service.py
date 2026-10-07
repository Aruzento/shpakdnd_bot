import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.equipment.catalog import SLOTS, load_catalog


def sync_catalog(db_path=DB_PATH):
    items = load_catalog()["items"]
    with connect_mini_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in items:
            conn.execute("""INSERT INTO mini_equipment(code,name,slot,attack_bonus) VALUES(?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET name=excluded.name,
                    slot=excluded.slot, attack_bonus=excluded.attack_bonus""",
                (item["code"],item["name"],item["slot"],item["attack_bonus"]))
    return len(items)


def aggregate_in_transaction(conn, player_id):
    return int(conn.execute("""SELECT COALESCE(SUM(e.attack_bonus),0)
        FROM mini_equipment_slots s JOIN mini_equipment e ON e.code=s.code
        JOIN mini_equipment_owned o ON o.player_id=s.player_id AND o.code=s.code
        WHERE s.player_id=?""", (player_id,)).fetchone()[0])


def grant_in_transaction(conn, player_id, code):
    if not conn.in_transaction:
        raise ValueError("Equipment mutation requires a transaction.")
    item = conn.execute("SELECT * FROM mini_equipment WHERE code=?", (code,)).fetchone()
    if item is None:
        raise ValueError("Экипировка не найдена.")
    conn.execute("""INSERT INTO mini_equipment_owned(player_id,code,quantity) VALUES(?,?,1)
        ON CONFLICT(player_id,code) DO UPDATE SET quantity=quantity+1""", (player_id,code))
    return dict(item)


def remove_in_transaction(conn, player_id, code):
    if not conn.in_transaction:
        raise ValueError("Equipment mutation requires a transaction.")
    row = conn.execute("SELECT quantity FROM mini_equipment_owned WHERE player_id=? AND code=?",
                       (player_id,code)).fetchone()
    if row is None:
        raise ValueError("Этого предмета нет у игрока.")
    if row[0] == 1:
        # Explicit removal also works on databases opened without foreign_keys by legacy callers.
        conn.execute("DELETE FROM mini_equipment_slots WHERE player_id=? AND code=?", (player_id,code))
        conn.execute("DELETE FROM mini_equipment_owned WHERE player_id=? AND code=?", (player_id,code))
    else:
        conn.execute("UPDATE mini_equipment_owned SET quantity=quantity-1 WHERE player_id=? AND code=?",
                     (player_id,code))


def get_equipment(player_id, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        owned = [dict(r) for r in conn.execute("""SELECT e.*,o.quantity FROM mini_equipment_owned o
            JOIN mini_equipment e ON e.code=o.code WHERE o.player_id=? ORDER BY e.slot,e.attack_bonus DESC""",
            (player_id,))]
        equipped = {r["slot"]: dict(r) for r in conn.execute("""SELECT e.* FROM mini_equipment_slots s
            JOIN mini_equipment e ON e.code=s.code WHERE s.player_id=?""", (player_id,))}
        return {"owned":owned, "equipped":equipped, "attack_bonus":aggregate_in_transaction(conn,player_id)}


def equip(player_id, code, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("BEGIN IMMEDIATE")
        item = conn.execute("""SELECT e.* FROM mini_equipment_owned o JOIN mini_equipment e ON e.code=o.code
            WHERE o.player_id=? AND o.code=?""", (player_id,code)).fetchone()
        if item is None:
            raise ValueError("Этого предмета нет у тебя.")
        conn.execute("""INSERT INTO mini_equipment_slots(player_id,slot,code) VALUES(?,?,?)
            ON CONFLICT(player_id,slot) DO UPDATE SET code=excluded.code""", (player_id,item["slot"],code))
    return get_equipment(player_id,db_path)


def unequip(player_id, slot, db_path=DB_PATH):
    if slot not in SLOTS:
        raise ValueError("Неизвестный слот.")
    with connect_mini_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM mini_equipment_slots WHERE player_id=? AND slot=?", (player_id,slot))
    return get_equipment(player_id,db_path)
