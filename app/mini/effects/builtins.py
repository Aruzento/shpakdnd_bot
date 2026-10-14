"""Existing V1.2 effects. Amounts, reasons and operation keys stay unchanged."""
from app.mini.editorial import text as editorial_text
from app.mini.effects.contracts import ItemEffectContext, ItemUseError


def coin_pouch(ctx: ItemEffectContext) -> None:
    amount = int(ctx.amount_picker(10, 30))
    if not 10 <= amount <= 30:
        raise ItemUseError(editorial_text('errors.effects.builtins.coin_pouch.01'))
    balance = ctx.change_balance(
        ctx.conn, int(ctx.player_id), amount, f"Использован предмет: {ctx.row['name']}",
        "item", int(ctx.item_id),
        f"item-use:{ctx.operation_key}:coins" if ctx.operation_key else "",
    )["balance"]
    ctx.result.update(amount=amount, coins=balance)


def shard_casket(ctx: ItemEffectContext) -> None:
    amount = int(ctx.amount_picker(10, 30))
    if not 10 <= amount <= 30:
        raise ItemUseError(editorial_text('errors.effects.builtins.shard_casket.01'))
    shards = int(ctx.row["shards"]) + amount
    ctx.conn.execute("UPDATE mini_players SET shards = ? WHERE id = ?", (shards, int(ctx.player_id)))
    ctx.result.update(amount=amount, shards=shards)


def rarity_coupon(ctx):
    import secrets
    from app.mini.gacha import grant_summoned_hero_in_transaction, _settings
    rarity = ctx.row['effect_key'].removeprefix('rarity_coupon_')
    heroes = ctx.conn.execute("SELECT * FROM mini_heroes WHERE active=1 AND rarity=? ORDER BY code", (rarity,)).fetchall()
    if rarity not in ('uncommon', 'rare', 'legendary') or not heroes:
        raise ItemUseError(editorial_text('errors.effects.builtins.rarity_coupon.01'))
    hero = secrets.choice(heroes)
    player = ctx.conn.execute('SELECT * FROM mini_players WHERE id=?', (ctx.player_id,)).fetchone()
    result = dict(hero)
    result.update(grant_summoned_hero_in_transaction(ctx.conn, player, hero, _settings()['duplicate_shards']))
    result.update(payment='coupon', cost_coins=0, used_ticket=False, balance=player['coins'], luck_used=False, forced_legendary=False)
    ctx.result['summon_result'] = result


def charged_effect(ctx: ItemEffectContext) -> None:
    ctx.result["charges"] = ctx.add_charge(ctx.conn, ctx.player_id, ctx.result["effect_key"], 1)


def village_boost(ctx):
    from app.mini.village.service import activate_boost
    resource='coins' if ctx.row['effect_key']=='village_gold_boost' else 'shards'
    ctx.result['boost_ends_at']=activate_boost(ctx.conn,ctx.player_id,resource)
    ctx.result['amount']=25


def equipment_chest(ctx):
    import secrets
    from app.mini.equipment.service import grant_in_transaction
    codes=[r[0] for r in ctx.conn.execute('SELECT code FROM mini_equipment ORDER BY code')]
    if not codes: raise ValueError('Каталог экипировки ещё не загружен.')
    code=secrets.choice(codes)
    grant_in_transaction(ctx.conn,ctx.player_id,code)
    ctx.result['equipment_code']=code
