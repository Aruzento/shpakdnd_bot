"""One shared Telegram router for D&D and Mini stories."""

import logging

logger = logging.getLogger(__name__)
import json
from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.stories import service, enrollment, publication
from app.stories.repository import transaction
from app.stories.catalog import discover
from app.stories.presentation import render
from app.mini.ui.transport import send_private_text_from_callback

router = Router(name="stories")


def username(user):
    return "@" + user.username.lower() if user and user.username else ""


def scoped_callback(callback, sid, *, personal=False):
    with transaction() as conn:
        row = conn.execute("SELECT * FROM story_sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("История не найдена.")
        session = dict(row)
        message = callback.message
        same = (
            message
            and message.chat.id == session["chat_id"]
            and (message.message_thread_id or 0) == session["thread_id"]
        )
        if (
            not same
            and personal
            and message
            and message.chat.type == "private"
            and message.chat.id == callback.from_user.id
        ):
            same = bool(
                conn.execute(
                    "SELECT 1 FROM story_enrollment WHERE session_id=? AND user_id=?",
                    (sid, callback.from_user.id),
                ).fetchone()
            )
        if not same:
            raise ValueError("Кнопка из другого чата или темы.")
        return session


async def publish(bot, session):
    step = session["step"]
    claim = publication.claim(session["id"], step)
    if claim is None:
        return
    text, markup = render(session)
    try:
        message = await bot.send_message(
            chat_id=session["chat_id"],
            message_thread_id=session["thread_id"] or None,
            text=text,
            reply_markup=markup,
            parse_mode=None,
        )
    except BaseException:
        publication.finish(session["id"], step)
        raise
    else:
        publication.finish(session["id"], step, message.message_id)


async def refresh(bot, session, old_step=None):
    if old_step is not None and old_step != session["step"]:
        with transaction() as conn:
            old = conn.execute(
                "SELECT message_id FROM story_publications WHERE session_id=? AND step=?",
                (session["id"], old_step),
            ).fetchone()
        if old and old[0]:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=session["chat_id"], message_id=old[0], reply_markup=None
                )
            except Exception:
                logger.exception("Cannot refresh story %s", session["id"])
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM story_publications WHERE session_id=? AND step=?",
            (session["id"], session["step"]),
        ).fetchone()
    if row and row["message_id"]:
        text, markup = render(session)
        try:
            await bot.edit_message_text(
                chat_id=session["chat_id"],
                message_id=row["message_id"],
                text=text,
                reply_markup=markup,
                parse_mode=None,
            )
        except Exception:
            logger.exception("Cannot refresh story %s", session["id"])
    else:
        await publish(bot, session)


@router.message(Command("story"))
async def command(message):
    if not message.from_user:
        return
    chat = message.chat.id
    thread = message.message_thread_id or 0
    uid = message.from_user.id
    user = username(message.from_user)
    parts = (message.text or "").split()
    action = parts[1] if len(parts) > 1 else "status"
    key = f"story-command:{chat}:{message.message_id}"
    try:
        if action == "list":
            service._admin(chat, thread, uid, user)
            stories, errors = discover()
            text = "📖 Истории\n" + "\n".join(
                f"{code} — {s['title']}" for code, s in stories.items()
            )
            if not stories:
                text += "Нет валидных сценариев. Добавь JSON в STORIES_DIR."
            if errors:
                text += "\nОтклонены: " + "; ".join(
                    f"{name}: {reason}" for name, reason in errors.items()
                )
            await message.answer(text[:3900], parse_mode=None)
            return
        session = service.status(chat, thread)
        if action in ("start", "cancel", "kick"):
            if not session:
                raise ValueError("Нет открытой истории.")
            sid = session["id"]
            old_step = session["step"]
            if action == "start":
                service.start(sid, chat, thread, uid, user, key)
            elif action == "cancel":
                service.cancel(sid, chat, thread, uid, user, key)
            else:
                if len(parts) != 3 or not parts[2].isdigit():
                    raise ValueError("/story kick TELEGRAM_USER_ID")
                service.kick(sid, chat, thread, uid, user, int(parts[2]), key)
            await refresh(
                message.bot, service.status(chat, thread, session_id=sid), old_step
            )
            await message.answer("История обновлена.")
            return
        if action == "status":
            if not session:
                raise ValueError(
                    "Нет истории. /story list; /story CODE; /story start/status/cancel; /story kick USER_ID"
                )
            text, markup = render(session)
            await message.answer(text, reply_markup=markup, parse_mode=None)
            return
        if len(parts) != 2:
            raise ValueError(
                "Использование: /story CODE или /story list/start/status/cancel/kick"
            )
        created = service.create(chat, thread, uid, user, action, key)
        await publish(
            message.bot, service.status(chat, thread, session_id=created["session_id"])
        )
    except ValueError as error:
        await message.answer(str(error), parse_mode=None)


async def enrollment_menu(callback, session):
    sid = session["id"]
    uid = callback.from_user.id
    user = username(callback.from_user)
    rows = []
    enrollment.begin(sid, session["chat_id"], session["thread_id"], uid, "choose")
    with transaction() as conn:
        characters = (
            [
                dict(r)
                for r in conn.execute(
                    "SELECT thread_id,name FROM characters WHERE chat_id=? AND username=? ORDER BY thread_id",
                    (session["chat_id"], user),
                )
            ]
            if user
            else []
        )
        mini = [
            dict(r)
            for r in conn.execute(
                "SELECT p.id,p.character_name FROM mini_players p JOIN mini_worlds w ON w.id=p.world_id WHERE p.telegram_user_id=? AND w.chat_id=? AND w.enabled=1",
                (uid, session["chat_id"]),
            )
        ]
        worlds = (
            [
                dict(r)
                for r in conn.execute(
                    "SELECT id,name FROM mini_worlds WHERE chat_id=? AND enabled=1",
                    (session["chat_id"],),
                )
            ]
            if not mini
            else []
        )
    for c in characters:
        rows.append(
            [
                Button(
                    text="D&D: " + c["name"],
                    callback_data=f'story:dnd:{sid}:{c["thread_id"]}',
                )
            ]
        )
    rows.append(
        [
            Button(
                text="Создать D&D: имя → раса → класс", callback_data=f"story:new:{sid}"
            )
        ]
    )
    for account in mini:
        rows.append(
            [
                Button(
                    text="Mini: " + account["character_name"],
                    callback_data=f'story:mini:{sid}:{account["id"]}',
                )
            ]
        )
    for world in worlds:
        rows.append(
            [
                Button(
                    text="Регистрация Mini: " + world["name"],
                    callback_data=f'story:minireg:{sid}:{world["id"]}',
                )
            ]
        )
    await send_private_text_from_callback(
        callback,
        session,
        "Выбери настоящего персонажа D&D или Mini-аккаунт для этой истории.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("story:"))
async def callback_action(callback):
    try:
        parts = callback.data.split(":")
        action = parts[1]
        sid = int(parts[2])
        uid = callback.from_user.id
        personal = action in ("join", "new", "dnd", "mini", "minireg")
        session = scoped_callback(callback, sid, personal=personal)
        chat = session["chat_id"]
        thread = session["thread_id"]
        user = username(callback.from_user)
        key = "story-callback:" + callback.id
        old_step = session["step"]
        if action == "join":
            if session["status"] != "registration":
                raise ValueError("Регистрация закрыта.")
            await callback.answer()
            await enrollment_menu(callback, session)
            return
        if action == "new":
            if not user:
                raise ValueError("Добавь username Telegram для D&D или выбери Mini.")
            enrollment.begin(
                sid,
                chat,
                thread,
                uid,
                "dnd",
                input_scope=(
                    (uid, 0) if callback.message.chat.type == "private" else None
                ),
            )
            await callback.answer()
            await callback.message.answer(
                "Быстрое создание D&D-персонажа: введи имя. Затем попрошу расу и класс."
            )
            return
        if action == "minireg":
            from app.mini.registration import begin

            wid = int(parts[3])
            with transaction() as conn:
                world = conn.execute(
                    "SELECT * FROM mini_worlds WHERE id=? AND chat_id=?", (wid, chat)
                ).fetchone()
            if world is None:
                raise ValueError("Этот Mini-мир не принадлежит чату истории.")
            token = begin(wid, uid)
            bot = await callback.bot.get_me()
            url = f"https://t.me/{bot.username}?start=mini_reg_{token}"
            await callback.answer()
            await send_private_text_from_callback(
                callback,
                session,
                "Зарегистрируйся в Mini. После регистрации появится возврат к истории.",
                InlineKeyboardMarkup(
                    inline_keyboard=[[Button(text="Зарегистрироваться", url=url)]]
                ),
            )
            return
        if action == "dnd":
            service.join(
                sid,
                chat,
                thread,
                uid,
                "dnd",
                {"thread_id": int(parts[3])},
                key,
                username=user,
            )
        elif action == "mini":
            service.join(
                sid,
                chat,
                thread,
                uid,
                "mini",
                {"id": int(parts[3])},
                key,
                username=user,
            )
        elif action == "leave":
            service.leave(sid, chat, thread, uid, key)
        elif action == "start":
            service.start(sid, chat, thread, uid, user, key)
        elif action == "vote":
            step, index = int(parts[3]), int(parts[4])
            if session["status"] != "active" or step != session["step"]:
                raise ValueError("Устаревший этап истории.")
            story = json.loads(session["snapshot_json"])
            choices = story["nodes"][session["node_id"]]["choices"]
            if not 0 <= index < len(choices):
                raise ValueError("Недопустимый вариант.")
            service.vote(sid, chat, thread, uid, step, choices[index]["id"], key)
        else:
            raise ValueError("Неизвестная кнопка истории.")
        await callback.answer("Сохранено.")
        await refresh(
            callback.bot, service.status(chat, thread, session_id=sid), old_step
        )
    except (ValueError, IndexError) as error:
        await callback.answer(str(error), show_alert=True)


class PendingStoryCharacter(Filter):
    async def __call__(self, message):
        if not message.from_user or not message.text or message.text.startswith("/"):
            return False
        return bool(
            enrollment.pending(
                message.chat.id, message.message_thread_id or 0, message.from_user.id
            )
        )


@router.message(PendingStoryCharacter())
async def quick_character(message):
    try:
        result = enrollment.input_text(
            message.chat.id,
            message.message_thread_id or 0,
            message.from_user.id,
            username(message.from_user),
            message.text,
            operation_key=f"story-text:{message.chat.id}:{message.message_id}",
        )
        stage = result["stage"]
        chat, thread = result["chat_id"], result["thread_id"]
        if stage == "done":
            service.join(
                result["session_id"],
                chat,
                thread,
                message.from_user.id,
                "dnd",
                {"thread_id": thread},
                f"story-enroll:{result['session_id']}:{message.from_user.id}",
                username=username(message.from_user),
            )
            await message.answer("Настоящий D&D-персонаж создан и записан в историю.")
            await refresh(
                message.bot,
                service.status(
                    chat,
                    thread,
                    session_id=result["session_id"],
                ),
            )
        else:
            await message.answer(
                "Введи расу персонажа." if stage == "race" else "Введи класс персонажа."
            )
    except ValueError as error:
        await message.answer(str(error))


async def recover_pending(bot):
    """Only unsent current nodes are retried; sending/uncertain is never resent."""
    with transaction() as conn:
        targets = [
            dict(r)
            for r in conn.execute(
                "SELECT s.id,s.chat_id,s.thread_id FROM story_sessions s JOIN story_publications p ON p.session_id=s.id AND p.step=s.step WHERE p.status='pending'"
            )
        ]
    for row in targets:
        try:
            await publish(
                bot,
                service.status(row["chat_id"], row["thread_id"], session_id=row["id"]),
            )
        except Exception:
            logger.exception("Story pending publication recovery failed: %s", row["id"])
