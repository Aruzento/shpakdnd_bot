"""Registration name input is accepted only in the owner's private chat."""
from app.mini.editorial import text as editorial_text, screen as editorial_screen
from aiogram import Router
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from app.mini import registration as service
from app.mini.players import get_mini_player
from app.mini.heroes import get_active_hero,get_hero_image
from app.mini.ui.home import player_menu,home_content
from app.mini.worlds import get_mini_world_by_id
from app.mini.ui.context import username_from_user
from app.mini.onboarding import starter_message


router=Router(name='mini_private_registration')


@editorial_screen
async def reply_home(message,world,player,prefix=''):
    active=get_active_hero(player['id'])
    text=prefix+home_content(player,active)
    markup=player_menu(world['id'],message.from_user.id)
    image=get_hero_image(active) if active else None
    if image:
        try:
            return await message.answer_photo(FSInputFile(image),caption=text,reply_markup=markup)
        except TelegramBadRequest:
            pass
    return await message.answer(text,reply_markup=markup)


@editorial_screen
async def start_private(message):
    if message.chat.type!='private' or not message.from_user:
        return
    token=message.text.split(maxsplit=1)[1].removeprefix('mini_reg_')
    try:
        request=service.activate(token,message.from_user.id)
    except ValueError as error:
        await message.answer(str(error));return
    player=get_mini_player(request['world_id'],message.from_user.id)
    if player:
        await reply_home(message,get_mini_world_by_id(request['world_id']),player)
    else:
        await message.answer(editorial_text('registration.start_private.line_01'))


class PendingName(Filter):
    async def __call__(self,message):
        if message.chat.type!='private' or not message.from_user or not message.text:
            return False
        # Do not swallow commands belonging to other routers.
        if message.text.startswith('/') and message.text!='/cancel':
            return False
        request=service.pending(message.from_user.id)
        return {'registration_request':request} if request else False


@router.message(PendingName())
@editorial_screen
async def name_message(message,registration_request):
    if message.chat.type!='private' or not message.from_user:
        return
    if message.text=='/cancel':
        service.cancel(message.from_user.id);await message.answer(editorial_text('registration.name_message.line_02'));return
    try:
        player=service.complete(registration_request['token'],message.from_user.id,
                                username_from_user(message.from_user),message.text)
    except ValueError as error:
        await message.answer(str(error));return
    world=get_mini_world_by_id(player['world_id'])
    intro=editorial_text('registration.name_message.line_01') if player['newly_created'] else ''
    gift=starter_message()+'\n\n' if player.get('onboarding_granted') else ''
    await reply_home(message,world,player,intro+gift)
