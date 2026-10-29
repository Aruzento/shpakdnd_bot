"""Timestamp-settled pets: inventory != reserve; cooldowns use active time."""

import json
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.config import DB_PATH
from app.mini.mythic.wallet import transaction, change, previous, remember

ELEMENTS = ("ice", "lightning", "dark", "fire", "star", "earth", "moon", "sun")
STARTERS = ("fire", "earth", "lightning", "ice")
CARE = {
    "feed": (10, 86400),
    "wash": (5, 28800),
    "walk": (8, 57600),
    "play": (15, 86400),
    "sleep": (0, 86400),
}
FIRST_EGG_POLICY = "inventory_APPROVED"


def timestamp(now=None):
    if now is None:
        return int(datetime.now(timezone.utc).timestamp())
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("Timezone-aware clock required")
        return int(now.timestamp())
    if type(now) is not int:
        raise ValueError("Integer timestamp required")
    return now


def day(now):
    return (
        datetime.fromtimestamp(timestamp(now), ZoneInfo("Europe/Moscow"))
        .date()
        .isoformat()
    )


def _unlock(conn, pet):
    hero = conn.execute(
        "SELECT id,image_path AS image,mythic_element FROM mini_heroes WHERE rarity='mythic' AND mythic_element=?",
        (pet["element"],),
    ).fetchone()
    from app.mini.mythic.content import ready_hero

    if hero is None or not ready_hero(dict(hero)):
        pet["evolution_pending"] = 1
        return False
    conn.execute(
        "INSERT OR IGNORE INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,1)",
        (pet["player_id"], hero[0]),
    )
    pet.update(stage="adult", stage_seconds=0, hero_id=hero[0], evolution_pending=0)
    return True


def settle(conn, pet, now):
    pet = dict(pet)
    now = timestamp(now)
    if now < pet["settled_at"]:
        raise ValueError("Clock cannot move backwards")
    if pet["location"] != "active":
        return pet
    end = pet["active_seconds"] + now - pet["settled_at"]
    while pet["active_seconds"] < end:
        active = pet["active_seconds"]
        threshold = 30 if pet["stage"] == "egg" else 80
        needed = 259200 if pet["stage"] == "egg" else 345600
        growing = pet["stage"] != "adult" and pet["mood"] >= threshold
        boundary = min(end, pet["next_decay"])
        if growing and pet["stage_seconds"] < needed:
            boundary = min(boundary, active + needed - pet["stage_seconds"])
        delta = boundary - active
        if growing:
            pet["stage_seconds"] = min(needed, pet["stage_seconds"] + delta)
        pet["active_seconds"] = boundary
        if growing and pet["stage_seconds"] >= needed:
            if pet["stage"] == "egg":
                pet.update(stage="wonder", stage_seconds=0)
            else:
                _unlock(conn, pet)
        if boundary == pet["next_decay"]:
            if boundary > pet["protect_until"]:
                pet["mood"] = max(0, pet["mood"] - 3)
            pet["next_decay"] += 28800
    if pet["evolution_pending"]:
        _unlock(conn, pet)
    pet["settled_at"] = now
    conn.execute(
        """UPDATE mini_mythic_pets SET stage=:stage,mood=:mood,active_seconds=:active_seconds,
      stage_seconds=:stage_seconds,next_decay=:next_decay,protect_until=:protect_until,
      settled_at=:settled_at,hero_id=:hero_id,evolution_pending=:evolution_pending WHERE id=:id""",
        pet,
    )
    return pet


def first_egg(conn, pid, now, chooser=None):
    # Permanently received elements cover inventory, active, reserve and grown.
    if conn.execute(
        "SELECT 1 FROM mini_mythic_pets WHERE player_id=?", (pid,)
    ).fetchone():
        return None
    element = (chooser or secrets.choice)(STARTERS)
    if element not in STARTERS:
        raise ValueError("Invalid starter choice")
    cursor = conn.execute(
        "INSERT INTO mini_mythic_pets(player_id,element,settled_at) VALUES(?,?,?)",
        (pid, element, timestamp(now)),
    )
    return {"id": cursor.lastrowid, "element": element, "policy": FIRST_EGG_POLICY}


def buy(pid, key, db_path=DB_PATH, *, now=None, chooser=None):
    now = timestamp(now)
    payload = ["buy"]
    with transaction(db_path) as conn:
        prior = previous(conn, pid, key, payload)
        if prior is not None:
            return prior
        if not conn.execute(
            "SELECT 1 FROM mini_mythic_pets WHERE player_id=?", (pid,)
        ).fetchone():
            raise ValueError(
                "Первое бесплатное яйцо выдаётся за успешную рейдовую атаку."
            )
        owned = {
            r[0]
            for r in conn.execute(
                "SELECT element FROM mini_mythic_pets WHERE player_id=?", (pid,)
            )
        }
        owned |= {
            r[0]
            for r in conn.execute(
                "SELECT h.mythic_element FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND h.rarity='mythic'",
                (pid,),
            )
        }
        choices = [e for e in ELEMENTS if e not in owned]
        if not choices:
            raise ValueError("Все восемь яиц уже получены.")
        element = (chooser or secrets.choice)(choices)
        if element not in choices:
            raise ValueError("Invalid egg choice")
        balance = change(conn, pid, -75, "egg", key, payload)
        cursor = conn.execute(
            "INSERT INTO mini_mythic_pets(player_id,element,settled_at) VALUES(?,?,?)",
            (pid, element, now),
        )
        return remember(
            conn,
            pid,
            key,
            payload,
            {"id": cursor.lastrowid, "element": element, "balance": balance},
        )


def activate(pid, pet_id, key, db_path=DB_PATH, *, now=None):
    now = timestamp(now)
    payload = ["activate", pet_id]
    with transaction(db_path) as conn:
        prior = previous(conn, pid, key, payload)
        if prior is not None:
            return prior
        pet = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE id=? AND player_id=?", (pet_id, pid)
        ).fetchone()
        if pet is None:
            raise ValueError("Чужой или отсутствующий зверь.")
        old = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE player_id=? AND location='active'",
            (pid,),
        ).fetchone()
        if old:
            settle(conn, old, now)
            conn.execute(
                "UPDATE mini_mythic_pets SET location='reserve' WHERE id=?",
                (old["id"],),
            )
        conn.execute(
            "UPDATE mini_mythic_pets SET location='active',settled_at=? WHERE id=?",
            (now, pet_id),
        )
        return remember(
            conn,
            pid,
            key,
            payload,
            dict(
                conn.execute(
                    "SELECT * FROM mini_mythic_pets WHERE id=?", (pet_id,)
                ).fetchone()
            ),
        )


def care(pid, pet_id, action, key, db_path=DB_PATH, *, now=None):
    now = timestamp(now)
    payload = ["care", pet_id, action]
    if action not in CARE:
        raise ValueError("Неизвестное действие ухода.")
    with transaction(db_path) as conn:
        prior = previous(conn, pid, key, payload)
        if prior is not None:
            return prior
        pet = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE id=? AND player_id=? AND location='active'",
            (pet_id, pid),
        ).fetchone()
        if pet is None:
            raise ValueError("Уход доступен только активному зверю.")
        pet = settle(conn, pet, now)
        cooldowns = json.loads(pet["cooldowns_json"])
        if cooldowns.get(action, 0) > pet["active_seconds"]:
            raise ValueError("Действие ещё восстанавливается.")
        gain, cooldown = CARE[action]
        cooldowns[action] = pet["active_seconds"] + cooldown
        pet["mood"] = min(100, pet["mood"] + gain)
        if action == "sleep":
            pet["protect_until"] = pet["active_seconds"] + 57600
        conn.execute(
            "UPDATE mini_mythic_pets SET mood=?,protect_until=?,cooldowns_json=? WHERE id=?",
            (pet["mood"], pet["protect_until"], json.dumps(cooldowns), pet_id),
        )
        return remember(
            conn,
            pid,
            key,
            payload,
            {
                "mood": pet["mood"],
                "cooldowns": cooldowns,
                "protect_until": pet["protect_until"],
            },
        )


def _daily(conn, pid, now):
    date = day(now)
    conn.execute(
        "INSERT OR IGNORE INTO mini_mythic_pet_days(player_id,game_day) VALUES(?,?)",
        (pid, date),
    )
    return conn.execute(
        "SELECT * FROM mini_mythic_pet_days WHERE player_id=? AND game_day=?",
        (pid, date),
    ).fetchone()


def consume(
    pid, pet_id, equipment_code, key, db_path=DB_PATH, *, now=None, confirmed=False
):
    if not confirmed:
        raise ValueError("Подтверди уничтожение одной ненадетой вещи.")
    now = timestamp(now)
    payload = ["consume", pet_id, equipment_code]
    with transaction(db_path) as conn:
        prior = previous(conn, pid, key, payload)
        if prior is not None:
            return prior
        pet = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE id=? AND player_id=? AND location='active'",
            (pet_id, pid),
        ).fetchone()
        if pet is None or settle(conn, pet, now)["stage"] != "wonder":
            raise ValueError("Поглощать экипировку может только активный чудо-зверь.")
        daily = _daily(conn, pid, now)
        if daily["consumed"] >= 5:
            raise ValueError("Лимит: пять вещей в московские сутки.")
        owned = conn.execute(
            "SELECT quantity FROM mini_equipment_owned WHERE player_id=? AND code=?",
            (pid, equipment_code),
        ).fetchone()
        equipped = conn.execute(
            "SELECT COUNT(*) FROM mini_equipment_slots WHERE player_id=? AND code=?",
            (pid, equipment_code),
        ).fetchone()[0]
        if owned is None or owned[0] <= equipped:
            raise ValueError("Нет ненадетой единицы этой экипировки.")
        from app.mini.equipment.service import remove_in_transaction

        remove_in_transaction(conn, pid, equipment_code)
        balance = change(conn, pid, 1, "pet_equipment", key, payload)
        conn.execute(
            "UPDATE mini_mythic_pet_days SET consumed=consumed+1 WHERE player_id=? AND game_day=?",
            (pid, day(now)),
        )
        return remember(
            conn,
            pid,
            key,
            payload,
            {"balance": balance, "consumed": daily["consumed"] + 1},
        )


def claim(pid, key, db_path=DB_PATH, *, now=None):
    now = timestamp(now)
    payload = ["claim", day(now)]
    with transaction(db_path) as conn:
        prior = previous(conn, pid, key, payload)
        if prior is not None:
            return prior
        pet = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE player_id=? AND location='active'",
            (pid,),
        ).fetchone()
        if pet is None:
            raise ValueError("Нет активного взрослого зверя.")
        pet = settle(conn, pet, now)
        if pet["stage"] != "adult" or pet["mood"] != 100:
            raise ValueError("Нужен взрослый зверь с настроением 100%.")
        if _daily(conn, pid, now)["claimed"]:
            raise ValueError("Сегодня осколок уже получен.")
        balance = change(conn, pid, 1, "pet_daily", key, payload)
        conn.execute(
            "UPDATE mini_mythic_pet_days SET claimed=1 WHERE player_id=? AND game_day=?",
            (pid, day(now)),
        )
        return remember(conn, pid, key, payload, {"balance": balance, "amount": 1})


def damage_delta(conn, pid, now):
    pet = conn.execute(
        "SELECT * FROM mini_mythic_pets WHERE player_id=? AND location='active'", (pid,)
    ).fetchone()
    if pet is None:
        return 0
    pet = settle(conn, pet, now)
    return pet["mood"] - 50 if pet["stage"] == "adult" else 0


def state(pid, db_path=DB_PATH, *, now=None):
    with transaction(db_path) as conn:
        pets = [
            settle(conn, row, timestamp(now))
            for row in conn.execute(
                "SELECT * FROM mini_mythic_pets WHERE player_id=? ORDER BY id", (pid,)
            ).fetchall()
        ]
        player = conn.execute(
            "SELECT mythic_shards FROM mini_players WHERE id=?", (pid,)
        ).fetchone()
        if player is None:
            raise ValueError("Игрок не найден.")
        return {"pets": pets, "mythic_shards": player[0]}


def reserve(pid, pet_id, key, db_path=DB_PATH, *, now=None):
    now = timestamp(now)
    payload = ["reserve", pet_id]
    with transaction(db_path) as conn:
        old = previous(conn, pid, key, payload)
        if old is not None:
            return old
        pet = conn.execute(
            "SELECT * FROM mini_mythic_pets WHERE id=? AND player_id=?", (pet_id, pid)
        ).fetchone()
        if not pet:
            raise ValueError("Чужой или отсутствующий зверь.")
        pet = settle(conn, pet, now)
        conn.execute(
            "UPDATE mini_mythic_pets SET location='reserve' WHERE id=?", (pet_id,)
        )
        return remember(conn, pid, key, payload, {**dict(pet), "location": "reserve"})
