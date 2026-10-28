"""Frozen read-only functional rollback guard; no imports from the target executable."""

import argparse
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

# Whole app trees pinned after functional source audit. Independent release review
# and owner permission remain separate; runtime self-declared flags are not trusted.
CAPABILITIES = {
    "cb678fe9f0978bb115a12e27c56a7160d0c94e4c": "v15-feedback-ui-stories",
    "64823c903daa7e3cea5d12613f364da67f630a54": "v15-once-raid-pets-economy",
    "908868852fc3b1e132a65e325ca546025b4034de": "legacy-pre-v15",
}
STATES = {
    "mini_mythic_raids": ("status", {"active", "rewarded", "cancelled"}),
    "story_sessions": ("status", {"registration", "active", "finished", "cancelled"}),
    "story_publications": ("status", {"pending", "sending", "sent", "uncertain"}),
    "story_reminders": ("status", {"sending", "sent", "uncertain"}),
    "mini_public_notifications": ("status", {"pending", "sending", "sent", "uncertain", "cancelled"}),
    # This is a permanent one-shot dedupe claim, not a delivery lease. The
    # original backfill intentionally leaves status='sending' forever.
    "mini_ui_publications": ("status", {"sending"}),
    "mini_mythic_raid_publications": ("status", {"pending", "sending", "sent", "uncertain"}),
    "mini_mythic_pets": ("location", {"inventory", "active", "reserve"}),
}
# Complete schema is compared to an immutable audited schema inventory. Absence
# is valid only for a genuinely pre-v15 schema, never as a zero count on a partial DB.
SCHEMA_FILE = "release_schema.json"
# Additive feedback journals are optional on older V1.5 databases. Never treat
# their absence as a partial legacy schema, or silently authorize an old target.
FEEDBACK_SCHEMA = {
    "mini_private_windows": {"bot_id","world_id","user_id","generation","chat_id","message_id","ephemeral_id","callbacks_json"},
    "story_publication_parts": {"session_id","step","part","message_id"},
    "story_reminders": {"session_id","step","operation_key","claimed_at","status","recipients_json","message_id"},
    "story_achievements": {"user_id","story_code","ending_id","session_id","earned_at"},
}


class GuardError(ValueError):
    pass


def _git(project, *args):
    # Ignore ambient Git path/config overrides; never certify replacement code.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(project), *args],
        text=True, stderr=subprocess.DEVNULL, env=env).strip()


def target_capability(project, target):
    if not isinstance(target, str) or len(target) != 40 or any(c not in "0123456789abcdef" for c in target):
        raise GuardError("BLOCKED_INVALID_TARGET_SHA")
    try:
        if _git(project, "for-each-ref", "--format=%(refname)", "refs/replace"):
            raise GuardError("BLOCKED_GIT_REPLACEMENTS")
        actual = _git(project, "rev-parse", "--verify", target + "^{commit}")
        tree = _git(project, "rev-parse", "--verify", target + ":app")
        kind = _git(project, "cat-file", "-t", tree)
    except subprocess.CalledProcessError:
        raise GuardError("BLOCKED_UNKNOWN_TARGET") from None
    if actual != target or kind != "tree" or tree not in CAPABILITIES:
        raise GuardError("BLOCKED_UNREVIEWED_TARGET_CAPABILITY")
    return CAPABILITIES[tree]


def _table_signature(conn, table):
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    columns = [list(row)[1:] for row in conn.execute("PRAGMA table_xinfo(" + quoted(table) + ")")]
    foreign = [list(row) for row in conn.execute("PRAGMA foreign_key_list(" + quoted(table) + ")")]
    unique = []
    for index in conn.execute("PRAGMA index_list(" + quoted(table) + ")"):
        if index[2]:
            fields = [list(row)[1:] for row in conn.execute("PRAGMA index_info(" + quoted(index[1]) + ")")]
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index[1],)).fetchone()[0] if index[4] else None
            unique.append([fields, index[4], sql])
    payload = [columns, foreign, sorted(unique, key=lambda row: json.dumps(row, sort_keys=True))]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def inspect_connection(conn, capability):
    if not isinstance(capability, str) or capability not in set(CAPABILITIES.values()):
        raise GuardError("BLOCKED_UNKNOWN_CAPABILITY")
    conn.execute("PRAGMA query_only=ON")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise GuardError("BLOCKED_INTEGRITY")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise GuardError("BLOCKED_FOREIGN_KEYS")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.row_factory = sqlite3.Row
    if not tables:
        raise GuardError("BLOCKED_EMPTY_DATABASE")
    inventory = json.loads(Path(__file__).with_name(SCHEMA_FILE).read_text(encoding="utf-8"))
    baseline = inventory.pop("_baseline", None)
    if not isinstance(baseline, dict) or not {"columns", "required_mini", "boss_tables", "legacy_columns", "v15_columns", "objects", "required_dnd", "v15_boss_tables", "table_signatures", "hero_codes"} <= set(baseline):
        raise GuardError("BLOCKED_SCHEMA_INVENTORY")
    def table_columns(table):
        # table_xinfo also exposes generated/hidden columns.
        return {r[1] for r in conn.execute('PRAGMA table_xinfo("' + table + '")')}
    present = set(inventory) & tables
    if present and present != set(inventory):
        raise GuardError("BLOCKED_PARTIAL_V15_SCHEMA")
    for table, columns in inventory.items():
        if table in tables and set(columns) != {r[1] for r in conn.execute('PRAGMA table_xinfo("' + table + '")')}:
            raise GuardError("BLOCKED_UNKNOWN_V15_COLUMNS")
    feedback_present = set(FEEDBACK_SCHEMA) & tables
    if feedback_present and feedback_present != set(FEEDBACK_SCHEMA):
        raise GuardError("BLOCKED_PARTIAL_FEEDBACK_SCHEMA")
    for table, columns in FEEDBACK_SCHEMA.items():
        if table in tables and columns != {r[1] for r in conn.execute('PRAGMA table_xinfo("' + table + '")')}:
            raise GuardError("BLOCKED_UNKNOWN_FEEDBACK_COLUMNS")
    unknown = {t for t in tables if t.startswith(("story_", "mini_mythic_", "mini_star_"))} - set(inventory) - set(FEEDBACK_SCHEMA) - {"mini_mythic_fragments", "mini_mythic_grants"}
    if unknown:
        raise GuardError("BLOCKED_UNKNOWN_V15_SCHEMA")
    if feedback_present and not present:
        raise GuardError("BLOCKED_PARTIAL_V15_SCHEMA")
    known = set(baseline["columns"]) | set(inventory) | set(FEEDBACK_SCHEMA)
    if tables - known - {t for t in tables if t.startswith("sqlite_")}:
        raise GuardError("BLOCKED_UNKNOWN_DATABASE_SCHEMA")
    for table in set(baseline["columns"]) & tables:
        if table_columns(table) not in [set(v) for v in baseline["columns"][table]]:
            raise GuardError("BLOCKED_UNKNOWN_BASE_COLUMNS")
    shape = baseline["v15_columns"] if present else baseline["legacy_columns"]
    for table in set(shape) & tables:
        if table_columns(table) != set(shape[table]):
            raise GuardError("BLOCKED_PARTIAL_BASE_SCHEMA")
    for kind, name, sql in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','view')"):
        if hashlib.sha256(sql.encode()).hexdigest() not in baseline["objects"].get(kind + ":" + name, []):
            raise GuardError("BLOCKED_UNKNOWN_DATABASE_OBJECT")
    core = set(baseline["required_mini"])
    if any(t.startswith("mini_") for t in tables) and not core <= tables:
        raise GuardError("BLOCKED_PARTIAL_BASE_SCHEMA")
    dnd = set(baseline["required_dnd"])
    if tables & dnd and not dnd <= tables:
        raise GuardError("BLOCKED_PARTIAL_BASE_SCHEMA")
    bosses = set(baseline["v15_boss_tables"] if present else baseline["boss_tables"])
    if tables & bosses and not bosses <= tables:
        raise GuardError("BLOCKED_PARTIAL_BASE_SCHEMA")
    for table in tables & known:
        if _table_signature(conn, table) not in baseline["table_signatures"].get(table, []):
            raise GuardError("BLOCKED_UNKNOWN_TABLE_CONTRACT")
    counts = {}
    blockers = []
    for table in sorted(set(FEEDBACK_SCHEMA) & tables):
        counts[table] = conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
    if any(counts.get(t,0) for t in FEEDBACK_SCHEMA) and capability != "v15-feedback-ui-stories":
        blockers.append("BLOCKED_FEEDBACK_STATE_UNSUPPORTED")
    if "story_reminders" in tables:
        if conn.execute("SELECT 1 FROM story_reminders WHERE status IN ('sending','uncertain') LIMIT 1").fetchone():
            blockers.append("BLOCKED_MANUAL_DELIVERY_REVIEW:story_reminders")
    if "mini_private_windows" in tables:
        for row in conn.execute("SELECT * FROM mini_private_windows"):
            try:
                actions = json.loads(row["callbacks_json"])
                if not isinstance(actions, list) or any(not isinstance(a, str) for a in actions):
                    raise ValueError()
                if any(type(row[k]) is not int or row[k] <= 0 for k in ("bot_id", "world_id", "user_id", "generation")):
                    raise ValueError()
                if row["chat_id"] is None:
                    if row["message_id"] is not None or row["ephemeral_id"] is not None:
                        raise ValueError()
                elif (type(row["chat_id"]) is not int or type(row["message_id"]) is not int
                      or row["message_id"] < 0 or (row["ephemeral_id"] is not None and type(row["ephemeral_id"]) is not int)):
                    raise ValueError()
            except (ValueError, TypeError):
                blockers.append("BLOCKED_UNKNOWN_FEEDBACK_STATE:mini_private_windows")
    if "story_reminders" in tables:
        for raw, message, status in conn.execute("SELECT recipients_json,message_id,status FROM story_reminders"):
            try:
                recipients = json.loads(raw)
                if (not isinstance(recipients, list) or not recipients
                    or any(type(v) is not int or v <= 0 for v in recipients)
                    or len(set(recipients)) != len(recipients)
                    or (status == "sent" and (type(message) is not int or message <= 0))):
                    raise ValueError()
            except (ValueError, TypeError):
                blockers.append("BLOCKED_UNKNOWN_FEEDBACK_STATE:story_reminders")
    if "story_publication_parts" in tables:
        for part, message, status in conn.execute("""SELECT p.part,p.message_id,s.status
                FROM story_publication_parts p JOIN story_publications s
                ON s.session_id=p.session_id AND s.step=p.step"""):
            if type(part) is not int or part < 0 or type(message) is not int or message <= 0 or status == "pending":
                blockers.append("BLOCKED_UNKNOWN_FEEDBACK_STATE:story_publication_parts")
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
                if type(policy.get("schema_version", 1)) is not int or policy.get("schema_version", 1) not in (1, 2):
                    raise ValueError()
                if type(state.get("mechanic", {}).get("version", 1)) is not int or state.get("mechanic", {}).get("version", 1) != 1:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                blockers.append("BLOCKED_UNKNOWN_RAID_POLICY")
    for table in ("story_publications", "mini_mythic_raid_publications", "mini_public_notifications"):
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
    if {"mini_player_heroes", "mini_heroes", "mini_players"} <= tables:
        # Registry/image support is part of functional ownership compatibility.
        # An empty Raid/Story journal cannot authorize an S5 downgrade to V1.4.
        supported = baseline["hero_codes"]["legacy" if capability == "legacy-pre-v15" else "v15"]
        placeholders = ",".join("?" for _ in supported)
        counts["target_unsupported_hero_ownership"] = conn.execute(
            "SELECT COUNT(*) FROM mini_player_heroes p JOIN mini_heroes h ON h.id=p.hero_id "
            "WHERE h.code NOT IN (" + placeholders + ")", supported).fetchone()[0]
        counts["target_unsupported_active_hero"] = conn.execute(
            "SELECT COUNT(*) FROM mini_players p JOIN mini_heroes h ON h.id=p.active_hero_id "
            "WHERE h.code NOT IN (" + placeholders + ")", supported).fetchone()[0]
        if counts["target_unsupported_hero_ownership"] or counts["target_unsupported_active_hero"]:
            blockers.append("BLOCKED_TARGET_HERO_OWNERSHIP_UNSUPPORTED")
    for table, col, required in (("mini_bosses", "team_state_json", "rules_version"),
                                 ("mini_tower_attempts", "runtime_json", None)):
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute('PRAGMA table_info("' + table + '")')}
        legacy_boss = (table == "mini_bosses" and not present
                       and cols == set(baseline["legacy_columns"][table]))
        if "status" not in cols or (col not in cols and not legacy_boss):
            raise GuardError("BLOCKED_UNKNOWN_COMBAT_SCHEMA")
        for row in conn.execute('SELECT * FROM "' + table + '"'):
            # sqlite Row is installed by inspect; tests use the same contract.
            if row["status"] not in ("announced", "registration", "ready", "fighting", "active", "completed", "defeated", "failed", "cancelled", "finished", "won", "lost", "rewarded"):
                blockers.append("BLOCKED_UNKNOWN_COMBAT_STATUS:" + table)
            if row["status"] in ("announced", "registration", "ready", "fighting", "active"):
                if legacy_boss:
                    key = table + ":unfinished"
                    counts[key] = counts.get(key, 0) + 1
                    continue  # Exact audited V1.4 shape has no V1.5 team snapshot.
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
                if not isinstance(policy, dict) or type(policy.get("version", 0)) is not int or policy.get("version", 0) not in (0, 1, 2):
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
        "universal_balances", "mythic_ownership", "target_unsupported_hero_ownership", "target_unsupported_active_hero", "mini_bosses:v15_unfinished", "mini_tower_attempts:v15_unfinished"))
    if capability not in ("v15-once-raid-pets-economy","v15-feedback-ui-stories") and persistent_v15:
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
    if not isinstance(current, str) or len(current) != 40 or any(c not in "0123456789abcdef" for c in current):
        raise GuardError("BLOCKED_INVALID_CURRENT_SHA")
    try:
        if _git(project, "rev-parse", "--verify", "HEAD^{commit}") != current:
            raise GuardError("BLOCKED_CURRENT_HEAD_MISMATCH")
    except subprocess.CalledProcessError:
        raise GuardError("BLOCKED_UNKNOWN_CURRENT") from None
    with closing(sqlite3.connect(raw.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        # Keep tuple PRAGMA results; use Rows only for the later combat section.
        result = inspect_connection(conn, capability)
    result.update(target_sha=target, current_sha=current)
    return result


def public_pvp_report(path):
    # Raw reports contain session IDs: publish fixed aggregate codes only.
    try:
        raw = json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text(encoding="utf-8"))
        codes = raw.get("blockers", [])
        if not isinstance(codes, list) or not codes:
            raise ValueError()
        allowed = {"pause_new_required", "maintenance_control_missing"}
        return {"blockers": sorted({code if isinstance(code, str) and code in allowed
                                  else "pvp_session_review_required" for code in codes})}
    except (OSError, ValueError, AttributeError, TypeError):
        return {"blockers": ["pvp_report_unavailable"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--db")
    parser.add_argument("--target")
    parser.add_argument("--current")
    parser.add_argument("--pvp-report")
    args = parser.parse_args()
    if args.pvp_report:
        print(json.dumps(public_pvp_report(args.pvp_report), sort_keys=True))
        return 0
    if not all((args.project, args.db, args.target, args.current)):
        parser.error("project, db, target and current required")
    try:
        report = inspect(args.project, args.db, args.target, args.current)
    except (GuardError, sqlite3.Error, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, GuardError) else "BLOCKED_DATABASE_OR_GUARD_UNAVAILABLE"
        report = dict(rollback_ready=False, read_only=True, blockers=[reason], target_sha=args.target, current_sha=args.current)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["rollback_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
