"""Изолированный модуль боссов D&D Mini."""

from app.mini.boss.catalog import (
    get_boss_template,
    list_boss_templates,
    load_boss_catalog,
)
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    BossError,
    cancel_boss,
    close_registration,
    create_boss_event,
    get_active_boss,
    get_boss,
    list_participants,
    register_player,
    reopen_registration,
    unregister_player,
)

__all__ = [
    "BossError",
    "cancel_boss",
    "close_registration",
    "create_boss_event",
    "get_active_boss",
    "get_boss",
    "get_boss_template",
    "init_boss_db",
    "list_boss_templates",
    "list_participants",
    "load_boss_catalog",
    "register_player",
    "reopen_registration",
    "unregister_player",
]
