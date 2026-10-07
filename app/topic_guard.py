"""One boundary for configured ordinary D&D topics and Mini worlds."""
from app.context import get_thread_id
from app.topics import TOPIC_SETTINGS


def is_mini_topic(chat_id,thread_id):
    return bool(TOPIC_SETTINGS.get(chat_id,{}).get(thread_id,{}).get("mini",False))


def require_mini_topic(chat_id,thread_id):
    if not is_mini_topic(chat_id,thread_id):
        raise ValueError("Эта тема не работает в режиме D&D Mini.")


async def allow_dnd(message,chat_id=None,thread_id=None):
    chat_id=message.chat.id if chat_id is None else chat_id
    thread_id=get_thread_id(message) if thread_id is None else thread_id
    if is_mini_topic(chat_id,thread_id):
        await message.answer("❌ Эта тема используется D&D Mini. Для управления Mini используй /super... или личное меню.")
        return False
    return True
