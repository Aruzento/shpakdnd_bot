"""Owner-scoped, read-only card context; eviction stays in Village.mutate."""
import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db

def with_village(hero,player_id,db_path=DB_PATH):
    if hero is None:return None
    result=dict(hero)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        row=conn.execute("""SELECT r.hero_id,r.building,v.revision FROM mini_village_residents r
            JOIN mini_villages v ON v.player_id=r.player_id
            JOIN mini_player_heroes ph ON ph.player_id=r.player_id AND ph.hero_id=r.hero_id
            WHERE r.player_id=? AND r.hero_id=?""",(player_id,hero["id"])).fetchone()
    result["village_resident"]=dict(row) if row else None
    return result
