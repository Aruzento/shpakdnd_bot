




from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


from app.mini.boss.catalog import list_boss_templates



from app.mini.boss.public import format_public_boss




HERO_PAGE_SIZE = 8

def format_private_boss(
    boss: dict,
    participants: list[dict],
    *,
    joined: bool,
) -> str:
    text = format_public_boss(boss, participants)
    if boss["status"] == "announced":
        text += (
            "\n\n✅ Ты записан на этого босса."
            if joined
            else "\n\nТы пока не записан."
        )
    elif boss["status"] == "fighting" and joined:
        text += "\n\n⚔️ Ты участвуешь в этом бою."
    return text



def boss_private_menu(
    world_id: int,
    user_id: int,
    boss: dict,
    *,
    joined: bool,
    is_admin: bool,
) -> InlineKeyboardMarkup:
    rows = []

    if boss["status"] == "announced":
        if joined:
            rows.append([
                InlineKeyboardButton(
                    text="🚪 Выйти из регистрации",
                    callback_data=f"miniboss:leave:{world_id}:{boss['id']}",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="⚔️ Записаться на босса",
                    callback_data=f"miniboss:join:{world_id}:{boss['id']}",
                )
            ])

    if joined and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text="🎴 Выбрать героя",
                callback_data=f"miniboss:heroes:{world_id}:{user_id}:{boss['id']}:0",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="👥 Участники",
            callback_data=f"miniboss:list:{world_id}:{boss['id']}",
        )
    ])

    if is_admin and boss["status"] == "announced":
        rows.append([
            InlineKeyboardButton(
                text="🔒 Закрыть регистрацию",
                callback_data=(
                    f"miniboss:close:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] == "ready":
        rows.append([
            InlineKeyboardButton(
                text="▶️ Начать бой",
                callback_data=(
                    f"miniboss:start:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="🔓 Открыть регистрацию снова",
                callback_data=(
                    f"miniboss:reopen:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] == "fighting":
        rows.append([
            InlineKeyboardButton(
                text="⚡ Завершить бой",
                callback_data=(
                    f"miniboss:forcefinish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text="📣 Повторить анонс",
                callback_data=(
                    f"miniboss:republish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="❌ Отменить босса",
                callback_data=(
                    f"miniboss:cancel:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ На главную",
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def no_boss_menu(
    world_id: int,
    user_id: int,
    *,
    is_admin: bool,
) -> InlineKeyboardMarkup:
    rows = []
    if is_admin:
        rows.append([
            InlineKeyboardButton(
                text="➕ Объявить босса",
                callback_data=f"miniboss:create:{world_id}:{user_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ На главную",
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def template_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for boss in list_boss_templates():
        rows.append([
            InlineKeyboardButton(
                text=f"👹 {boss['name']} • {boss['max_hp']} HP",
                callback_data=(
                    f"miniboss:preview:{world_id}:{user_id}:{boss['code']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"mini:boss:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def preview_menu(
    world_id: int,
    user_id: int,
    code: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📣 Объявить и открыть регистрацию",
                    callback_data=(
                        f"miniboss:announce:{world_id}:{user_id}:{code}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку боссов",
                    callback_data=f"miniboss:create:{world_id}:{user_id}",
                )
            ],
        ]
    )



def battle_hero_menu(world_id: int, user_id: int, boss_id: int, heroes: list[dict], page: int):
    pages = max(1, (len(heroes) + HERO_PAGE_SIZE - 1) // HERO_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = [[InlineKeyboardButton(
        text=f"{hero['name']} • {hero['rarity']}",
        callback_data=f"miniboss:hero:{world_id}:{user_id}:{boss_id}:{hero['id']}",
    )] for hero in heroes[page * HERO_PAGE_SIZE:(page + 1) * HERO_PAGE_SIZE]]
    navigation = []
    for target, label in ((page - 1, "⬅️"), (page + 1, "➡️")):
        if 0 <= target < pages:
            navigation.append(InlineKeyboardButton(
                text=label,
                callback_data=f"miniboss:heroes:{world_id}:{user_id}:{boss_id}:{target}",
            ))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(
        text="⬅️ К боссу", callback_data=f"mini:boss:{world_id}:{user_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

