"""Изолированный модуль D&D Mini."""

from app.mini.catalog import load_hero_catalog, load_shop_catalog, validate_content
from app.mini.daily import claim_daily, get_daily_claim
from app.mini.heroes import sync_hero_catalog
from app.mini.shop import purchase_offer, sync_shop_catalog
from app.mini.players import (
    create_mini_player,
    get_mini_player,
    touch_mini_player,
)
from app.mini.schema import init_mini_db
from app.mini.wallet import (
    InsufficientFundsError,
    add_coins,
    get_balance,
    get_wallet_history,
    spend_coins,
)
from app.mini.worlds import (
    get_mini_world,
    is_mini_world,
)

__all__ = [
    "load_hero_catalog",
    "load_shop_catalog",
    "validate_content",
    "sync_hero_catalog",
    "sync_shop_catalog",
    "purchase_offer",
    "claim_daily",
    "get_daily_claim",
    "create_mini_player",
    "get_mini_player",
    "get_balance",
    "get_wallet_history",
    "add_coins",
    "spend_coins",
    "InsufficientFundsError",
    "get_mini_world",
    "init_mini_db",
    "is_mini_world",
    "touch_mini_player",
]
