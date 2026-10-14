"""Pure home presentation shared by topic and private registration."""
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from app.mini.ui.context import personal_callback
from app.mini.presentation import hero_heading,hero_trait_lines


def player_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    def b(label,action,**kwargs):
        return InlineKeyboardButton(text=label,callback_data=personal_callback(action,world_id,user_id),**kwargs)
    return InlineKeyboardMarkup(inline_keyboard=[
        [b('⚔️ Приключения','adventures'),b('🧙 Герой','heroarea')],
        [b('✨ Призвать героя','gacha',style='success')],
        [b('🎪 Ярмарка','fair'),b('📖 Ещё','more')],
    ])



def home_content(player: dict, active: dict | None) -> str:
    if active is None:
        lines = ["🎴 Активный герой пока не выбран.", "Призови своего первого героя."]
    else:
        lines = [
            hero_heading(active), "", *hero_trait_lines(active),
            f"⚔️ Урон: {int(active.get('attack', 1))}",
        ]
    return "\n".join([
        *lines, "",
        f"🪙 {int(player['coins'])} монет • 🧩 {int(player['shards'])} осколков",
    ])
