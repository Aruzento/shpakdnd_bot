from app.mini.ui.context import load_world as _load_world, load_player as _load_player
from app.mini.ui.transport import send_private_text_from_callback as _send_private
from aiogram import F, Router
from aiogram.types import CallbackQuery
from app.mini.boss.service import BossError, get_boss, list_participants, select_battle_hero
from app.mini.heroes import get_player_heroes
from app.mini.boss.presentation import battle_hero_menu, selection_details
from app.mini.boss.ui import is_joined, show_boss_home
from app.mini.boss.public import format_public_boss

from app.mini.favorites import get_favorites
from app.mini.ui.hero_selector import render_selector

router = Router(name="mini_boss_selection")

async def _hero_selection_context(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    try:
        if len(parts) != 6:
            raise ValueError
        world_id, owner_id, boss_id, value = map(int, parts[2:])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return None
    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    boss = get_boss(boss_id)
    if world is None or player is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return None
    if boss["status"] not in {"announced", "ready"}:
        await callback.answer("Героя можно выбрать только до начала боя.", show_alert=True)
        return None
    if not is_joined(player["id"], list_participants(boss_id)):
        await callback.answer("Ты не зарегистрирован на этого босса.", show_alert=True)
        return None
    return world, player, boss, value


@router.callback_query(F.data.startswith("miniboss:heroes:"))
async def boss_heroes_callback(callback: CallbackQuery):
    context = await _hero_selection_context(callback)
    if context is None:
        return
    world, player, boss, page = context
    heroes = get_player_heroes(player["id"])
    text,markup=render_selector(heroes,get_favorites(player['id']),'b',world['id'],callback.from_user.id,boss['id'])
    await callback.answer()
    await _send_private(callback,world,format_public_boss(boss,list_participants(boss['id']))+'\n\n🎴 Выбери активного героя\n'+text,markup)



@router.callback_query(F.data.startswith("miniboss:hero:"))
async def boss_select_hero_callback(callback: CallbackQuery):
    context = await _hero_selection_context(callback)
    if context is None:
        return
    world, player, boss, hero_id = context
    try:
        hero = select_battle_hero(boss["id"], player["id"], hero_id)
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer(f"⭐ Активный герой: {hero['name']}"[:200])
    await show_boss_home(callback, world, player)

