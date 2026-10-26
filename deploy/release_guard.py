"""Frozen read-only functional rollback guard; no imports from the target executable."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

# Whole app trees from independently audited immutable commits. Any application
# change requires a new independent capability review, not a self-declared flag.
CAPABILITIES = {
    "64823c903daa7e3cea5d12613f364da67f630a54": "v15-once-raid-pets-economy",
    "908868852fc3b1e132a65e325ca546025b4034de": "legacy-pre-v15",
}
STATES = {
    "mini_mythic_raids": ("status", {"active", "rewarded", "cancelled"}),
    "story_sessions": ("status", {"registration", "active", "finished", "cancelled"}),
    "story_publications": ("status", {"pending", "sending", "sent", "uncertain"}),
    "mini_mythic_raid_publications": ("status", {"pending", "sending", "sent", "uncertain"}),
    "mini_mythic_pets": ("location", {"inventory", "active", "reserve"}),
}
# Complete schema is compared to an immutable audited schema inventory. Absence
# is valid only for a genuinely pre-v15 schema, never as a zero count on a partial DB.
SCHEMA_FILE = "release_schema.json"


class GuardError(ValueError):
    pass


def target_capability(project, target):
    if len(target) != 40 or any(c not in "0123456789abcdef" for c in target):
        raise GuardError("BLOCKED_INVALID_TARGET_SHA")
    try:
        actual = subprocess.check_output(["git", "-C", str(project), "rev-parse", target + "^{commit}"], text=True, stderr=subprocess.DEVNULL).strip()
        tree = subprocess.check_output(["git", "-C", str(project), "rev-parse", target + ":app"], text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        raise GuardError("BLOCKED_UNKNOWN_TARGET") from None
    if actual != target or tree not in CAPABILITIES:
        raise GuardError("BLOCKED_UNREVIEWED_TARGET_CAPABILITY")
    return CAPABILITIES[tree]


def inspect_connection(conn, capability):
    if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise GuardError("BLOCKED_INTEGRITY")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise GuardError("BLOCKED_FOREIGN_KEYS")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.row_factory = sqlite3.Row
    if not tables:
        raise GuardError("BLOCKED_EMPTY_DATABASE")
    inventory = json.loads(Path(__file__).with_name(SCHEMA_FILE).read_text(encoding="utf-8"))
    present = set(inventory) & tables
    if present and present != set(inventory):
        raise GuardError("BLOCKED_PARTIAL_V15_SCHEMA")
    for table, columns in inventory.items():
        if table in tables and set(columns) != {r[1] for r in conn.execute('PRAGMA table_info("' + table + '")')}:
            raise GuardError("BLOCKED_UNKNOWN_V15_COLUMNS")
    unknown = {t for t in tables if t.startswith(("story_", "mini_mythic_", "mini_star_"))} - set(inventory) - {"mini_mythic_fragments", "mini_mythic_grants"}
    if unknown:
        raise GuardError("BLOCKED_UNKNOWN_V15_SCHEMA")
    counts = {}
    blockers = []
    for table in sorted(present):
        counts[table] = conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
    for table, (column, allowed) in STATES.items():
        if table in tables:
            for status, count in conn.execute('SELECT "' + column + '",COUNT(*) FROM "' + table + '" GROUP BY "' + column + '"'):
                if status not in allowed:
                    blockers.append("BLOCKED_UNKNOWN_STATUS:" + table)
                else:
                    counts[table + ":" + status] = count
    if "mini_mythic_pets" in tables:
        for stage, count in conn.execute("SELECT stage,COUNT(*) FROM mini_mythic_pets GROUP BY stage"):
            if stage not in ("egg", "wonder", "adult"):
                blockers.append("BLOCKED_UNKNOWN_PET_STAGE")
            counts["pet_stage:" + (stage if stage in ("egg", "wonder", "adult") else "unknown")] = count
        counts["evolution_pending"] = conn.execute("SELECT COUNT(*) FROM mini_mythic_pets WHERE evolution_pending!=0").fetchone()[0]
    if "story_sessions" in tables:
        for status, code, raw in conn.execute("SELECT status,code,snapshot_json FROM story_sessions"):
            try:
                story = json.loads(raw)
                version = story.get("schema_version")
                if status == "registration" and story == {} and code == "tayna_propavshey_ovtsy":
                    version = 2
                if type(version) is not int or version not in (1, 2):
                    raise ValueError()
                if version == 2 and status != "registration" and story.get("visit_policy") != "once":
                    raise ValueError()
            except (ValueError, AttributeError, TypeError):
                blockers.append("BLOCKED_UNKNOWN_STORY_SNAPSHOT")
                continue
            key = "story_v" + str(version) + ":" + status
            counts[key] = counts.get(key, 0) + 1
    if "mini_mythic_raids" in tables:
        for snapshot, state in conn.execute("SELECT snapshot_json,state_json FROM mini_mythic_raids"):
            try:
                snapshot, state = json.loads(snapshot), json.loads(state)
                if not isinstance(snapshot, dict) or not isinstance(state, dict):
                    raise ValueError()
                policy = snapshot.get("runtime_policies", {})
                if policy.get("schema_version", 1) not in (1, 2):
                    raise ValueError()
                if state.get("mechanic", {}).get("version", 1) != 1:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                blockers.append("BLOCKED_UNKNOWN_RAID_POLICY")
    for table in ("story_publications", "mini_mythic_raid_publications"):
        if counts.get(table + ":sending") or counts.get(table + ":uncertain"):
            blockers.append("BLOCKED_MANUAL_DELIVERY_REVIEW:" + table)
    if "mini_players" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mini_players)")}
        if "mythic_shards" in cols:
            counts["universal_balances"] = conn.execute("SELECT COUNT(*) FROM mini_players WHERE mythic_shards!=0").fetchone()[0]
            if conn.execute("SELECT 1 FROM mini_players WHERE mythic_shards<0 OR typeof(mythic_shards)!='integer' LIMIT 1").fetchone():
                blockers.append("BLOCKED_NEGATIVE_BALANCE")
    if "mini_mythic_fragments" in tables:
        counts["legacy_fragment_balances"] = conn.execute("SELECT COUNT(*) FROM mini_mythic_fragments WHERE amount>0").fetchone()[0]
    if {"mini_player_heroes", "mini_heroes"} <= tables:
        counts["mythic_ownership"] = conn.execute("SELECT COUNT(*) FROM mini_player_heroes p JOIN mini_heroes h ON h.id=p.hero_id WHERE h.rarity='mythic'").fetchone()[0]
    for table, col, required in (("mini_bosses", "team_state_json", "rules_version"),
                                 ("mini_tower_attempts", "runtime_json", None)):
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute('PRAGMA table_info("' + table + '")')}
        if col not in cols or "status" not in cols:
            raise GuardError("BLOCKED_UNKNOWN_COMBAT_SCHEMA")
        for row in conn.execute('SELECT * FROM "' + table + '"'):
            # sqlite Row is installed by inspect; tests use the same contract.
            if row["status"] not in ("announced", "registration", "ready", "fighting", "active", "completed", "defeated", "failed", "cancelled", "finished", "won", "lost", "rewarded"):
                blockers.append("BLOCKED_UNKNOWN_COMBAT_STATUS:" + table)
            if row["status"] in ("announced", "registration", "ready", "fighting", "active"):
                try:
                    runtime = json.loads(row[col])
                    if not isinstance(runtime, dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    runtime = {}
                    blockers.append("BLOCKED_INVALID_COMBAT_SNAPSHOT:" + table)
                key = table + ":unfinished"
                counts[key] = counts.get(key, 0) + 1
                team = runtime.get("team", {})
                policy = team.get("reward_policy", {}) if isinstance(team, dict) else None
                if not isinstance(policy, dict) or policy.get("version", 0) not in (0, 1, 2):
                    blockers.append("BLOCKED_UNKNOWN_COMBAT_POLICY:" + table)
                    policy = {}
                rules = row[required] if required and required in cols else runtime.get("rules_version", 0)
                if type(rules) is not int or rules not in (0, 15):
                    blockers.append("BLOCKED_UNKNOWN_COMBAT_POLICY:" + table)
                if type(rules) is int and (rules >= 15 or policy.get("version", 0) == 2):
                    key = table + ":v15_unfinished"
                    counts[key] = counts.get(key, 0) + 1
    import importlib.util
    spec = importlib.util.spec_from_file_location("frozen_pvp_ops", Path(__file__).with_name("pvp_ops.py"))
    pvp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pvp)
    try:
        state = pvp.inspect_connection(conn)
        for key in ("open", "choosing", "active_stake_coins", "unknown_publications", "unfinished_publications", "economic_errors"):
            counts["pvp:" + key] = state[key]
        if not state["rollback_ready"]:
            blockers.append("BLOCKED_PVP_UNRESOLVED")
    except (ValueError, sqlite3.Error):
        blockers.append("BLOCKED_PVP_SCHEMA_OR_ECONOMY")
    persistent_v15 = any(counts[t] for t in present) or any(counts.get(key, 0) for key in (
        "universal_balances", "mythic_ownership", "mini_bosses:v15_unfinished", "mini_tower_attempts:v15_unfinished"))
    if capability != "v15-once-raid-pets-economy" and persistent_v15:
        # Includes committed ledger/operations/Star history, owned Mythics and
        # reserve pets: draining only active raids cannot make a downgrade safe.
        blockers.append("BLOCKED_V15_PERSISTENT_STATE_UNSUPPORTED")
    return dict(capability=capability, counts=counts, blockers=sorted(set(blockers)),
                rollback_ready=not blockers, read_only=True)


def inspect(project, database, target, current):
    project = Path(project).resolve(strict=True)
    raw = Path(database)
    if raw.is_symlink() or raw.resolve(strict=True) != project / "shpakdnd.db":
        raise GuardError("BLOCKED_DATABASE_OUTSIDE_PROJECT")
    capability = target_capability(project, target)
    with closing(sqlite3.connect(raw.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        # Keep tuple PRAGMA results; use Rows only for the later combat section.
        result = inspect_connection(conn, capability)
    result.update(target_sha=target, current_sha=current)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--current", required=True)
    args = parser.parse_args()
    try:
        report = inspect(args.project, args.db, args.target, args.current)
    except (GuardError, sqlite3.Error, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, GuardError) else "BLOCKED_DATABASE_OR_GUARD_UNAVAILABLE"
        report = dict(rollback_ready=False, read_only=True, blockers=[reason], target_sha=args.target, current_sha=args.current)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["rollback_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
