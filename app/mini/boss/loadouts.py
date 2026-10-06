from app.mini.combat.tags import LEGACY_HERO_TRAITS as LEGACY_TRAITS
"""Immutable battle loadouts, including safe pre-v2 defaults."""
import json


def decode_loadout(raw: str) -> dict:
    snapshot = json.loads(raw or "{}")
    if not isinstance(snapshot, dict):
        raise ValueError("Hero battle snapshot must be an object.")
    return snapshot


def freeze_legacy_loadouts(conn) -> None:
    """Fill missing loadout fields once, before the catalog is synced.

    Empty pre-v2 traits always keep the original placeholders. A v2 snapshot's
    existing traits are preserved. Passive keys did not previously have a
    snapshot, so capture the currently installed value once.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_boss_participants'"
    ).fetchone()
    heroes_exist = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_heroes'"
    ).fetchone()
    if exists is None or heroes_exist is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(mini_boss_participants)")}
    if "hero_snapshot_json" not in columns:
        return
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        """SELECT bp.boss_id, bp.player_id, bp.hero_snapshot_json,
                  h.passive_key, h.passive_text
           FROM mini_boss_participants bp
           JOIN mini_bosses b ON b.id = bp.boss_id
           LEFT JOIN mini_heroes h ON h.id = bp.hero_id
           WHERE b.status = 'fighting'"""
    ).fetchall()
    for boss_id, player_id, raw, passive_key, passive_text in rows:
        snapshot = decode_loadout(raw)
        original = dict(snapshot)
        for field, default in LEGACY_TRAITS.items():
            snapshot.setdefault(field, default)
        snapshot.setdefault("passive_key", passive_key or "none")
        snapshot.setdefault("passive_text", passive_text or "")
        if snapshot != original:
            conn.execute(
                """UPDATE mini_boss_participants SET hero_snapshot_json = ?
                   WHERE boss_id = ? AND player_id = ?""",
                (json.dumps(snapshot, ensure_ascii=False), boss_id, player_id),
            )


def battle_loadout(raw: str) -> dict:
    # Legacy traits never fall through to live mini_heroes values.
    snapshot = decode_loadout(raw)
    return {**LEGACY_TRAITS, **snapshot}
