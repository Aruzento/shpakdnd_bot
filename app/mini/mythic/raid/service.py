"""Free-order raids. HP, limits, events, eggs and payouts commit together."""

import json
import sqlite3
import secrets
from app.config import DB_PATH
from app.mini.mythic.wallet import transaction, change
from app.mini.mythic.pets import timestamp, day, first_egg, damage_delta
from app.mini.mythic.raid.catalog import load
from app.mini.mythic.raid.effects import resolve
from app.mini.mythic.raid.rewards import allocate
from app.mini.combat.modes import freeze_hero


def _raid(conn, raid_id, world_id=None):
    row = conn.execute(
        "SELECT * FROM mini_mythic_raids WHERE id=?", (raid_id,)
    ).fetchone()
    if row is None or (world_id is not None and row["world_id"] != world_id):
        raise ValueError("Рейд не найден в этом мире.")
    result = dict(row)
    result["snapshot"] = json.loads(row["snapshot_json"])
    result["state"] = json.loads(row["state_json"])
    return result


def start(player_id, code, key, db_path=DB_PATH, *, now=None):
    now = timestamp(now)
    entries = load()
    template = next((v for v in entries if v["code"] == code), None)
    if template is None:
        raise ValueError("Реальный каталог четырёх мифических боссов ещё не заполнен.")
    with transaction(db_path) as conn:
        player = conn.execute(
            "SELECT * FROM mini_players WHERE id=?", (player_id,)
        ).fetchone()
        if player is None:
            raise ValueError("Сначала зарегистрируйся в Mini.")
        old = conn.execute(
            "SELECT * FROM mini_mythic_raid_audit WHERE operation_key=?", (key,)
        ).fetchone()
        if old:
            if (
                old["actor_id"] != player_id
                or old["action"] != "start"
                or json.loads(old["payload"]) != code
            ):
                raise ValueError("Ключ операции уже использован.")
            return _raid(conn, old["raid_id"], player["world_id"])
        if conn.execute(
            "SELECT 1 FROM mini_mythic_raids WHERE world_id=? AND status='active'",
            (player["world_id"],),
        ).fetchone():
            raise ValueError("В мире уже идёт мифический рейд.")
        hp = template["max_hp"]
        cursor = conn.execute(
            "INSERT INTO mini_mythic_raids(world_id,template_code,snapshot_json,original_max_hp,max_hp,current_hp,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                player["world_id"],
                code,
                json.dumps(template, ensure_ascii=False),
                hp,
                hp,
                hp,
                player_id,
                now,
            ),
        )
        rid = cursor.lastrowid
        conn.execute(
            "INSERT INTO mini_mythic_raid_audit(raid_id,actor_id,operation_key,action,payload,created_at) VALUES(?,?,?,?,?,?)",
            (rid, player_id, key, "start", json.dumps(code), now),
        )
        return _raid(conn, rid)


def _arise(conn, raid, pid, *, roller=None, now=None):
    target = raid["snapshot"]
    if not target.get("shadow_extractable", False):
        return []
    shadow = conn.execute(
        "SELECT id,code FROM mini_heroes WHERE rarity='shadow' AND code=?",
        (target.get("shadow_hero_code"),),
    ).fetchone()
    if shadow is None:
        return []
    from app.mini.combat.arise import sources, award

    chances = []
    for row in conn.execute(
        "SELECT hero_json FROM mini_mythic_raid_attacks WHERE raid_id=? AND player_id=? ORDER BY id",
        (raid["id"], pid),
    ):
        chances.extend(sources(json.loads(row[0]), mode="raid"))
    contribution = conn.execute(
        "SELECT damage FROM mini_mythic_raid_damage WHERE raid_id=? AND player_id=?",
        (raid["id"], pid),
    ).fetchone()
    return award(
        conn,
        "raid",
        raid["id"],
        pid,
        target,
        bool(contribution and contribution[0] > 0),
        chances,
        now=now,
        roller=roller,
    )


def _finalize(conn, raid, now, *, chooser=None, roller=None):
    if raid["status"] != "active":
        return json.loads(raid["result_json"])
    contributions = dict(
        conn.execute(
            "SELECT player_id,damage FROM mini_mythic_raid_damage WHERE raid_id=?",
            (raid["id"],),
        )
    )
    rewards = allocate(raid["original_max_hp"], contributions, chooser=chooser)
    lunar = raid["state"].get("lunar", {})
    for reward in rewards:
        if lunar.get("arcana") == 0:
            reward["lunar_bonus"] = 1 if lunar["level"] < 3 else 2
            reward["amount"] += reward["lunar_bonus"]
        pid = reward["player_id"]
        change(
            conn,
            pid,
            reward["amount"],
            "raid_reward",
            f"raid:{raid['id']}:reward",
            {"raid_id": raid["id"]},
        )
        reward["arise"] = _arise(conn, raid, pid, roller=roller, now=now)
        conn.execute(
            "INSERT INTO mini_mythic_raid_rewards VALUES(?,?,?,?,?,?)",
            (
                raid["id"],
                pid,
                reward["rank"],
                reward["damage"],
                reward["amount"],
                json.dumps(reward),
            ),
        )
    result = {"rewards": rewards, "fund": (raid["original_max_hp"] + 4999) // 5000}
    conn.execute(
        "UPDATE mini_mythic_raids SET status='rewarded',ended_at=?,result_json=? WHERE id=? AND status='active'",
        (now, json.dumps(result), raid["id"]),
    )
    return result


def attack(
    player_id,
    raid_id,
    hero_id,
    key,
    db_path=DB_PATH,
    *,
    now=None,
    chooser=None,
    roller=None,
):
    if not key:
        raise ValueError("Ключ операции обязателен.")
    now = timestamp(now)
    date = day(now)
    payload = json.dumps([raid_id, hero_id])
    with transaction(db_path) as conn:
        player = conn.execute(
            "SELECT * FROM mini_players WHERE id=?", (player_id,)
        ).fetchone()
        if player is None:
            raise ValueError("Сначала зарегистрируйся в Mini.")
        raid = _raid(conn, raid_id, player["world_id"])
        old = conn.execute(
            "SELECT * FROM mini_mythic_raid_attacks WHERE player_id=? AND operation_key=?",
            (player_id, key),
        ).fetchone()
        if old:
            if old["payload"] != payload:
                raise ValueError("Ключ операции уже использован.")
            return json.loads(old["result_json"])
        if raid["status"] != "active":
            raise ValueError("Рейд завершён.")
        count = conn.execute(
            "SELECT COUNT(*) FROM mini_mythic_raid_attacks WHERE player_id=? AND game_day=?",
            (player_id, date),
        ).fetchone()[0]
        if count >= 10:
            raise ValueError("Лимит десяти атак за московские сутки исчерпан.")
        if conn.execute(
            "SELECT 1 FROM mini_mythic_raid_attacks WHERE raid_id=? AND player_id=? AND hero_id=? AND game_day=?",
            (raid_id, player_id, hero_id, date),
        ).fetchone():
            raise ValueError("Этот герой уже атаковал рейд сегодня.")
        row = conn.execute(
            "SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND ph.hero_id=?",
            (player_id, hero_id),
        ).fetchone()
        if row is None:
            raise ValueError("Этот герой тебе не принадлежит.")
        from app.mini.availability import assert_available

        assert_available(conn, player_id, hero_id)
        source = dict(row)
        source["passives"] = json.loads(source.get("passives_json") or "{}")
        hero = freeze_hero(source)
        from app.mini.equipment.service import aggregate_in_transaction

        hero["attack"] += aggregate_in_transaction(conn, player_id)
        result = resolve(
            raid,
            hero,
            player_id,
            pet_delta=damage_delta(conn, player_id, now),
            chooser=chooser,
        )
        for event in result["events"]:
            if event["actual_hp_damage"]:
                conn.execute(
                    "INSERT INTO mini_mythic_raid_damage VALUES(?,?,?) ON CONFLICT(raid_id,player_id) DO UPDATE SET damage=damage+excluded.damage",
                    (raid_id, event["actor_id"], event["actual_hp_damage"]),
                )
        result["egg"] = (
            first_egg(conn, player_id, now, chooser=chooser)
            if result["actual_hp_damage"] > 0
            else None
        )
        result.update(raid_id=raid_id, hero_id=hero_id, attempts_left=9 - count)
        conn.execute(
            "UPDATE mini_mythic_raids SET state_json=?,current_hp=?,max_hp=?,revision=revision+1 WHERE id=?",
            (
                json.dumps(result["state"]),
                result["current_hp"],
                result["max_hp"],
                raid_id,
            ),
        )
        conn.execute(
            "INSERT INTO mini_mythic_raid_attacks(raid_id,player_id,hero_id,game_day,operation_key,payload,hero_json,events_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                raid_id,
                player_id,
                hero_id,
                date,
                key,
                payload,
                json.dumps(hero),
                json.dumps(result["events"]),
                json.dumps(result),
                now,
            ),
        )
        if result["current_hp"] == 0:
            result["final"] = _finalize(
                conn, _raid(conn, raid_id), now, chooser=chooser, roller=roller
            )
        conn.execute(
            "UPDATE mini_mythic_raid_attacks SET result_json=? WHERE player_id=? AND operation_key=?",
            (json.dumps(result), player_id, key),
        )
        return result


def cancel(
    raid_id,
    world_id,
    actor_user_id,
    key,
    db_path=DB_PATH,
    *,
    with_reward=False,
    confirmed=False,
    reason="",
    now=None,
    actor_username=None,
    expected_revision=None,
):
    if not confirmed:
        raise ValueError("Требуется подтверждение отмены.")
    now = timestamp(now)
    with transaction(db_path) as conn:
        raid = _raid(conn, raid_id, world_id)
        world = conn.execute(
            "SELECT chat_id,thread_id FROM mini_worlds WHERE id=?", (world_id,)
        ).fetchone()
        from app.context import get_topic_admin

        required = (
            get_topic_admin(world["chat_id"], world["thread_id"]) if world else None
        )
        from app.mini.superadmin.access import SUPERADMIN_USER_ID

        # Callers resolve topic administrator by Telegram identity; superadmin is numeric.
        if actor_user_id != SUPERADMIN_USER_ID and (
            not required
            or not actor_username
            or actor_username.lower().lstrip("@") != required.lower().lstrip("@")
        ):
            raise ValueError("Нет прав администратора для отмены рейда.")
        if with_reward:
            raise ValueError(
                "Параметры выплаты при отмене с наградой не утверждены (O01)."
            )
        old = conn.execute(
            "SELECT * FROM mini_mythic_raid_audit WHERE operation_key=?", (key,)
        ).fetchone()
        if old:
            if old["raid_id"] != raid_id or old["actor_id"] != actor_user_id:
                raise ValueError("Ключ операции уже использован.")
            return {"cancelled": True, "applied": False}
        if raid["status"] != "active":
            raise ValueError("Рейд уже завершён.")
        if expected_revision is not None and raid["revision"] != expected_revision:
            raise ValueError("Рейд изменился, подтверди отмену заново.")
        conn.execute(
            "UPDATE mini_mythic_raids SET status='cancelled',ended_at=?,revision=revision+1 WHERE id=?",
            (now, raid_id),
        )
        conn.execute(
            "INSERT INTO mini_mythic_raid_audit(raid_id,actor_id,operation_key,action,payload,created_at) VALUES(?,?,?,?,?,?)",
            (
                raid_id,
                actor_user_id,
                key,
                "cancel",
                json.dumps({"reason": reason, "with_reward": False}),
                now,
            ),
        )
        return {"cancelled": True, "applied": True}


def state(world_id, db_path=DB_PATH, *, raid_id=None):
    with transaction(db_path) as conn:
        row = (
            conn.execute(
                "SELECT id FROM mini_mythic_raids WHERE world_id=? ORDER BY id DESC LIMIT 1",
                (world_id,),
            ).fetchone()
            if raid_id is None
            else (raid_id,)
        )
        if row is None:
            return None
        raid = _raid(conn, row[0], world_id)
        raid["ranking"] = [
            dict(r)
            for r in conn.execute(
                "SELECT d.*,p.character_name,p.username FROM mini_mythic_raid_damage d JOIN mini_players p ON p.id=d.player_id WHERE raid_id=? ORDER BY damage DESC,d.player_id",
                (raid["id"],),
            )
        ]
        return raid


def recover_publications(db_path=DB_PATH):
    with transaction(db_path) as conn:
        return conn.execute(
            "UPDATE mini_mythic_raid_publications SET status='uncertain' WHERE status='sending'"
        ).rowcount
