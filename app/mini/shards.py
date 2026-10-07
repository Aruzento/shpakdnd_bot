"""Shared shards remain separate from the coin wallet."""
def change_shards_in_transaction(conn, player_id: int, amount: int) -> int:
    if not conn.in_transaction:
        raise ValueError("Shard mutation requires an open transaction.")
    row = conn.execute("SELECT shards FROM mini_players WHERE id=?", (player_id,)).fetchone()
    if row is None:
        raise ValueError("Mini-игрок не найден.")
    balance = int(row[0]) + int(amount)
    if balance < 0:
        raise ValueError("Недостаточно осколков Mini.")
    conn.execute("UPDATE mini_players SET shards=? WHERE id=?", (balance, player_id))
    return balance
