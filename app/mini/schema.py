from app.mini.migrations import migrate
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


MINI_TABLES = (
    "mini_tower_selections",
    "mini_hero_favorites",
    "mini_onboarding_claims",
    "mini_titles",
    "mini_public_notifications",
    "mini_equipment",
    "mini_equipment_owned",
    "mini_equipment_slots",
    "mini_tower_progress",
    "mini_tower_attempts",
    "mini_tower_rewards",
    "mini_gacha_guarantees",
    "mini_superadmin_audit",
    "mini_worlds",
    "mini_players",
    "mini_heroes",
    "mini_player_heroes",
    "mini_wallet_transactions",
    "mini_daily_claims",
    "mini_items",
    "mini_inventory",
    "mini_player_effects",
    "mini_item_uses",
    "mini_shop_offers",
    "mini_purchases",
    "mini_gacha_pulls",
    "mini_event_sessions",
    "mini_event_requests",
)


def init_mini_db(db_path: str | Path = DB_PATH) -> None:
    """Create or upgrade Mini in one transaction, including SQLite DDL."""
    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        migrate(conn)
