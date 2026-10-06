"""Ephemeral UI for Mini events; no economic state lives in the router."""
import secrets

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.mini.handlers import (
    _load_extended_context, _load_personal_context,
    _personal_callback, _send_private_text_from_callback,
)
from app.mini.players import get_mini_player
from app.mini.wallet import InsufficientFundsError
from app.mini.events.service import (
    EventError, RPS_STAKES, choose_direction, get_active_session,
    get_session, repeat_session, resolve_rps, start_session,
)

router = Router(name="mini_events")
RPS_LABELS = {"saint": "😇 Святоша", "demon": "😈 Демон", "villager": "🧑 Житель"}
CHOICE_CODES = {"s": "saint", "d": "demon", "v": "villager"}


def _balance(player: dict) -> str:
    return f"🪙 {player['coins']} монет • 🧩 {player['shards']} осколков"


def _button(world_id: int, user_id: int, text: str, payload: str):
    return InlineKeyboardButton(
        text=text, callback_data=f"mini:ev:{world_id}:{user_id}:{payload}",
    )


def _screen(world_id: int, user_id: int, player: dict, screen: str,
            notice: str = "") -> tuple[str, InlineKeyboardMarkup]:
    def button(text, payload):
        return _button(world_id, user_id, text, payload)

    if screen == "events":
        text = ("🎪 События\n\nЗдесь можно испытать удачу и интуицию — "
                "и немного заработать. Выбери игру.")
        rows = [[button("😇 Святоша, Демон, Житель", "rps")],
                [button("🌀 Лабиринт интуиции", "lab")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=
                    _personal_callback("home", world_id, user_id))]]
    elif screen == "rps":
        text = ("😇 Святоша, Демон, Житель\n\nСвятоша побеждает Демона, "
                "Демон — Жителя, а Житель — Святошу. Выбери ставку, затем "
                "своего бойца — бот сделает выбор одновременно с тобой.")
        rows = [[button("🎲 Играть", "bets")]]
    elif screen == "bets":
        text = "😇 Святоша, Демон, Житель\n\nВыбери ставку."
        token = secrets.token_hex(6)
        rows = [[button(f"🪙 {stake}", f"start.{token}.{stake}") for stake in RPS_STAKES],
                [button("⬅️ Выйти", "events")]]
    elif screen == "lab":
        text = ("🌀 Лабиринт интуиции\n\nПеред тобой появляется проход, стены "
                "которого постоянно меняются. Трижды выбери правильное "
                "направление — и лабиринт выпустит тебя с наградой.\n\n"
                "Стоимость попытки: 🪙 5\nНаграда: 🪙 10\n"
                "При поражении: 🧩 3 осколка")
        rows = [[button("🚪 Войти в лабиринт — 5 монет", f"enter.{secrets.token_hex(6)}")],
                [button("⬅️ Назад", "events")]]
    else:
        raise EventError("Неизвестный экран.")
    text += "\n\n" + _balance(player)
    if notice:
        text += "\n\n" + notice
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _session_screen(world_id: int, user_id: int, player: dict,
                    session: dict) -> tuple[str, InlineKeyboardMarkup]:
    def button(text, payload):
        return _button(world_id, user_id, text, payload)

    sid = session["id"]
    stake = session["stake"]
    payload = session["payload"]
    if session["status"] == "active":
        if session["game_type"] == "rps":
            text = ("😇 Святоша, Демон, Житель\n\n"
                    f"Ставка принята: 🪙 {stake}\nВыбери своего бойца.")
            rows = [[button(RPS_LABELS[choice], f"pick.{sid}.{code}")]
                    for code, choice in CHOICE_CODES.items()]
        else:
            step = session["step"]
            text = ("🌀 Лабиринт интуиции\n\n" + (
                "Вы заходите в волшебный лабиринт. Перед вами два одинаковых "
                "прохода. Куда повернуть?" if step == 0 else
                "✨ Путь оказался верным. Вы заходите глубже в лабиринт..."
            ) + f"\n\nПуть: {step}/3")
            rows = [[button("⬅️ Влево", f"turn.{sid}.{step}.l"),
                     button("➡️ Вправо", f"turn.{sid}.{step}.r")]]
    elif session["game_type"] == "rps":
        result = {
            "win": f"🏆 Победа! +{stake} монет",
            "loss": f"💀 Поражение. −{stake} монет",
            "draw": "🤝 Ничья. Ставка возвращена.\nИтог: 0 монет",
        }[payload["outcome"]]
        text = (f"Ты: {RPS_LABELS[payload['choice']]}\n"
                f"Бот: {RPS_LABELS[payload['bot_choice']]}\n\n{result}")
        rows = [[button("🔁 Ещё раз!", f"again.{sid}")],
                [button("💰 Поменять ставку", f"bets.{sid}")]]
    else:
        if payload["outcome"] == "win":
            text = ("🏆 Вы находите выход!\n\nВ центре последнего зала вас ждёт "
                    "маленький сундук.\n\n🪙 Получено: 10 монет\n"
                    "Итог попытки: +5 монет")
        else:
            text = ("💀 Проход захлопывается за вашей спиной.\n\nЛабиринт "
                    "выбрасывает вас обратно ко входу, но в кармане остаётся "
                    "несколько мерцающих осколков.\n\n🪙 Потеряно: 5 монет\n"
                    "🧩 Получено: 3 осколка")
        rows = [[button("🔁 Ещё раз — 5 монет", f"again.{sid}")],
                [button("🎪 К событиям", "events")]]
    return text + "\n\n" + _balance(player), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(callback, world, player, *, screen="events", session=None, notice=""):
    # A stale result/navigation button must not hide a newer unfinished round.
    active = get_active_session(player["id"], world["id"])
    if active is not None:
        session = active
    player = get_mini_player(world["id"], callback.from_user.id)
    if session is not None:
        text, markup = _session_screen(world["id"], callback.from_user.id, player, session)
    else:
        text, markup = _screen(world["id"], callback.from_user.id, player, screen, notice)
    # Unlike the legacy edit helper's public fallback, this ALWAYS sends an
    # ephemeral message and removes the preceding ephemeral if present.
    await _send_private_text_from_callback(callback, world, text, markup)


async def _valid_location(callback, world) -> bool:
    message = callback.message
    if message is None or message.chat.id != world["chat_id"] or (
        (message.message_thread_id or 0) != int(world["thread_id"] or 0)
    ):
        await callback.answer("Эта кнопка из другого мира.", show_alert=True)
        return False
    return True


@router.callback_query(F.data.startswith("mini:events:"))
async def events_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return
    world, player = context
    if not await _valid_location(callback, world):
        return
    await callback.answer()
    await _show(callback, world, player)


@router.callback_query(F.data.startswith("mini:ev:"))
async def event_game_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "ev")
    if context is None:
        return
    world, player, extra = context
    if not await _valid_location(callback, world):
        return
    parts = extra.split(".")
    action = parts[0]
    session = None
    screen = "events"
    try:
        if action in {"events", "rps", "lab", "bets"}:
            if len(parts) == 2 and action == "bets":
                previous = get_session(player["id"], world["id"], int(parts[1]))
                if previous["game_type"] != "rps":
                    raise EventError("Это другая игра.")
            elif len(parts) != 1:
                raise EventError("Некорректная кнопка.")
            screen = action
        elif action in {"start", "enter"}:
            if len(parts) != (3 if action == "start" else 2):
                raise EventError("Некорректная кнопка.")
            token = parts[1]
            if len(token) != 12 or any(c not in "0123456789abcdef" for c in token):
                raise EventError("Некорректная кнопка.")
            screen = "bets" if action == "start" else "lab"
            session = start_session(
                player["id"], world["id"], "rps" if action == "start" else "labyrinth",
                int(parts[2]) if action == "start" else 5, f"button:{token}",
            )
        elif action == "again" and len(parts) == 2:
            previous = get_session(player["id"], world["id"], int(parts[1]))
            screen = "bets" if previous["game_type"] == "rps" else "lab"
            session = repeat_session(player["id"], world["id"], previous["id"])
        elif action == "pick" and len(parts) == 3:
            session = resolve_rps(player["id"], world["id"], int(parts[1]),
                                  CHOICE_CODES[parts[2]])
        elif action == "turn" and len(parts) == 4:
            direction = {"l": "left", "r": "right"}[parts[3]]
            session = choose_direction(player["id"], world["id"], int(parts[1]),
                                       int(parts[2]), direction)
        else:
            raise EventError("Некорректная кнопка.")
    except InsufficientFundsError:
        await callback.answer("Недостаточно монет Mini.", show_alert=True)
        await _show(callback, world, player, screen=screen, notice="Недостаточно монет Mini.")
        return
    except (EventError, ValueError, KeyError):
        await callback.answer("Игра или кнопка недоступна.", show_alert=True)
        return
    await callback.answer()
    await _show(callback, world, player, screen=screen, session=session)
