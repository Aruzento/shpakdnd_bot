"""Persistent event rounds. Every economic transition uses one SQLite transaction."""
import json
import secrets
import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.wallet import change_balance_in_transaction

RPS_CHOICES = ("saint", "demon", "villager")
RPS_BEATS = {"saint": "demon", "demon": "villager", "villager": "saint"}
RPS_STAKES = (1, 5, 10)
DIRECTIONS = ("left", "right")


class EventError(ValueError):
    pass


def rps_outcome(choice: str, bot_choice: str) -> str:
    if choice not in RPS_CHOICES or bot_choice not in RPS_CHOICES:
        raise EventError("Неизвестный боец.")
    if choice == bot_choice:
        return "draw"
    return "win" if RPS_BEATS[choice] == bot_choice else "loss"


def _player(conn, player_id: int, world_id: int):
    row = conn.execute(
        "SELECT * FROM mini_players WHERE id = ? AND world_id = ?",
        (player_id, world_id),
    ).fetchone()
    if row is None:
        raise EventError("Mini-персонаж в этом мире не найден.")
    return row


def _decode(row) -> dict:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def _session(conn, player_id: int, world_id: int, session_id: int) -> dict:
    _player(conn, player_id, world_id)
    row = conn.execute(
        "SELECT * FROM mini_event_sessions WHERE id = ? AND player_id = ?",
        (session_id, player_id),
    ).fetchone()
    if row is None:
        raise EventError("Это игра другого игрока или другого мира.")
    return _decode(row)


def get_session(player_id: int, world_id: int, session_id: int,
                db_path: str | Path = DB_PATH) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return _session(conn, player_id, world_id, session_id)


def _active(conn, player_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM mini_event_sessions WHERE player_id = ? AND status = 'active'",
        (player_id,),
    ).fetchone()
    return _decode(row) if row is not None else None


def get_active_session(player_id: int, world_id: int,
                       db_path: str | Path = DB_PATH) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _player(conn, player_id, world_id)
        return _active(conn, player_id)


def _start(conn, player_id, world_id, game_type, stake, start_key) -> dict:
    _player(conn, player_id, world_id)
    if (game_type == "rps" and stake not in RPS_STAKES) or (
        game_type == "labyrinth" and stake != 5
    ) or game_type not in {"rps", "labyrinth"}:
        raise EventError("Некорректная игра или ставка.")
    if not start_key or len(start_key) > 128:
        raise EventError("Некорректный ключ запуска игры.")
    # A button is consumed forever, even after its round has finished.
    existing = conn.execute(
        """SELECT s.* FROM mini_event_requests AS r
           JOIN mini_event_sessions AS s ON s.id = r.session_id
           WHERE r.player_id = ? AND r.operation_key = ?""",
        (player_id, start_key),
    ).fetchone()
    if existing is not None:
        return _decode(existing)
    active = _active(conn, player_id)
    if active is not None:
        # Consume this button too: replaying it after the resumed round ends
        # must not silently create another paid attempt.
        conn.execute(
            "INSERT INTO mini_event_requests VALUES (?, ?, ?)",
            (player_id, start_key, active["id"]),
        )
        return active

    payload = {}
    if game_type == "labyrinth":
        payload["sequence"] = [secrets.choice(DIRECTIONS) for _ in range(3)]
    cursor = conn.execute(
        """INSERT INTO mini_event_sessions
           (player_id, game_type, stake, start_key, payload_json)
           VALUES (?, ?, ?, ?, ?)""",
        (player_id, game_type, stake, start_key, json.dumps(payload)),
    )
    session_id = int(cursor.lastrowid)
    change_balance_in_transaction(
        conn, player_id, -stake, "Ставка в событии Mini",
        "mini_event", session_id, f"event:{session_id}:stake",
    )
    conn.execute(
        "INSERT INTO mini_event_requests VALUES (?, ?, ?)",
        (player_id, start_key, session_id),
    )
    return _session(conn, player_id, world_id, session_id)


def start_session(player_id: int, world_id: int, game_type: str, stake: int,
                  start_key: str, db_path: str | Path = DB_PATH) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        return _start(conn, player_id, world_id, game_type, stake, start_key)


def repeat_session(player_id: int, world_id: int, session_id: int,
                   db_path: str | Path = DB_PATH) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        previous = _session(conn, player_id, world_id, session_id)
        if previous["status"] != "resolved":
            raise EventError("Сначала закончи текущую игру.")
        return _start(conn, player_id, world_id, previous["game_type"],
                      previous["stake"], f"repeat:{session_id}")


def _save(conn, session: dict, *, resolved: bool = False):
    conn.execute(
        """UPDATE mini_event_sessions SET step = ?, payload_json = ?, status = ?,
           resolved_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
           WHERE id = ?""",
        (session["step"], json.dumps(session["payload"]),
         "resolved" if resolved else "active", resolved, session["id"]),
    )


def _finish(conn, session: dict, outcome: str, coins: int, shards: int = 0):
    session["payload"].update(outcome=outcome, coins_awarded=coins,
                              shards_awarded=shards)
    if coins:
        change_balance_in_transaction(
            conn, session["player_id"], coins, "Результат события Mini",
            "mini_event", session["id"], f"event:{session['id']}:reward",
        )
    if shards:
        # Status is checked under BEGIN IMMEDIATE. The reward and resolved
        # status commit together, including when the process dies mid-attempt.
        conn.execute("UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                     (shards, session["player_id"]))
    _save(conn, session, resolved=True)


def resolve_rps(player_id: int, world_id: int, session_id: int, choice: str,
                db_path: str | Path = DB_PATH) -> dict:
    if choice not in RPS_CHOICES:
        raise EventError("Неизвестный боец.")
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        session = _session(conn, player_id, world_id, session_id)
        if session["game_type"] != "rps":
            raise EventError("Это другая игра.")
        if session["status"] == "active":
            bot_choice = secrets.choice(RPS_CHOICES)
            outcome = rps_outcome(choice, bot_choice)
            session["payload"].update(choice=choice, bot_choice=bot_choice)
            reward = {"win": 2 * session["stake"], "draw": session["stake"], "loss": 0}
            _finish(conn, session, outcome, reward[outcome])
        return _session(conn, player_id, world_id, session_id)


def choose_direction(player_id: int, world_id: int, session_id: int,
                     expected_step: int, direction: str,
                     db_path: str | Path = DB_PATH) -> dict:
    if direction not in DIRECTIONS or expected_step not in (0, 1, 2):
        raise EventError("Некорректный проход.")
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        session = _session(conn, player_id, world_id, session_id)
        if session["game_type"] != "labyrinth":
            raise EventError("Это другая игра.")
        if session["status"] == "active" and session["step"] == expected_step:
            if direction != session["payload"]["sequence"][expected_step]:
                _finish(conn, session, "loss", 0, 3)
            else:
                session["step"] += 1
                if session["step"] == 3:
                    _finish(conn, session, "win", 10)
                else:
                    _save(conn, session)
        return _session(conn, player_id, world_id, session_id)
