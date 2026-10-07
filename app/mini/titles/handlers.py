from app import config
from aiogram import Router
from aiogram.filters import Command
from app.context import check_topic_admin_permission, get_thread_id
from app.mini.worlds import ensure_configured_mini_world
from app.mini.presentation import format_player_mention
from app.mini.titles.service import parse_command, issue_title, get_title_target

router=Router(name='mini_titles')


@router.message(Command('supertitle'))
async def supertitle_handler(message):
    if message.from_user is None:
        return
    db_path=config.DB_PATH
    world=ensure_configured_mini_world(message.chat.id,get_thread_id(message),db_path)
    if not world or not world['enabled']:
        return
    if not await check_topic_admin_permission(message):
        return
    try:
        target,duration,text=parse_command(message.text)
        player=get_title_target(world['id'],target,db_path)
        if player is None:
            raise ValueError('Mini-игрок не найден в этой теме.')
        issue_title(player['id'],text,duration,message.from_user.id,
                    f'title:{message.chat.id}:{message.message_id}',db_path)
    except ValueError as error:
        await message.answer(f'❌ {error}')
        return
    await message.answer(f'👑 {format_player_mention(player,db_path)} — титул на {duration//86400} дн.')
