"""Shared Moscow calendar, quota, history and ordinary Boss administration."""

import hashlib
import html
import json
import re
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.boss.clock import coerce_utc, db_time


def daily_bounds(now):
    local = coerce_utc(now).astimezone(ZoneInfo("Europe/Moscow"))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return db_time(start), db_time(start + timedelta(days=1))


def started_count(conn, world_id, now):
    start, end = daily_bounds(now)
    return conn.execute(
        "SELECT COUNT(*) FROM mini_bosses WHERE world_id=? AND starts_at>=? AND starts_at<?",
        (world_id, start, end),
    ).fetchone()[0]


def remaining(world_id, db_path=DB_PATH, *, now=None):
    with connect_mini_db(db_path) as conn:
        return max(0, 10 - started_count(conn, world_id, now))


def turn_token(boss):
    raw = ":".join(
        str(boss[k])
        for k in ("id", "current_round", "current_turn_position", "turn_started_at")
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _admin(conn, boss, actor_id, username):
    from app.context import get_topic_admin, normalize_username
    from app.mini.superadmin.access import is_superadmin

    world = conn.execute(
        "SELECT * FROM mini_worlds WHERE id=?", (boss["world_id"],)
    ).fetchone()
    required = get_topic_admin(world["chat_id"], world["thread_id"]) if world else None
    if not is_superadmin(actor_id) and (
        not required or not username or normalize_username(username) != required
    ):
        raise ValueError("Нет прав администратора этой темы.")


def admin_skip(
    boss_id,
    world_id,
    actor_id,
    username,
    expected_token,
    key,
    db_path=DB_PATH,
    *,
    now=None,
):
    from app.mini.boss.repository import refresh_boss, current_participant
    from app.mini.boss.runtime import advance_after_turn

    now = coerce_utc(now)
    with connect_mini_db(db_path) as conn:
        import sqlite3

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        boss = refresh_boss(conn, boss_id)
        if not boss or boss["world_id"] != world_id:
            raise ValueError("Босс не найден в этой теме.")
        _admin(conn, boss, actor_id, username)
        payload = json.dumps({"token": expected_token})
        old = conn.execute(
            "SELECT * FROM mini_boss_admin_audit WHERE operation_key=?", (key,)
        ).fetchone()
        if old:
            if (
                old["boss_id"],
                old["actor_user_id"],
                old["action"],
                old["payload"],
            ) != (boss_id, actor_id, "admin_skip", payload):
                raise ValueError("Ключ операции уже использован.")
            return json.loads(old["result_json"])
        if boss["status"] != "fighting" or turn_token(boss) != expected_token:
            raise ValueError("Ход изменился; подтверждение устарело.")
        from app.mini.boss.clock import parse_db_time

        if now >= parse_db_time(boss["turn_started_at"]) + timedelta(
            seconds=boss["turn_timeout_seconds"]
        ):
            raise ValueError("Срок хода уже истёк; обнови бой.")
        player = current_participant(conn, boss)
        event = {
            "actor_admin_id": actor_id,
            "reason": "admin",
            "hero": json.loads(player["hero_snapshot_json"]),
        }
        conn.execute(
            "INSERT INTO mini_boss_actions(boss_id,player_id,round_number,action_type,damage,boss_hp_after,created_at,event_json) VALUES(?,?,?,'admin_skip',0,?,?,?)",
            (
                boss_id,
                player["player_id"],
                boss["current_round"],
                boss["current_hp"],
                db_time(now),
                json.dumps(event),
            ),
        )
        # Deliberately does not increment skipped_turns: only no-show timeouts do.
        result = advance_after_turn(conn, boss, now)
        result["skipped_player_id"] = player["player_id"]
        conn.execute(
            "INSERT INTO mini_boss_admin_audit(boss_id,world_id,actor_user_id,operation_key,action,payload,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                boss_id,
                world_id,
                actor_id,
                key,
                "admin_skip",
                payload,
                json.dumps(result),
                db_time(now),
            ),
        )
        return result


def change_boss(
    boss_id, world_id, actor_id, flag, value, key, db_path=DB_PATH, *, now=None
):
    from app.mini.superadmin.access import require_superadmin

    require_superadmin(actor_id)
    if (
        flag not in ("-hp", "-drop", "-shield")
        or type(value) is not int
        or not 0 <= value <= 2**63 - 1
        or flag == "-hp"
        and value == 0
    ):
        raise ValueError("Недопустимый параметр или значение.")
    import sqlite3

    payload = json.dumps([flag, value])
    now = coerce_utc(now)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id=? AND world_id=?", (boss_id, world_id)
        ).fetchone()
        if not boss:
            raise ValueError("Босс не найден в текущем мире.")
        old = conn.execute(
            "SELECT * FROM mini_boss_admin_audit WHERE operation_key=?", (key,)
        ).fetchone()
        if old:
            if (
                old["boss_id"],
                old["actor_user_id"],
                old["action"],
                old["payload"],
            ) != (boss_id, actor_id, "change", payload):
                raise ValueError("Ключ операции уже использован.")
            return json.loads(old["result_json"])
        if boss["status"] not in ("announced", "ready", "fighting"):
            raise ValueError("Завершённый бой менять нельзя.")
        if flag == "-hp":
            field = "current_hp"
            before = boss[field]
            if boss["status"] == "fighting":
                if value > boss["max_hp"]:
                    raise ValueError("HP не может превышать максимум активного боя.")
                conn.execute(
                    "UPDATE mini_bosses SET current_hp=? WHERE id=?", (value, boss_id)
                )
            else:
                conn.execute(
                    "UPDATE mini_bosses SET max_hp=?,current_hp=?,original_max_hp=? WHERE id=?",
                    (value, value, value, boss_id),
                )
        elif flag == "-drop":
            field = "reward_coins"
            before = boss[field]
            conn.execute(
                "UPDATE mini_bosses SET reward_coins=? WHERE id=?", (value, boss_id)
            )
        else:
            field = "reward_shields"
            before = boss[field]
            maximum = (
                max(boss["reward_shields_max"], value)
                if boss["status"] == "fighting"
                else value
            )
            conn.execute(
                "UPDATE mini_bosses SET reward_shields=?,reward_shields_max=? WHERE id=?",
                (value, maximum, boss_id),
            )
        result = {
            "boss_id": boss_id,
            "field": field,
            "old": before,
            "new": value,
            "status": boss["status"],
        }
        conn.execute(
            "INSERT INTO mini_boss_admin_audit(boss_id,world_id,actor_user_id,operation_key,action,payload,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                boss_id,
                world_id,
                actor_id,
                key,
                "change",
                payload,
                json.dumps(result),
                db_time(now),
            ),
        )
        return result


def parse_change(text):
    parts = (text or "").split()
    if (
        len(parts) != 4
        or parts[0].split("@")[0] != "/superadminch"
        or not re.fullmatch(r"[1-9]\d*", parts[1])
        or parts[2] not in ("-hp", "-drop", "-shield")
        or not re.fullmatch(r"\d+", parts[3])
    ):
        raise ValueError("Использование: /superadminch BOSS_ID -hp/-drop/-shield ЦЕЛОЕ")
    return int(parts[1]), parts[2], int(parts[3])


def history(boss_id, world_id, page=0, db_path=DB_PATH):
    import sqlite3

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id=? AND world_id=?", (boss_id, world_id)
        ).fetchone()
        if not boss or boss["status"] not in ("defeated", "failed"):
            raise ValueError("История доступна после завершения боя этого мира.")
        actions = [
            dict(r)
            for r in conn.execute(
                "SELECT a.*,p.username,p.character_name FROM mini_boss_actions a JOIN mini_players p ON p.id=a.player_id WHERE boss_id=? ORDER BY round_number,id",
                (boss_id,),
            )
        ]
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM mini_boss_events WHERE boss_id=? ORDER BY id", (boss_id,)
            )
        ]
    # Canonical actions and actor events are displayed in separate stable sections.
    # Events retain their table ID; same-timestamp records are never guessed/reordered.
    lines = []
    line_keys = []
    for action in actions:
        if action["action_type"].startswith("boss_ability_"):
            continue
        data = json.loads(action.get("event_json") or "{}")
        hero = data.get("hero", {})
        who = action["username"] or action["character_name"] or str(action["player_id"])
        damage = data.get(
            "actual_hp_damage",
            (
                0
                if action["action_type"] in ("skip", "admin_skip", "boss_skip_queued")
                else "не сохранилось"
            ),
        )
        label = {"skip": "таймаут", "admin_skip": "пропуск администратором"}.get(
            action["action_type"], action["action_type"]
        )
        line = f"Раунд {action['round_number']} · {who} · {hero.get('name',hero.get('code','герой не сохранился'))} · {label}: {damage} HP"
        if hero.get("special_trait"):
            line += " · " + hero["special_trait"]
        proc = [
            e for e in data.get("passive_events", []) if not e.get("journal_event_id")
        ]
        if proc:
            line += "\n" + "; ".join(
                str(e.get("message") or e.get("type")) for e in proc
            )
        lines.append(line)
        line_keys.append(("actions", action["id"]))
    for event in events:
        data = json.loads(event.get("event_json") or "{}")
        lines.append(
            f"Раунд {event['round_number']} · событие #{event['id']}: "
            + str(
                data.get("message")
                or data.get("type")
                or event.get("event_type", "событие")
            )
        )
        line_keys.append(("events", event["id"]))
    if int(boss["rules_version"]) >= 15:
        with connect_mini_db(db_path) as conn:
            order = {
                (source, sid): seq
                for seq, source, sid in conn.execute(
                    "SELECT sequence,source,source_id FROM mini_boss_timeline WHERE boss_id=?",
                    (boss_id,),
                )
            }
        paired = sorted(zip(line_keys, lines), key=lambda p: order.get(p[0], 10**30))
        lines = [line for _, line in paired]
    title = ("📜 История боя — " + str(boss["name"])) + "\n\n"
    chunks = []
    units = lambda value: len(value.encode("utf-16-le")) // 2
    for line in lines:
        current = ""
        for char in line:
            encoded = char
            if units(current) + units(encoded) > 3000:
                chunks.append(current)
                current = ""
            current += encoded
        if current:
            chunks.append(current)
    texts = []
    current = title
    count = 0
    for chunk in chunks:
        if units(current) + units(chunk) + 2 > 3800 or count >= 8:
            texts.append(current)
            current = title
            count = 0
        current += chunk + "\n\n"
        count += 1
    texts.append(current.rstrip())
    page = max(0, min(int(page), len(texts) - 1))
    return {"text": texts[page], "page": page, "pages": len(texts)}
