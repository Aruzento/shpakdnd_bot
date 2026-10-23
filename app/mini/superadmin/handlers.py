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


@router.message(Command("superadminch"))
async def change_boss_handler(message: Message):
    from app.mini.boss.operations import parse_change,change_boss
    from app.mini.worlds import get_mini_world
    try:
        if not message.from_user or not is_superadmin(message.from_user.id):raise ValueError('Команда доступна только superadmin Mini.')
        world=get_mini_world(message.chat.id,message.message_thread_id or 0)
        if not world:raise ValueError('Команда работает только в текущем Mini-мире.')
        bid,flag,value=parse_change(message.text)
        result=change_boss(bid,world['id'],message.from_user.id,flag,value,f'superadminch:{message.chat.id}:{message.message_id}')
    except ValueError as error:
        await message.answer(str(error));return
    await message.answer(f"Босс #{bid}: {result['field']} {result['old']} → {result['new']}. Изменение сохранено.")
    from app.mini.boss.public import refresh_public_boss
    from app.mini.boss.service import get_boss
    await refresh_public_boss(message.bot,world,get_boss(bid))
