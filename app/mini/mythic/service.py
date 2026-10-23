"""V1.5 public Mythic API; old per-hero crafting is retired without deleting data."""

from app.config import DB_PATH
from app.mini.mythic.wallet import transaction, change
from app.mini.mythic.schema import legacy_nonzero


def grant_mythic_shards(player_id, amount, source, idempotency_key, db_path=DB_PATH):
    if type(amount) is not int or amount <= 0:
        raise ValueError("Количество должно быть положительным целым.")
    with transaction(db_path) as conn:
        balance = change(conn, player_id, amount, source, idempotency_key)
        return {"balance": balance}


def grant_mythic_fragments(*args, **kwargs):
    raise ValueError(
        "Персональные фрагменты отключены в V1.5; используется отдельный universal mythic_shards ledger."
    )


def craft(*args, **kwargs):
    raise ValueError(
        "Mythic-герой получается только взрослением зверя. Призыв/craft отключён."
    )


def craft_by_id(*args, **kwargs):
    return craft(*args, **kwargs)


def list_heroes(player_id, db_path=DB_PATH):
    with transaction(db_path) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT h.*,ph.stars,ph.copies FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND h.rarity='mythic' ORDER BY h.code",
                (player_id,),
            )
        ]


def inspect_legacy(db_path=DB_PATH):
    with transaction(db_path) as conn:
        return legacy_nonzero(conn)
