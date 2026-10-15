#!/usr/bin/env python3
"""Standalone PvP diagnostics/rollback guard. Read commands import only stdlib.

No Telegram calls, config/.env reads or migrations. Mutations require an explicit
--confirm and use the economic service for pause/refunds/expiry.
"""
import argparse
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

class OpsError(ValueError):
    pass

def readonly(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_mode & 0o444 == 0:
        raise OpsError("database_not_readable")
    # WAL-aware snapshot; immutable=1 would hide live committed WAL changes.
    conn=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn

def _issues(conn,rows,members,awards,wallet):
    by_session={r["id"]:[] for r in rows}
    for tx in wallet:
        if tx["reference_id"] not in by_session:
            raise OpsError("orphan_pvp_wallet_operation")
        by_session[tx["reference_id"]].append(tx)
    results=[]
    for row in rows:
        sid=row["id"];errors=[];warnings=[]
        people=[m for m in members if m["session_id"]==sid]
        pids={row["creator_id"]}
        if row["acceptor_id"] is not None:pids.add(row["acceptor_id"])
        active=row["status"] in ("open","choosing")
        if {m["player_id"] for m in people}!=pids or len(people)!=len(pids):
            errors.append("membership_mismatch")
        if any(bool(m["active"])!=active for m in people):errors.append("member_active_mismatch")
        if row["stake"]!=5:errors.append("stake_mismatch")
        if row["acceptor_id"]==row["creator_id"]:errors.append("self_opponent")
        if active and (row["winner_id"] is not None or row["outcome"] is not None):
            errors.append("active_has_result")
        if row["status"]!="finished" and row["winner_id"] is not None:
            errors.append("unexpected_winner")
        expected_outcome={"cancelled":("cancel","maintenance"),"expired":("timeout",),"publication_failed":("publication",)}
        if row["status"] in expected_outcome and row["outcome"] not in expected_outcome[row["status"]]:
            errors.append("closed_outcome_mismatch")
        if row["status"]=="open" and row["acceptor_id"] is not None:errors.append("open_has_opponent")
        if row["status"]=="choosing" and (row["acceptor_id"] is None or row["choose_until"] is None):
            errors.append("choosing_incomplete")
        expected=[(pid,-5,"mini_event_pvp",sid,f"pvp:{sid}:stake:{pid}") for pid in pids]
        receipts=[a for a in awards if a["session_id"]==sid]
        if row["status"]=="finished":
            choices={m["player_id"]:m["choice"] for m in people}
            beats={"saint":"demon","demon":"villager","villager":"saint"}
            first,second=choices.get(row["creator_id"]),choices.get(row["acceptor_id"])
            if len(pids)!=2 or first not in beats or second not in beats:
                errors.append("finished_choices_invalid")
            else:
                winner=None if first==second else row["creator_id"] if beats[first]==second else row["acceptor_id"]
                if row["winner_id"]!=winner or row["outcome"]!=("draw" if winner is None else "win"):
                    errors.append("result_mismatch")
            if row["outcome"]=="win" and row["winner_id"] in pids and len(pids)==2:
                winner=row["winner_id"];loser=next(p for p in pids if p!=winner)
                expected.append((winner,8,"mini_event_pvp",sid,f"pvp:{sid}:winner:{winner}"))
                if not receipts and row.get("economy_version",0)==0:
                    if not row.get("legacy_shards_reviewed_at",0):errors.append("legacy_shards_unverified")
                    else:warnings.append("legacy_shards_operator_attested")
                elif len(receipts)!=1 or (receipts[0]["player_id"],receipts[0]["amount"])!=(loser,2):
                    errors.append("shard_receipt_mismatch")
            elif row["outcome"]=="draw":
                expected.extend((pid,5,"mini_event_pvp",sid,f"pvp:{sid}:refund:{pid}") for pid in pids)
                if receipts:errors.append("unexpected_shard_receipt")
            else:errors.append("finished_result_invalid")
        elif row["status"] in ("cancelled","expired","publication_failed"):
            expected.extend((pid,5,"mini_event_pvp",sid,f"pvp:{sid}:refund:{pid}") for pid in pids)
            if receipts:errors.append("unexpected_shard_receipt")
        elif not active:errors.append("unknown_game_status")
        elif receipts:errors.append("active_has_shard_award")
        actual=[tuple(tx[k] for k in ("player_id","amount","reference_type","reference_id","operation_key"))
                for tx in by_session[sid]]
        if Counter(expected)!=Counter(actual):errors.append("wallet_transition_mismatch")
        issues=[]
        publication=row["publication"];reviewed=bool(row.get("publication_reviewed_at",0))
        message_id=row["message_id"]
        if publication in ("pending","sending"):issues.append("publication_"+publication)
        if publication=="uncertain" and not reviewed:issues.append("publication_uncertain_unreviewed")
        if publication=="sent" and (type(message_id) is not int or message_id<=0):
            issues.append("sent_without_message_id")
        if publication not in ("pending","sending","sent","uncertain","failed"):
            issues.append("unknown_publication_status")
        edit=row.get("edit_status","idle")
        if edit=="editing" or row["editing_until"]>0:issues.append("edit_lease_unresolved")
        if publication=="sent" and row["published_revision"]<row["revision"]:
            if edit in ("permanent","exhausted") and reviewed:pass
            else:issues.append("edit_"+edit+"_unresolved")
        results.append(dict(session_id=sid,world_id=row["world_id"],status=row["status"],
            publication=publication,has_message_id=bool(message_id),
            send_attempt_at=row.get("send_attempt_at",0),edit_attempt_at=row.get("edit_attempt_at",0),
            retry_count=row.get("retry_count",0),next_retry_at=row.get("next_retry_at",0),
            edit_status=edit,last_error_at=row.get("last_error_at",0),
            last_error_type=row.get("last_error_type","LegacyUnknown")[:64],
            # Descriptions are controlled codes, not old/raw exception strings.
            last_error=(row.get("last_error","") if row.get("last_error","") in {
                "","Transport result unknown","Telegram rejected request","Telegram rate limit",
                "Send lease ended without acknowledgment","Legacy edit attempt; retry scheduled",
                "Legacy publication delivery unknown","Legacy publication error"} else "Legacy error (redacted)"),
            delivery_uncertain=bool(row.get("delivery_uncertain",publication=="uncertain")),
            publication_reviewed=reviewed,errors=errors,warnings=warnings,publication_issues=issues,
            refunds_confirmed=not errors and not active and row["outcome"]!="win",
            active_stake_coins=5*len(pids) if active else 0))
    return results

def inspect_connection(conn):
    if [r[0] for r in conn.execute("PRAGMA integrity_check")]!=["ok"]:
        raise OpsError("integrity_check_failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():raise OpsError("foreign_key_check_failed")
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pvp_tables={"mini_event_pvp","mini_event_pvp_members"}
    if not (tables & pvp_tables):
        if tables & {"mini_event_pvp_control","mini_event_pvp_shard_awards"}:
            raise OpsError("partial_pvp_schema")
        if "mini_wallet_transactions" in tables and conn.execute("""SELECT 1 FROM mini_wallet_transactions
            WHERE reference_type='mini_event_pvp' OR operation_key LIKE 'pvp:%' LIMIT 1""").fetchone():
            raise OpsError("pvp_wallet_without_sessions")
        return dict(schema="pre-pvp",paused=None,blocked_create=0,blocked_accept=0,open=0,choosing=0,
                    active_stake_coins=0,unknown_publications=0,unfinished_publications=0,
                    economic_errors=0,rollback_ready=True,blockers=[],sessions=[])
    if not pvp_tables<=tables:raise OpsError("partial_pvp_schema")
    required={
        "mini_event_pvp": {"id","world_id","creator_id","acceptor_id","stake","status","winner_id",
            "outcome","revision","publication","message_id","published_revision","editing_until","last_error",
            "retry_count","next_retry_at","edit_status","edit_token","edit_revision","edit_attempt_at",
            "send_attempt_at","send_until","send_token","last_error_type","last_error_at","delivery_uncertain",
            "publication_reviewed_at","publication_review_note","economy_version",
            "legacy_shards_reviewed_at","legacy_shards_review_note"},
        "mini_event_pvp_members":{"session_id","player_id","choice","active"},
        "mini_event_pvp_control":{"id","paused","blocked_create","blocked_accept","updated_at"},
        "mini_event_pvp_shard_awards":{"session_id","player_id","amount","created_at"},
    }
    for table,columns in required.items():
        if table not in tables or not columns<={r[1] for r in conn.execute('PRAGMA table_info("'+table+'")')}:
            raise OpsError("partial_pvp_safety_schema")
    rows=[dict(r) for r in conn.execute("SELECT * FROM mini_event_pvp")]
    members=[dict(r) for r in conn.execute("SELECT * FROM mini_event_pvp_members")]
    awards=[dict(r) for r in conn.execute("SELECT * FROM mini_event_pvp_shard_awards")] if "mini_event_pvp_shard_awards" in tables else []
    wallet=[dict(r) for r in conn.execute("""SELECT * FROM mini_wallet_transactions
        WHERE reference_type='mini_event_pvp' OR operation_key LIKE 'pvp:%'""")]
    if conn.execute("SELECT 1 FROM mini_players WHERE coins<0 OR shards<0 LIMIT 1").fetchone():
        raise OpsError("negative_player_resources")
    ids={r["id"] for r in rows}
    if any(m["session_id"] not in ids for m in members) or any(a["session_id"] not in ids for a in awards):
        raise OpsError("orphan_pvp_members_or_awards")
    sessions=_issues(conn,rows,members,awards,wallet)
    control=None
    if "mini_event_pvp_control" in tables:
        controls=conn.execute("SELECT * FROM mini_event_pvp_control").fetchall()
        if len(controls)!=1 or controls[0]["id"]!=1 or controls[0]["paused"] not in (0,1):
            raise OpsError("maintenance_control_invalid")
        control=dict(controls[0])
    blockers=[]
    if not control:blockers.append("maintenance_control_missing")
    elif not control["paused"]:blockers.append("pause_new_required")
    for session in sessions:
        if session["status"] in ("open","choosing"):blockers.append(f"session:{session['session_id']}:active_escrow")
        blockers.extend(f"session:{session['session_id']}:"+code for code in session["errors"]+session["publication_issues"])
    return dict(schema="pvp",paused=bool(control["paused"]) if control else None,
        blocked_create=control["blocked_create"] if control else 0,
        blocked_accept=control["blocked_accept"] if control else 0,
        open=sum(s["status"]=="open" for s in sessions),choosing=sum(s["status"]=="choosing" for s in sessions),
        active_stake_coins=sum(s["active_stake_coins"] for s in sessions),
        unknown_publications=sum(s["delivery_uncertain"] for s in sessions),
        unfinished_publications=sum(bool(s["publication_issues"]) for s in sessions),
        economic_errors=sum(bool(s["errors"]) for s in sessions),
        rollback_ready=not blockers,blockers=blockers,sessions=sessions)

def inspect(path):
    with closing(readonly(path)) as conn:
        result=inspect_connection(conn)
        # Add location only, never names, choices, tokens or config paths.
        if result["schema"]=="pvp":
            worlds={r["id"]:dict(r) for r in conn.execute("SELECT id,chat_id,thread_id FROM mini_worlds")}
            for session in result["sessions"]:
                world=worlds.get(session["world_id"])
                if world is None:raise OpsError("missing_world")
                session.update(chat_id=world["chat_id"],topic_id=world["thread_id"])
        return result

def review(path,sid,action,note):
    # Explicit, audited operator acknowledgment; no economic data is repaired.
    if not note.strip() or len(note)>160:raise OpsError("short_review_note_required")
    with closing(sqlite3.connect(Path(path).resolve(strict=True),timeout=5)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            report=inspect_connection(conn)
            item=next((s for s in report["sessions"] if s["session_id"]==sid),None)
            if not item or item["status"] in ("open","choosing"):raise OpsError("terminal_session_required")
            row=conn.execute("SELECT * FROM mini_event_pvp WHERE id=?",(sid,)).fetchone()
            now=int(time.time())
            if action=="review-economy":
                if item["errors"]!=["legacy_shards_unverified"]:raise OpsError("only_legacy_shard_review_allowed")
                conn.execute("""UPDATE mini_event_pvp SET legacy_shards_reviewed_at=?,
                    legacy_shards_review_note=? WHERE id=?""",(now,note,sid))
            else:
                if item["errors"]:raise OpsError("economic_audit_must_pass")
                if row["publication"]=="sending" or row["editing_until"]>now:
                    raise OpsError("wait_for_request_lease")
                if action=="review-publication":
                    if row["publication"]=="sent" and row["published_revision"]<row["revision"] and row["edit_status"] not in ("permanent","exhausted"):
                        raise OpsError("finish_or_explicitly_abandon_edit")
                    conn.execute("""UPDATE mini_event_pvp SET publication_reviewed_at=?,publication_review_note=?,
                        publication=CASE WHEN publication='pending' THEN 'failed' ELSE publication END WHERE id=?""",
                        (now,note,sid))
                elif action=="abandon-edit":
                    if row["publication"]!="sent":raise OpsError("known_message_required")
                    conn.execute("""UPDATE mini_event_pvp SET edit_status='permanent',next_retry_at=0,
                        editing_until=0,edit_token='',publication_reviewed_at=?,publication_review_note=?
                        WHERE id=?""",(now,note,sid))
                else:raise OpsError("invalid_review_action")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return inspect(path)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True,help="Explicit trusted local SQLite path; never implicitly create a DB")
    parser.add_argument("--project",help="Trusted checkout; required only for service mutations")
    parser.add_argument("command",choices=("status","drain-status","rollback-check","pause-new","resume","expire",
                                         "review-publication","abandon-edit","review-economy"))
    parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--session",type=int)
    parser.add_argument("--note",default="")
    args=parser.parse_args(argv)
    try:
        if args.command in ("status","drain-status","rollback-check"):
            result=inspect(args.db)
        else:
            if not args.confirm:raise OpsError("explicit_confirm_required")
            if args.command.startswith("review-") or args.command=="abandon-edit":
                if not args.session:raise OpsError("session_required")
                result=review(args.db,args.session,args.command,args.note)
            else:
                if not args.project:raise OpsError("trusted_project_required")
                project=Path(args.project).resolve(strict=True)
                if not (project/"app/mini/events/pvp.py").is_file():raise OpsError("current_economic_service_unavailable")
                before=inspect(args.db)
                if before["schema"]!="pvp" or before["paused"] is None:raise OpsError("safety_schema_required")
                if before["economic_errors"] and args.command!="pause-new":raise OpsError("economic_review_required_no_automatic_repair")
                sys.path.insert(0,str(project))
                from app.mini.events import pvp
                if args.command=="expire":
                    pvp.expire(args.db)
                    from app.mini.events.pvp_publication import recover
                    recover(args.db)
                else:pvp.maintenance(args.command=="pause-new",args.db)
                result=inspect(args.db)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0 if args.command!="rollback-check" or result["rollback_ready"] else 2
    except (OpsError,sqlite3.Error,OSError,ValueError,RuntimeError) as error:
        # Never echo an underlying exception (URLs, tokens or private paths).
        code=str(error) if isinstance(error,OpsError) else type(error).__name__
        print(json.dumps(dict(rollback_ready=False,error=code)),file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
