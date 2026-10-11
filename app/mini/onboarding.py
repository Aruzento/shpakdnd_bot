"""Starter inventory and announcement intent share the creation transaction."""
import json
from app.mini.catalog import load_shop_catalog
from app.mini.items import grant_item_in_transaction

STARTER_MESSAGE = "🎟 Стартовый подарок: 3 билета призыва.\n\nИспользуй их в Гаче, чтобы собрать первых героев."


def grant_starter_in_transaction(conn, player_id, world_id, telegram_user_id, character_name):
    if not conn.in_transaction:
        raise ValueError('Стартовая выдача требует транзакции создания персонажа.')
    claimed = conn.execute("""INSERT OR IGNORE INTO mini_onboarding_claims
        (world_id,telegram_user_id,player_id,tickets) VALUES(?,?,?,3)""",
        (world_id,telegram_user_id,player_id)).rowcount
    if not claimed:
        return False
    # The shop may not have been synchronized yet on a fresh database.
    from app.mini.item_registry import sync_in_transaction
    sync_in_transaction(conn)
    grant_item_in_transaction(conn, player_id, 'summon_ticket', 3)
    conn.execute("""INSERT INTO mini_public_notifications
        (event_key,world_id,player_id,kind,payload_json) VALUES(?,?,?,'welcome',?)""",
        (f'welcome:{world_id}:{telegram_user_id}',world_id,player_id,
         json.dumps({'character_name':character_name},ensure_ascii=False)))
    return True
