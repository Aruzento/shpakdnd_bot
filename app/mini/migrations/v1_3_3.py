"""Persistent Tower choice, independent of floor and frozen attempts."""

def apply(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_tower_selections (
        player_id INTEGER PRIMARY KEY REFERENCES mini_players(id) ON DELETE CASCADE,
        hero_id INTEGER NOT NULL REFERENCES mini_heroes(id) ON DELETE CASCADE,
        FOREIGN KEY(player_id,hero_id) REFERENCES mini_player_heroes(player_id,hero_id) ON DELETE CASCADE)""")
