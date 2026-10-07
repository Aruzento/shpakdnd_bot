"""Ordered, idempotent schema steps; transaction ownership stays with the caller.

No version ledger is needed for these guarded CREATE/ALTER steps. Append new
feature modules without changing historical data conversions or their order.
"""
from app.mini.migrations.core import apply as core_schema
from app.mini.migrations.inventory import apply as inventory_schema
from app.mini.migrations.activities import apply as activities_schema
from app.mini.migrations.legacy import apply as legacy_schema

from app.mini.migrations.v1_3 import apply as v1_3_schema

from app.mini.migrations.v1_3_1 import apply as v1_3_1_schema

from app.mini.migrations.v1_3_2 import apply as v1_3_2_schema

from app.mini.migrations.v1_3_3 import apply as v1_3_3_schema

MIGRATIONS = (core_schema, inventory_schema, activities_schema, legacy_schema, v1_3_schema, v1_3_1_schema, v1_3_2_schema, v1_3_3_schema)


def migrate(conn) -> None:
    for apply in MIGRATIONS:
        apply(conn)
