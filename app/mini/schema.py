from app.mini.migrations import migrate
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


MINI_TABLES = (
    "mini_event_pvp_shard_awards", "mini_event_pvp_control",
    "mini_event_pvp_members", "mini_event_pvp",
    "mini_registration_sessions", "mini_public_shares",
    "mini_gacha_batches", "mini_gacha_batch_pulls", "mini_registration_requests",
    "mini_ui_navigation", "mini_ui_publications", "mini_tower_retries",
    "mini_gacha_pity",
    "mini_villages",
    "mini_village_residents",
    "mini_village_boosts",
    "mini_village_operations",
    "mini_daily_streak",
    "mini_daily_chests",
    "mini_duels",
    "mini_duel_locks",
    "mini_shadow_rolls",
    "mini_mythic_fragments",
    "mini_mythic_grants",

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
