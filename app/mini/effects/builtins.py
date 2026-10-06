"""Existing V1.2 effects. Amounts, reasons and operation keys stay unchanged."""
from app.mini.effects.contracts import ItemEffectContext, ItemUseError


def coin_pouch(ctx: ItemEffectContext) -> None:
    amount = int(ctx.amount_picker(10, 30))
    if not 10 <= amount <= 30:
        raise ItemUseError("Некорректный результат открытия кошеля.")
    balance = ctx.change_balance(
        ctx.conn, int(ctx.player_id), amount, f"Использован предмет: {ctx.row['name']}",
        "item", int(ctx.item_id),
        f"item-use:{ctx.operation_key}:coins" if ctx.operation_key else "",
    )["balance"]
    ctx.result.update(amount=amount, coins=balance)


def shard_casket(ctx: ItemEffectContext) -> None:
    amount = int(ctx.amount_picker(10, 30))
    if not 10 <= amount <= 30:
        raise ItemUseError("Некорректный результат открытия шкатулки.")
    shards = int(ctx.row["shards"]) + amount
    ctx.conn.execute("UPDATE mini_players SET shards = ? WHERE id = ?", (shards, int(ctx.player_id)))
    ctx.result.update(amount=amount, shards=shards)


def charged_effect(ctx: ItemEffectContext) -> None:
    ctx.result["charges"] = ctx.add_charge(ctx.conn, ctx.player_id, ctx.result["effect_key"], 1)
