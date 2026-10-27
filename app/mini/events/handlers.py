from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.editorial.registry import LiveMapping
"""Ephemeral UI for Mini events; no economic state lives in the router."""
from app.mini.ui.context import load_extended_context, load_personal_context, personal_callback, validate_callback_location
from app.mini.ui.transport import send_private_text_from_callback
from app.topic_guard import is_mini_topic
import secrets

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from app.mini.players import get_mini_player
from app.mini.wallet import InsufficientFundsError
from app.mini.events.service import (
    EventError, RPS_STAKES, choose_direction, get_active_session,
    get_session, repeat_session, resolve_rps, start_session,
)


router = Router(name="mini_events")
RPS_LABELS = LiveMapping({'saint': 'rps.saint', 'demon': 'rps.demon', 'villager': 'rps.villager'}, terms=True, decorated=True)
CHOICE_CODES = {"s": "saint", "d": "demon", "v": "villager"}


@editorial_screen
def _balance(player: dict) -> str:
    return editorial_text('events.balance.line_01', coins=f"{player['coins']}", shards=f"{player['shards']}")


@editorial_screen
def _button(world_id: int, user_id: int, text: str, payload: str):
    return InlineKeyboardButton(
        text=text, callback_data=f"mini:ev:{world_id}:{user_id}:{payload}",
    )


@editorial_screen
def _screen(world_id: int, user_id: int, player: dict, screen: str,
            notice: str = "") -> tuple[str, InlineKeyboardMarkup]:
    def button(text, payload):
        return _button(world_id, user_id, text, payload)

    if screen == "events":
        text = (editorial_text('events.screen.line_01'))
        rows = [[button(editorial_text('events.screen.line_04'), "rps")],
                [button(editorial_text('events.screen.line_05'), "lab")],
                [InlineKeyboardButton(text=editorial_text('navigation.back'), callback_data=
                    personal_callback("fair", world_id, user_id))]]
    elif screen == "rps":
        text = (editorial_text('events.screen.line_02'))
        from app.mini.features import enabled
        rows = [[button(editorial_text('pvp.bot'), "bets")]]
        if enabled("pvp"):
            rows.append([InlineKeyboardButton(text=editorial_text("pvp.player"),callback_data=f"mini:pvp:{world_id}:{user_id}:home")])
    elif screen == "bets":
        text = editorial_text('events.screen.line_03')
        token = secrets.token_hex(6)
        rows = [[button(editorial_text('events.screen.line_10', stake=f'{stake}'), f"start.{token}.{stake}") for stake in RPS_STAKES],
                [button(editorial_text('events.screen.line_11'), "events")]]
    elif screen == "lab":
        text = (editorial_text('events.screen.line_06'))
        rows = [[button(editorial_text('events.screen.line_12'), f"enter.{secrets.token_hex(6)}")],
                [button(editorial_text('navigation.back'), "events")]]
    else:
        raise EventError(editorial_text('errors.events.handlers.screen.01'))
    if screen=='rps':rows.append([button(editorial_text('navigation.back'),'events')])
    text += "\n\n" + _balance(player)
    if notice:
        text += "\n\n" + notice
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
def _session_screen(world_id: int, user_id: int, player: dict,
                    session: dict) -> tuple[str, InlineKeyboardMarkup]:
    def button(text, payload):
        return _button(world_id, user_id, text, payload)

    sid = session["id"]
    stake = session["stake"]
    payload = session["payload"]
    if session["status"] == "active":
        if session["game_type"] == "rps":
            text = (editorial_text('events.session_screen.line_01', stake=f'{stake}'))
            rows = [[button(RPS_LABELS[choice], f"pick.{sid}.{code}")]
                    for code, choice in CHOICE_CODES.items()]
        else:
            step = session["step"]
            text = (editorial_text('events.session_screen.line_06') + (
                editorial_text('events.session_screen.line_10') if step == 0 else
                editorial_text('events.session_screen.line_11')
            ) + editorial_text('events.session_screen.line_03', step=f'{step}'))
            rows = [[button(editorial_text('events.session_screen.line_12'), f"turn.{sid}.{step}.l"),
                     button(editorial_text('events.session_screen.line_13'), f"turn.{sid}.{step}.r")]]
    elif session["game_type"] == "rps":
        result = {
            "win": editorial_text('events.session_screen.line_07', stake=f'{stake}'),
            "loss": editorial_text('events.session_screen.line_08', stake=f'{stake}'),
            "draw": editorial_text('events.session_screen.line_09'),
        }[payload["outcome"]]
        text = (editorial_text('events.session_screen.line_02', value_1=f"{RPS_LABELS[payload['choice']]}", value_2=f"{RPS_LABELS[payload['bot_choice']]}", result=f'{result}'))
        rows = [[button(editorial_text('events.session_screen.line_14'), f"again.{sid}")],
                [button(editorial_text('events.session_screen.line_15'), f"bets.{sid}")]]
    else:
        if payload["outcome"] == "win":
            text = (editorial_text('events.session_screen.line_04'))
        else:
            text = (editorial_text('events.session_screen.line_05'))
        rows = [[button(editorial_text('events.session_screen.line_16'), f"again.{sid}")],
                [button(editorial_text('events.session_screen.line_17'), "events")]]
    if session['status']=='active' or session['game_type']=='rps':
        rows.append([InlineKeyboardButton(text=editorial_text('events.session_screen.line_18'),callback_data=personal_callback('fair',world_id,user_id))])
    return text + "\n\n" + _balance(player), InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
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
    await send_private_text_from_callback(callback, world, text, markup)


@editorial_screen
async def _valid_location(callback, world) -> bool:
    return await validate_callback_location(callback,world,callback.from_user.id)

@router.callback_query(F.data.startswith("mini:events:"))
@editorial_screen
async def events_callback(callback: CallbackQuery):
    context = await load_personal_context(callback)
    if context is None:
        return
    world, player = context
    if not await _valid_location(callback, world):
        return
    await callback.answer()
    await _show(callback, world, player)


@router.callback_query(F.data.startswith("mini:ev:"))
@editorial_screen
async def event_game_callback(callback: CallbackQuery):
    context = await load_extended_context(callback, "ev")
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
                    raise EventError(editorial_text('errors.events.handlers.event_game_callback.01'))
            elif len(parts) != 1:
                raise EventError(editorial_text('errors.events.handlers.event_game_callback.02'))
            screen = action
        elif action in {"start", "enter"}:
            if len(parts) != (3 if action == "start" else 2):
                raise EventError(editorial_text('errors.events.handlers.event_game_callback.03'))
            token = parts[1]
            if len(token) != 12 or any(c not in "0123456789abcdef" for c in token):
                raise EventError(editorial_text('errors.events.handlers.event_game_callback.04'))
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
            raise EventError(editorial_text('errors.events.handlers.event_game_callback.05'))
    except InsufficientFundsError:
        await callback.answer(editorial_text('events.event_game_callback.line_01'), show_alert=True)
        await _show(callback, world, player, screen=screen, notice=editorial_text('events.event_game_callback.line_03'))
        return
    except (EventError, ValueError, KeyError):
        await callback.answer(editorial_text('events.event_game_callback.line_02'), show_alert=True)
        return
    await callback.answer()
    await _show(callback, world, player, screen=screen, session=session)
