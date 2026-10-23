from app.mini.editorial import text as editorial_text
from app.mini.wallet import change_balance_in_transaction
import json
import sqlite3
from datetime import datetime
from app.mini.combat.hero_abilities import engine as hero_abilities
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.loadouts import battle_loadout
from app.mini.boss.errors import BossCombatError
from app.mini.combat import classes
from app.mini.boss.clock import utcnow, db_time
from app.mini.boss.repository import log_hero_event

def reward_items(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        quantity = int(item.get("quantity", 1) or 1)
        if code and quantity > 0:
            result.append({"code": code, "quantity": quantity})
    return result


def hydrate_reward_snapshot(conn: sqlite3.Connection, boss: sqlite3.Row) -> sqlite3.Row:
    if dict(boss).get("reward_snapshot_version",0)>=1:
        return boss
    template = get_boss_template(str(boss["template_code"]))
    if template is None:
        return boss

    current_items = reward_items(boss["reward_items_json"])
    shields_max = int(boss["reward_shields_max"])
    decay = int(boss["reward_decay_percent"])

    updates = []
    params = []
    if not current_items and template.get("reward_items"):
        updates.append("reward_items_json = ?")
        params.append(json.dumps(template.get("reward_items", []), ensure_ascii=False))
    if shields_max < 0:
        updates.extend(["reward_shields = ?", "reward_shields_max = ?"])
        value = int(template.get("reward_shields", 3))
        params.extend([value, value])
    if decay <= 0:
        updates.append("reward_decay_percent = ?")
        params.append(int(template.get("reward_decay_percent", 10)))

    if updates:
        params.append(int(boss["id"]))
        conn.execute(
            f"UPDATE mini_bosses SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?", (int(boss["id"]),)
        ).fetchone()
    return boss


def reward_eligible(row: sqlite3.Row) -> bool:
    return int(row["hit_count"] or 0) > 0 or bool(int(row["phantom_reward"] or 0)) or int(dict(row).get("skipped_turns",0)) == 0


def grant_victory_rewards(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    *,
    include_all_registered: bool = False,
    allow_shadow: bool = True,
    when: datetime | None = None,
) -> dict:
    reward_percent = max(0, min(100, int(boss["reward_percent"])))
    from app.mini.boss.team import coin_reward
    coins_each = coin_reward(boss)
    items = reward_items(boss["reward_items_json"])

    values=json.loads(dict(boss).get('team_state_json') or '{}')
    blessing=values.get('lunar',{}).get('arcana')==10
    if blessing:coins_each=0;items=[]
    star_bonus=False
    if int(dict(boss).get('rules_version',0))>=15 and not include_all_registered:
        snapshots=[json.loads(r[0]) for r in conn.execute('SELECT hero_snapshot_json FROM mini_boss_participants WHERE boss_id=?',(boss['id'],))]
        star_levels=[max(0,min(5,int(h.get('stars',1))-1)) for h in snapshots if h.get('passive_key')=='mythic_star']
        if star_levels:
            if 'star_reward_roll' not in values:
                values['star_reward_roll']=hero_abilities._roll_success(20+3*max(star_levels))
                conn.execute('UPDATE mini_bosses SET team_state_json=? WHERE id=?',(json.dumps(values),boss['id']))
            star_bonus=values['star_reward_roll']
    item_ids = {}
    for item in items:
        row = conn.execute(
            "SELECT id FROM mini_items WHERE code = ? AND active = 1",
            (item["code"],),
        ).fetchone()
        if row is None:
            raise BossCombatError(
                editorial_text('errors.boss.rewards.grant_victory_rewards.01', value_1=f"{item['code']}")
            )
        item_ids[item["code"]] = int(row[0])

    participants = conn.execute(
        """
        SELECT bp.*, p.coins, p.username,p.character_name, h.name AS hero_name
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
        LEFT JOIN mini_heroes h ON h.id = bp.hero_id
        WHERE bp.boss_id = ?
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    granted = 0
    missed = 0
    victory_events = []
    for row in participants:
        if int(row["reward_granted"]):
            continue
        player_id = int(row["player_id"])
        if not include_all_registered and not reward_eligible(row):
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET reward_granted = 1
                WHERE boss_id = ? AND player_id = ?
                """,
                (int(boss["id"]), player_id),
            )
            missed += 1
            continue
        if blessing:
            from app.mini.mythic.wallet import change
            victory_events.append(log_hero_event(conn,boss,row,{'type':'lunar_reward','message':f"🌙 {row['username'] or row['character_name']}: +5 мифических осколков вместо награды."},when or utcnow()))
            change(conn,player_id,5,'lunar_blessing',f"boss:{boss['id']}:lunar",{'boss_id':boss['id']})
        if star_bonus:
            victory_events.append(log_hero_event(conn,boss,row,{'type':'star_reward','message':f"✨ {row['username'] or row['character_name']}: +300 обычных осколков."},when or utcnow()))
            conn.execute('UPDATE mini_players SET shards=shards+300 WHERE id=?',(player_id,))
        if coins_each > 0:
            change_balance_in_transaction(
                conn, player_id, coins_each, f"Победа над боссом: {boss['name']}",
                "boss", int(boss["id"]), f"boss:{boss['id']}:victory:{player_id}:coins",
            )

        for item in items:
            conn.execute(
                """
                INSERT INTO mini_inventory (player_id, item_id, quantity, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(player_id, item_id) DO UPDATE SET
                    quantity = mini_inventory.quantity + excluded.quantity,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (player_id, item_ids[item["code"]], int(item["quantity"])),
            )

        if not include_all_registered and int(row["hit_count"]) > 0:
            from app.mini.shadow import extract_in_transaction
            extraction=extract_in_transaction(conn,boss,row,now=when) if allow_shadow else None
            if extraction: victory_events.append(log_hero_event(conn,boss,row,extraction,when or utcnow()))
            passive = battle_loadout(row["hero_snapshot_json"]).get("passive_key", "none")
            frozen=battle_loadout(row["hero_snapshot_json"])
            victory = hero_abilities.resolve_victory(passive,ability_definition=frozen.get("passives",{}).get("normal",{}).get("definition"))
            if victory["bonus_shards"]:
                conn.execute("UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                             (victory["bonus_shards"], player_id))
            for event in victory["events"]:
                victory_events.append(log_hero_event(conn, boss, row, event, when or utcnow()))

        conn.execute(
            """
            UPDATE mini_boss_participants
            SET reward_granted = 1
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss["id"]), player_id),
        )
        granted += 1

    return {
        "players": granted,
        "missed_players": missed,
        "coins_each": coins_each,
        "items": items,
        "shards_each": 0,
        "passive_events": victory_events,
    }


def grant_failure_rewards(conn: sqlite3.Connection, boss: sqlite3.Row) -> dict:
    shards_each = max(0, int(boss["reward_coins"]) // 10)
    participants = conn.execute(
        """
        SELECT bp.player_id, bp.reward_granted, bp.hit_count, bp.phantom_reward, bp.skipped_turns
        FROM mini_boss_participants bp
        WHERE bp.boss_id = ?
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    granted = 0
    missed = 0
    for row in participants:
        if int(row["reward_granted"]):
            continue
        player_id = int(row["player_id"])
        if not reward_eligible(row):
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET reward_granted = 1
                WHERE boss_id = ? AND player_id = ?
                """,
                (int(boss["id"]), player_id),
            )
            missed += 1
            continue
        if shards_each > 0:
            conn.execute(
                "UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                (shards_each, player_id),
            )
        conn.execute(
            """
            UPDATE mini_boss_participants
            SET reward_granted = 1
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss["id"]), player_id),
        )
        granted += 1

    return {
        "players": granted,
        "missed_players": missed,
        "coins_each": 0,
        "items": [],
        "shards_each": shards_each,
    }


def finish_victory(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = grant_victory_rewards(conn, boss, when=when)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL, reward_corruption = 0, reward_temp_hp = 0
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards


def finish_admin_victory(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict:
    """Аварийно завершает бой победой и награждает всех зарегистрированных."""
    rewards = grant_victory_rewards(
        conn,
        boss,
        include_all_registered=True,
    )
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'admin_victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL, turn_message_id = NULL, reward_corruption = 0, reward_temp_hp = 0
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards


def finish_banishment(conn,boss,when):
    """Living boss leaves treasure; no extraction or death hooks."""
    rewards=grant_victory_rewards(conn,boss,when=when,allow_shadow=False)
    conn.execute("UPDATE mini_bosses SET status='defeated',battle_result='banished',ended_at=?,turn_started_at=NULL WHERE id=?",(db_time(when),boss['id']))
    return rewards


def finish_failure(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = grant_failure_rewards(conn, boss)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'failed', battle_result = 'reward_destroyed', ended_at = ?,
            reward_percent = 0, turn_started_at = NULL
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards
