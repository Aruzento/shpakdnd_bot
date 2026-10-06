from collections.abc import Callable, Mapping
from dataclasses import dataclass
import sqlite3
from typing import Any


class ItemUseError(ValueError):
    pass


@dataclass(frozen=True)
class ItemEffectContext:
    """A handler shares the item's transaction and updates its result.

    Persistence operations are supplied by the service. Handlers never open,
    commit or roll back a connection, nor consume inventory independently.
    """
    conn: sqlite3.Connection
    player_id: int
    item_id: int
    row: Mapping[str, Any]
    result: dict
    operation_key: str
    amount_picker: Callable[[int, int], int]
    add_charge: Callable[..., int]
    change_balance: Callable[..., dict]


@dataclass(frozen=True)
class EffectDefinition:
    key: str
    description: str
    handler: Callable[[ItemEffectContext], None] | None = None
    active_title: str = ""
