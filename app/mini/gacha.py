from app.mini.wallet import change_balance_in_transaction
import json
import secrets
import sqlite3
from pathlib import Path

from app.mini.pity import read as read_pity, required_rarity, advance as advance_pity
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.catalog import load_hero_catalog, GACHA_RARITIES
from app.mini.items import EFFECT_GACHA_LUCK, consume_effect_charge


class GachaError(ValueError):
    pass


class GachaInsufficientFunds(GachaError):
    pass


class GachaNoTicket(GachaError):
    pass


class GachaNoHeroes(GachaError):
    pass


def _settings() -> dict:
    return load_hero_catalog()["settings"]


LUCK_RARITY_MULTIPLIERS = {
    "rare": 130,
    "legendary": 110,
}


def _rarity_weight_units(raw_weight: int, rarity: str, *, luck_active: bool) -> int:
    multiplier = LUCK_RARITY_MULTIPLIERS.get(rarity, 100) if luck_active else 100
    return max(0, int(raw_weight)) * int(multiplier)


def _choose_hero_code(*, luck_active: bool = False) -> str:
    catalog = load_hero_catalog()
    settings = catalog["settings"]

    by_rarity: dict[str, list[dict]] = {}
    for hero in catalog["heroes"]:
        if hero["rarity"] not in GACHA_RARITIES or not bool(hero.get("active", True)):
            continue
        by_rarity.setdefault(hero["rarity"], []).append(hero)

    choices = []
    total_weight = 0
    for rarity, raw_weight in settings["rarity_weights"].items():
        if rarity not in GACHA_RARITIES:
            continue
        heroes = by_rarity.get(rarity, [])
        weight = _rarity_weight_units(
            int(raw_weight), rarity, luck_active=luck_active
        )
        if not heroes or weight <= 0:
            continue
        choices.append((rarity, heroes, weight))
        total_weight += weight

    if total_weight <= 0:
        raise GachaNoHeroes("В гаче пока нет активных героев.")

    roll = secrets.randbelow(total_weight)
    cursor = 0
    selected_heroes = None

    for _, heroes, weight in choices:
        cursor += weight
        if roll < cursor:
            selected_heroes = heroes
            break

    if not selected_heroes:
        selected_heroes = choices[-1][1]

    return secrets.choice(selected_heroes)["code"]


def _ticket_quantity(
    conn: sqlite3.Connection,
    player_id: int,
    ticket_item_code: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(inv.quantity, 0)
        FROM mini_items i
        LEFT JOIN mini_inventory inv
          ON inv.item_id = i.id AND inv.player_id = ?
        WHERE i.code = ?
        """,
        (int(player_id), ticket_item_code),
    ).fetchone()
    return int(row[0]) if row else 0


def get_gacha_state(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    settings = _settings()
    ticket_code = str(settings["ticket_item_code"])

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        player = conn.execute(
            "SELECT coins, shards, active_hero_id FROM mini_players WHERE id = ?",
            (int(player_id),),
        ).fetchone()
        if player is None:
            raise GachaError("Mini-игрок не найден.")

        tickets = _ticket_quantity(conn, player_id, ticket_code)
        owned = int(conn.execute(
            "SELECT COUNT(*) FROM mini_player_heroes WHERE player_id = ?",
            (int(player_id),),
        ).fetchone()[0])
        rarity_rows = conn.execute(
            """
            SELECT rarity, COUNT(*)
            FROM mini_heroes
            WHERE active = 1 AND rarity IN ('common','uncommon','rare','legendary')
            GROUP BY rarity
            """
        ).fetchall()
        luck_row = conn.execute(
            """
            SELECT charges
            FROM mini_player_effects
            WHERE player_id = ? AND effect_key = ?
            """,
            (int(player_id), EFFECT_GACHA_LUCK),
        ).fetchone()

        guarantee_row = conn.execute(
            "SELECT forced_legendary FROM mini_gacha_guarantees WHERE player_id=?",
            (int(player_id),),
        ).fetchone()
        forced_legendary = bool(guarantee_row and guarantee_row[0])
        coupons=conn.execute("SELECT COALESCE(SUM(inv.quantity),0) FROM mini_inventory inv JOIN mini_items i ON i.id=inv.item_id WHERE inv.player_id=? AND i.code IN ('rarity_coupon_uncommon','rarity_coupon_rare','rarity_coupon_legendary')",(player_id,)).fetchone()[0]
        pity = read_pity(conn, int(player_id))

    luck_charges = int(luck_row[0]) if luck_row else 0
    luck_active = luck_charges > 0
    rarity_counts = {str(rarity): int(count) for rarity, count in rarity_rows}
    rarity_weights = {
        key: int(value)
        for key, value in settings["rarity_weights"].items() if key in GACHA_RARITIES
    }
    weighted_units = {
        rarity: _rarity_weight_units(
            weight, rarity, luck_active=luck_active
        )
        for rarity, weight in rarity_weights.items()
    }
    eligible_total = sum(
        units
        for rarity, units in weighted_units.items()
        if units > 0 and rarity_counts.get(rarity, 0) > 0
    )
    rarity_chances = {}
    for rarity, units in weighted_units.items():
        if eligible_total <= 0 or rarity_counts.get(rarity, 0) <= 0:
            rarity_chances[rarity] = 0.0
        else:
            rarity_chances[rarity] = round(units * 100 / eligible_total, 1)

    # Public odds describe ordinary pulls (including visible luck potions).
    # The administrative override stays private and is applied only at pull time.

    return {
        "coupons": coupons,
        "pity": pity,
        "pull_price": int(settings["pull_price"]),
        "ticket_item_code": ticket_code,
        "tickets": tickets,
        "coins": int(player["coins"]),
        "shards": int(player["shards"]),
        "owned": owned,
        "total": sum(rarity_counts.values()),
        "rarity_counts": rarity_counts,
        "rarity_weights": rarity_weights,
        "rarity_chances": rarity_chances,
        "forced_legendary": forced_legendary,
        "luck_active": luck_active,
        "luck_charges": luck_charges,
        "active_hero_id": player["active_hero_id"],
    }


def get_previous_gacha_pull(player_id, operation_key, db_path=DB_PATH):
    """Read the persisted result without checking the already consumed ticket."""
    if not operation_key:
        return None
    with connect_mini_db(db_path) as conn:
        row = conn.execute(
            'SELECT result_json FROM mini_gacha_pulls WHERE player_id=? AND operation_key=?',
            (int(player_id), operation_key),
        ).fetchone()
    return {**json.loads(row[0]), 'repeated': True} if row else None


def perform_gacha_pull(
    player_id: int,
    payment: str = "coins",
    db_path: str | Path = DB_PATH,
    *, operation_key: str = "",
) -> dict:
    """Одна атомарная крутка. payment: coins или ticket."""
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        return _pull_in_transaction(conn, player_id, payment, operation_key=operation_key)


def _pull_in_transaction(conn, player_id, payment, *, operation_key="", charge_coins=True,settings=None):
    if payment not in {"coins", "ticket"}:
        raise GachaError("Неизвестный способ оплаты призыва.")

    if not isinstance(operation_key,str): raise GachaError('Некорректный ключ призыва.')
    settings = settings or _settings()
    pull_price = int(settings["pull_price"])
    ticket_code = str(settings["ticket_item_code"])
    duplicate_shards = {
        key: int(value)
        for key, value in settings["duplicate_shards"].items()
    }

    if operation_key:
        old=conn.execute('SELECT result_json FROM mini_gacha_pulls WHERE player_id=? AND operation_key=?',
                         (player_id,operation_key)).fetchone()
        if old:
            result=json.loads(old['result_json'])
            if result['payment']!=payment: raise GachaError('Ключ призыва уже использован.')
            return {**result,'repeated':True}

    player = conn.execute(
        """
        SELECT id, world_id, coins, shards, active_hero_id
        FROM mini_players
        WHERE id = ?
        """,
        (int(player_id),),
    ).fetchone()
    if player is None:
        raise GachaError("Mini-игрок не найден.")

    balance = int(player["coins"])
    used_ticket = 0
    cost_coins = 0

    if payment == "ticket":
        item = conn.execute(
            "SELECT id FROM mini_items WHERE code = ?",
            (ticket_code,),
        ).fetchone()
        if item is None:
            raise GachaNoTicket("Билеты призыва ещё не настроены в магазине.")

        item_id = int(item[0])
        ticket_qty = conn.execute(
            """
            SELECT quantity
            FROM mini_inventory
            WHERE player_id = ? AND item_id = ?
            """,
            (int(player_id), item_id),
        ).fetchone()
        if ticket_qty is None or int(ticket_qty[0]) <= 0:
            raise GachaNoTicket("У тебя нет билета призыва.")

        conn.execute(
            """
            UPDATE mini_inventory
            SET quantity = quantity - 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE player_id = ? AND item_id = ?
            """,
            (int(player_id), item_id),
        )
        used_ticket = 1
    else:
        if charge_coins and balance < pull_price:
            raise GachaInsufficientFunds(
                f"Не хватает {pull_price - balance} монет для призыва."
            )
        cost_coins = pull_price
        if charge_coins: balance -= pull_price


    luck_used = consume_effect_charge(
        conn, int(player_id), EFFECT_GACHA_LUCK
    )
    guarantee = conn.execute("SELECT forced_legendary FROM mini_gacha_guarantees WHERE player_id=?", (int(player_id),)).fetchone()
    forced_legendary = bool(guarantee and guarantee[0])
    counts = read_pity(conn, int(player_id))
    required = "legendary" if forced_legendary else required_rarity(counts)
    if required:
        legendary = conn.execute("SELECT code FROM mini_heroes WHERE rarity=? AND active=1 ORDER BY code", (required,)).fetchall()
        if not legendary:
            raise GachaNoHeroes(f"В гаче пока нет активных героев редкости {required}.")
        hero_code = secrets.choice(legendary)[0]
    else:
        hero_code = _choose_hero_code(luck_active=luck_used)
    hero = conn.execute(
        "SELECT * FROM mini_heroes WHERE code = ? AND active = 1 AND rarity IN ('common','uncommon','rare','legendary')",
        (hero_code,),
    ).fetchone()
    if hero is None:
        raise GachaNoHeroes("Выбранный герой больше не активен. Повтори крутку.")

    grant = grant_summoned_hero_in_transaction(conn, player, hero, duplicate_shards)
    is_duplicate = grant['is_duplicate']
    shards_awarded = grant['shards_awarded']
    copies, shards, auto_activated = grant['copies'], grant['shards'], grant['auto_activated']
    pity = advance_pity(conn, int(player_id), counts, hero['rarity'])

    pull_cursor = conn.execute(
        """
        INSERT INTO mini_gacha_pulls (
            player_id,
            hero_id,
            cost_coins,
            used_ticket,
            is_duplicate,
            shards_awarded
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(player_id),
            int(hero["id"]),
            cost_coins,
            used_ticket,
            1 if is_duplicate else 0,
            shards_awarded,
        ),
    )
    pull_id = int(pull_cursor.lastrowid)

    if cost_coins > 0 and charge_coins:
        balance = change_balance_in_transaction(
            conn, int(player_id), -cost_coins, f"Призыв героя: {hero['name']}",
            "gacha", pull_id, f"gacha:{pull_id}",
        )["balance"]

    if forced_legendary:
        conn.execute("UPDATE mini_gacha_guarantees SET forced_legendary=0 WHERE player_id=?", (int(player_id),))
    tickets_after = _ticket_quantity(conn, player_id, ticket_code)

    result = dict(hero)
    result.update({
        "pity": pity,
        "pull_id": pull_id,
        "payment": payment,
        "cost_coins": cost_coins,
        "used_ticket": bool(used_ticket),
        "is_duplicate": is_duplicate,
        "shards_awarded": shards_awarded,
        "copies": copies,
        "shards": shards,
        "balance": balance,
        "tickets": tickets_after,
        "pull_price": pull_price,
        "luck_used": bool(luck_used),
        "forced_legendary": forced_legendary,
        "auto_activated": auto_activated,
    })
    if operation_key:
        conn.execute('UPDATE mini_gacha_pulls SET operation_key=?,result_json=? WHERE id=?',
                     (operation_key,json.dumps(result,ensure_ascii=False),pull_id))
    return result


def perform_gacha_batch(player_id, db_path=DB_PATH, *, operation_key):
    """Exactly ten standard outcomes, one payment and one SQLite transaction."""
    if not isinstance(operation_key, str) or not operation_key or len(operation_key)>128:
        raise GachaError('Некорректный ключ пакета призыва.')
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT result_json FROM mini_gacha_batches WHERE player_id=? AND operation_key=?',
                         (player_id,operation_key)).fetchone()
        if old: return {**json.loads(old[0]), 'repeated':True}
        player=conn.execute('SELECT coins FROM mini_players WHERE id=?',(player_id,)).fetchone()
        if not player: raise GachaError('Mini-игрок не найден.')
        settings=_settings();price=int(settings['pull_price']); total=price*10
        if player['coins']<total: raise GachaInsufficientFunds(f"Не хватает {total-player['coins']} монет для десяти призывов.")
        cursor=conn.execute('INSERT INTO mini_gacha_batches(player_id,operation_key,cost_coins) VALUES(?,?,?)',
                            (player_id,operation_key,total))
        batch_id=cursor.lastrowid
        change_balance_in_transaction(conn,player_id,-total,'Призыв десяти героев','gacha_batch',batch_id,f'gacha_batch:{batch_id}')
        # The shared standard pull reads current ownership, luck and pity at each step.
        # Any empty pool or failure rolls back the entire debit and all ten outcomes.
        results=[_pull_in_transaction(conn,player_id,'coins',operation_key=f'batch:{batch_id}:{i}',charge_coins=False,settings=settings)
                 for i in range(10)]
        result={'batch_id':batch_id,'cost_coins':total,'results':results,'balance':results[-1]['balance'],
                'shards_awarded':sum(r['shards_awarded'] for r in results),'pity':results[-1]['pity']}
        conn.execute('UPDATE mini_gacha_batches SET result_json=? WHERE id=?',
                     (json.dumps(result,ensure_ascii=False),batch_id))
        return result


def get_gacha_batch(player_id,batch_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        row=conn.execute('SELECT result_json FROM mini_gacha_batches WHERE player_id=? AND id=?',
                         (player_id,batch_id)).fetchone()
    if not row: raise GachaError('Пакет призыва не найден.')
    return json.loads(row[0])


def grant_summoned_hero_in_transaction(conn, player, hero, duplicate_shards):
    player_id = int(player["id"])
    owned = conn.execute(
        """
        SELECT copies
        FROM mini_player_heroes
        WHERE player_id = ? AND hero_id = ?
        """,
        (int(player_id), int(hero["id"])),
    ).fetchone()

    is_duplicate = owned is not None
    shards_awarded = 0
    shards = int(player["shards"])

    if owned is None:
        copies = 1
        conn.execute(
            """
            INSERT INTO mini_player_heroes (
                player_id,
                hero_id,
                copies
            )
            VALUES (?, ?, 1)
            """,
            (int(player_id), int(hero["id"])),
        )
    else:
        shards_awarded = int(duplicate_shards.get(hero["rarity"], 0))
        copies = int(owned["copies"]) + 1
        shards += shards_awarded
        conn.execute(
            """
            UPDATE mini_player_heroes
            SET copies = ?
            WHERE player_id = ? AND hero_id = ?
            """,
            (
                copies,
                int(player_id),
                int(hero["id"]),
            ),
        )
        conn.execute(
            "UPDATE mini_players SET shards = ? WHERE id = ?",
            (shards, int(player_id)),
        )

    auto_activated = player["active_hero_id"] is None
    if auto_activated:
        conn.execute(
            "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
            (int(hero["id"]), int(player_id)),
        )
    return {"is_duplicate": is_duplicate, "shards_awarded": shards_awarded, "copies": copies, "shards": shards, "auto_activated": auto_activated}
