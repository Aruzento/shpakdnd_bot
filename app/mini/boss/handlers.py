from app.mini.ui.context import username_from_user
from app.mini.boss.ui import admin_action_context as _admin_action_context
from app.mini.ui.context import load_world as _load_world, load_player as _load_player
from app.mini.ui.transport import delete_current_ephemeral as _delete_current_ephemeral

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.mini.boss.combat import BossCombatError, BossNotParticipant, BossNotYourTurn, force_finish_battle, hit_boss, start_battle
from app.mini.boss.notices import combat_event_lines, boss_event_line
from app.mini.boss.public import boss_attack_passive_lines, ensure_public_turn, publish_admin_victory, refresh_public_boss, replace_public_turn
from app.mini.boss.service import get_boss


router = Router(name="mini_boss")


@router.callback_query(F.data.startswith("miniboss:start:"))
async def boss_start_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "start")
    if context is None:
        return
    world, boss, _ = context
    try:
        state = start_battle(boss["id"])
    except BossCombatError as error:
        await callback.answer(str(error), show_alert=True)
        return

    boss = state["boss"]
    await refresh_public_boss(callback.bot, world, boss)
    await replace_public_turn(
        callback.bot,
        world,
        boss,
        notice="\n".join([
            "⚔️ Бой начался!",
            *(boss_event_line(event, state["participants"]) for event in state.get("boss_events", [])),
        ]),
    )
    current = state.get("current")
    who = (
        (current or {}).get("username")
        or (current or {}).get("character_name")
        or "первого игрока"
    )
    await callback.answer(f"Бой начался. Ход {who}")
    await _delete_current_ephemeral(callback)


@router.callback_query(F.data.startswith("miniboss:hit:"))
async def boss_hit_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) not in {4, 6}:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, boss_id = int(parts[2]), int(parts[3])
        expected_round = int(parts[4]) if len(parts) == 6 else None
        expected_position = int(parts[5]) if len(parts) == 6 else None
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    boss = get_boss(boss_id)
    if world is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return

    # Старые карточки (до turn-token) и дубли предыдущего хода не должны
    # оставаться рабочими после обновления. Актуальную карточку восстановит
    # ensure_public_turn. Это также обезвреживает уже висящие старые дубли.
    if expected_round is None or expected_position is None:
        await ensure_public_turn(callback.bot, world, boss)
        await callback.answer(
            "Эта кнопка хода устарела. Используй актуальное сообщение ниже.",
            show_alert=True,
        )
        return

    if (
        boss.get("status") != "fighting"
        or int(boss.get("current_round") or 0) != expected_round
        or int(boss.get("current_turn_position") or 0) != expected_position
    ):
        await ensure_public_turn(callback.bot, world, boss)
        await callback.answer(
            "Этот ход уже завершён. Используй актуальное сообщение ниже.",
            show_alert=True,
        )
        return

    try:
        result = hit_boss(
            boss_id, player["id"],
            expected_round=expected_round, expected_position=expected_position,
        )
    except (BossNotYourTurn, BossNotParticipant, BossCombatError) as error:
        refreshed = get_boss(boss_id)
        if refreshed is not None:
            await refresh_public_boss(callback.bot, world, refreshed)
            await ensure_public_turn(callback.bot, world, refreshed)
        await callback.answer(str(error), show_alert=True)
        return

    boss = result["state"]["boss"]

    passive_messages = [
        str(event.get("message", "")).strip()
        for event in result.get("passive_events", [])
        if str(event.get("message", "")).strip()
    ]
    passive_text = " ".join(passive_messages)

    attacker = (
        username_from_user(callback.from_user)
        or str(player.get("username") or "").strip()
        or str(player.get("character_name") or "Игрок").strip()
    )
    public_parts = [f"💥 {attacker} наносит {result.get('damage', 0)} урона."]
    if passive_text:
        public_parts.append(passive_text)

    reward_event = result.get("reward_event")
    public_parts.extend(combat_event_lines(result))
    public_notice = "\n".join(public_parts)

    if result.get("battle_ended"):
        if boss["status"] == "defeated":
            rewards = result.get("rewards") or {}
            message = (
                f"🏆 Победа! Урон: {result.get('damage', 0)}. "
                f"Награда за участие: {rewards.get('coins_each', 0)} монет."
            )
            if passive_text:
                message += f" {passive_text}"
            await callback.answer(message[:200], show_alert=True)
        else:
            rewards = result.get("rewards") or {}
            await callback.answer(
                (
                    f"💀 Бой проигран. Награда за участие: "
                    f"{rewards.get('shards_each', int(boss.get('reward_coins', 0)) // 10)} осколков."
                )[:200],
                show_alert=True,
            )
    else:
        suffix = ""
        if passive_text:
            suffix += f" {passive_text}."
        if reward_event:
            if reward_event.get("type") == "shield":
                suffix += f" Босс разбил щит: осталось {reward_event['shields']}."
            elif reward_event.get("type") == "reward_damage":
                suffix += f" Награда: {reward_event['reward_percent']}%."
            elif reward_event.get("type") == "boss_skip":
                suffix += " 🎵 Босс пропускает атаку по награде."
            passive_lines = boss_attack_passive_lines(reward_event)
            if passive_lines:
                suffix += " " + " ".join(passive_lines)
        await callback.answer(f"⚔️ Урон: {result['damage']}.{suffix}"[:200])

    # Если удар был сделан из старого личного ephemeral-меню, оно больше
    # не должно висеть после хода. Публичная карточка следующего хода
    # появится для всей темы ниже.
    await _delete_current_ephemeral(callback)
    await refresh_public_boss(callback.bot, world, boss)
    await replace_public_turn(
        callback.bot,
        world,
        boss,
        notice=public_notice,
    )


@router.callback_query(F.data.startswith("miniboss:forcefinish:"))
async def boss_force_finish_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "forcefinish")
    if context is None:
        return
    world, boss, _ = context

    if boss["status"] != "fighting":
        await callback.answer(
            "Завершить можно только идущий бой.",
            show_alert=True,
        )
        return

    try:
        result = force_finish_battle(boss["id"])
    except BossCombatError as error:
        await callback.answer(str(error), show_alert=True)
        return

    finished = result["state"]["boss"]
    await refresh_public_boss(callback.bot, world, finished)
    await publish_admin_victory(
        callback.bot,
        world,
        finished,
        old_turn_message_id=result.get("old_turn_message_id"),
    )
    await callback.answer("Бой завершён победой")
    await _delete_current_ephemeral(callback)



from app.mini.boss import registration, selection

router.include_router(registration.router)
router.include_router(selection.router)

from app.mini.boss.ui import _BOSS_PUBLISH_LOCKS, publish_boss as _publish_boss
from app.mini.boss.selection import boss_heroes_callback, boss_select_hero_callback
from app.mini.boss.presentation import battle_hero_menu as _battle_hero_menu
from app.mini.boss.presentation import boss_private_menu as _boss_private_menu
