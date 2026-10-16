"""Pure home presentation shared by topic and private registration."""
from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from app.mini.ui.context import personal_callback
from app.mini.presentation import hero_heading,hero_trait_lines



@editorial_screen
def player_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    def b(label,action,**kwargs):
        return InlineKeyboardButton(text=label,callback_data=personal_callback(action,world_id,user_id),**kwargs)
    return InlineKeyboardMarkup(inline_keyboard=[
        [b(editorial_text('home.player_menu.line_01'),'adventures'),b(editorial_text('home.player_menu.line_02'),'heroarea')],
        [b(editorial_text('gacha.summon'),'gacha',style='success')],
        [b(editorial_text('home.player_menu.line_04'),'fair'),b(editorial_text('home.player_menu.line_05'),'more')],
    ])



@editorial_screen
def home_content(player: dict, active: dict | None) -> str:
    if active is None:
        lines = [editorial_text('home.home_content.line_01'), editorial_text('home.home_content.line_02')]
    else:
        lines = [
            hero_heading(active), "", *hero_trait_lines(active),
            editorial_text('home.home_content.line_03', value_1=f"{int(active.get('attack', 1))}"),
        ]
    return "\n".join([
        *lines, "",
        editorial_text('home.home_content.line_04', coins=f"{int(player['coins'])}", shards=f"{int(player['shards'])}"),
    ])
