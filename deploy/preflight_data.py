"""Read-only source snapshots and strict preservation of existing SQLite data."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path

from deploy_helpers import DeployError, readonly_db, check_database

# Only startup-owned presentation metadata/configuration may change. Economy,
# ownership, offers' price/stock, battle snapshots and arbitrary tables are exact.
SERVICE_METADATA = {
    "mini_worlds": {"name", "enabled"},
    "mini_heroes": {"name", "description", "image_path", "passive_text"},
    "mini_items": {"name", "description"},
    "mini_equipment": {"name"},
    "mini_shop_offers": {"title", "description"},
}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def cell(value):
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    return [type(value).__name__, value]


def row_hash(row):
    return hashlib.sha256(json.dumps([cell(v) for v in row], ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def schema_digest(db):
    with closing(readonly_db(db)) as conn:
        rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def inventory_database(db, *, columns=None, strict=False):
    check_database(db)
    result = {}
    with closing(readonly_db(db)) as conn:
        # The copy is private and quiescent; this read transaction is NOT live DB.
        conn.execute("BEGIN")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            info = conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
            names = [r[1] for r in info]
            selected = (columns or {}).get(table, names)
            if not set(selected) <= set(names):
                raise DeployError(f"Existing columns lost: {table}")
            ignored = set() if strict else SERVICE_METADATA.get(table, set())
            selected = [name for name in selected if name not in ignored]
            if not selected:
                raise DeployError(f"Cannot inventory table without invariant columns: {table}")
            projection = ','.join(quote(name) for name in selected)
            rows = Counter(row_hash(row) for row in conn.execute(f"SELECT {projection} FROM {quote(table)}"))
            result[table] = {"columns": names, "definition": {r[1]: tuple(r[2:]) for r in info},
                             "rows": rows, "count": sum(rows.values())}
    return result


def compare_database(before, after, *, strict=False):
    changes = {"new_tables": sorted(set(after) - set(before)), "new_columns": {}, "tables": {}}
    for table, old in before.items():
        if table not in after:
            raise DeployError(f"Existing table lost: {table}")
        new = after[table]
        for name, definition in old["definition"].items():
            if name not in new["definition"] or new["definition"][name] != definition:
                raise DeployError(f"Existing column changed/lost: {table}.{name}")
        if old["rows"] - new["rows"]:
            raise DeployError(f"Existing user data changed/lost: {table}")
        if (strict or table not in SERVICE_METADATA) and new["count"] != old["count"]:
            raise DeployError(f"Unexpected user rows added: {table}")
        added = sorted(set(new["columns"]) - set(old["columns"]))
        if added:
            changes["new_columns"][table] = added
        changes["tables"][table] = {"before": old["count"], "after": new["count"],
                                     "preserved": True}
    return changes


def compare_copy(before, db, *, strict=False):
    columns = {name: data["columns"] for name, data in before.items()}
    return compare_database(before, inventory_database(db, columns=columns, strict=strict), strict=strict)


def isolated_database(project, expected, forbidden):
    project, expected = Path(project).resolve(), Path(expected)
    if expected.is_symlink() or expected.resolve() != project / "shpakdnd.db":
        raise DeployError("DB_PATH must be the independent checkout's regular shpakdnd.db")
    if not expected.is_file():
        raise DeployError("Migration requires a real database snapshot, not an empty fixture")
    if forbidden:
        live = Path(forbidden).resolve()
        if expected.resolve() == live or expected.samefile(live):
            raise DeployError("Snapshot DB physically aliases production DB")
        if project == live.parent or live.parent in project.parents:
            raise DeployError("Migration checkout is inside production")
    return expected.resolve()
