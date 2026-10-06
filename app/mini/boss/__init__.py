"""Изолированный модуль боссов D&D Mini."""

from app.mini.boss.catalog import (
    get_boss_template,
    list_boss_reward_items,
    list_boss_templates,
    load_boss_catalog,
    load_boss_item_catalog,
    sync_boss_reward_items,
)
from app.mini.boss.combat import (
    BossCombatError,
    BossNotParticipant,
    BossNotYourTurn,
    advance_expired_turns,
    force_finish_battle,
    get_combat_state,
    hit_boss,
    start_battle,
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
    select_battle_hero,
)

__all__ = [
    "BossCombatError",
    "BossError",
    "BossNotParticipant",
    "BossNotYourTurn",
    "advance_expired_turns",
    "cancel_boss",
    "close_registration",
    "create_boss_event",
    "force_finish_battle",
    "get_active_boss",
    "get_boss",
    "get_boss_template",
    "get_combat_state",
    "hit_boss",
    "init_boss_db",
    "list_boss_reward_items",
    "list_boss_templates",
    "list_participants",
    "load_boss_catalog",
    "load_boss_item_catalog",
    "register_player",
    "select_battle_hero",
    "reopen_registration",
    "start_battle",
    "sync_boss_reward_items",
    "unregister_player",
]
