"""O06 independent, durable ordinary-shard grants in the caller's transaction."""

import json
from app.mini.combat.hero_abilities.engine import _roll_success


def award(conn, mode, battle_id, pid, stars, policy, *, roller=None):
    if not conn.in_transaction:
        raise RuntimeError("Star reward needs a transaction")
    prior = conn.execute(
        "SELECT result_json FROM mini_star_rewards WHERE mode=? AND battle_id=? AND player_id=?",
        (mode, battle_id, pid),
    ).fetchone()
    if prior:
        return json.loads(prior[0])
    chance = policy["star_chance"][max(0, min(5, stars - 1))]
    success = bool((roller or _roll_success)(chance))
    amount = policy["star_shards"] if success else 0
    result = {
        "player_id": pid,
        "source_stars": stars,
        "chance": chance,
        "success": success,
        "amount": amount,
    }
    if amount:
        balance = (
            conn.execute(
                "SELECT shards FROM mini_players WHERE id=?", (pid,)
            ).fetchone()[0]
            + amount
        )
        if not 0 <= balance <= 2**63 - 1:
            raise ValueError("Ordinary shards exceed SQLite integer")
        conn.execute("UPDATE mini_players SET shards=? WHERE id=?", (balance, pid))
    conn.execute(
        "INSERT INTO mini_star_rewards VALUES(?,?,?,?,?,?,?,?)",
        (mode, battle_id, pid, chance, int(success), amount, stars, json.dumps(result)),
    )
    return result
