"""Ordered, idempotent schema steps; transaction ownership stays with the caller.

No version ledger is needed for these guarded CREATE/ALTER steps. Append new
feature modules without changing historical data conversions or their order.
"""
from app.mini.migrations import core, inventory, activities, legacy

MIGRATIONS = (core.apply, inventory.apply, activities.apply, legacy.apply)


def migrate(conn) -> None:
    for apply in MIGRATIONS:
        apply(conn)
