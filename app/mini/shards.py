"""Shared shards remain separate from the coin wallet."""
from app.mini.editorial import text as editorial_text
def change_shards_in_transaction(conn, player_id: int, amount: int) -> int:
    if not conn.in_transaction:
        raise ValueError("Shard mutation requires an open transaction.")
    row = conn.execute("SELECT shards FROM mini_players WHERE id=?", (player_id,)).fetchone()
    if row is None:
        raise ValueError(editorial_text('errors.shards.change_shards_in_transaction.01'))
    balance = int(row[0]) + int(amount)
    if balance < 0:
        raise ValueError(editorial_text('errors.shards.change_shards_in_transaction.02'))
    conn.execute("UPDATE mini_players SET shards=? WHERE id=?", (balance, player_id))
    return balance
