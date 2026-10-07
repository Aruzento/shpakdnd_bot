from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.mini.superadmin.access import is_superadmin
from app.mini.superadmin.parser import parse_command
from app.mini.superadmin.service import execute

router=Router(name="mini_superadmin")


@router.message(Command("superlook","superadd","superdel","superchars","superluck"))
async def superadmin_handler(message: Message):
    if not message.from_user or not is_superadmin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только владельцу D&D Mini.")
        return
    try:
        command=parse_command(message.text)
        response=execute(message.from_user.id,command,
            operation_key=f"super:{message.chat.id}:{message.message_id}")
    except ValueError as error:
        await message.answer(f"❌ {error}")
        return
    # Inventory and hero collections can exceed Telegram's message limit.
    chunk=''
    for line in response.splitlines():
        if len(chunk)+len(line)+1>3500:
            await message.answer(chunk);chunk=''
        chunk += line+'\n'
    if chunk: await message.answer(chunk.rstrip())
