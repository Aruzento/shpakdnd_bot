from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton



from aiogram.types import InlineKeyboardMarkup
from app.mini.boss.catalog import list_boss_templates
from app.mini.boss.public import format_public_boss
from app.mini.presentation import hero_heading, hero_trait_lines



HERO_PAGE_SIZE = 8

@editorial_screen
def format_private_boss(
    boss: dict,
    participants: list[dict],
    *,
    joined: bool,
) -> str:
    text = format_public_boss(boss, participants)
    if boss["status"] == "announced":
        text += (
            editorial_text('boss.format_private_boss.line_01')
            if joined
            else editorial_text('boss.format_private_boss.line_02')
        )
    elif boss["status"] == "fighting" and joined:
        text += editorial_text('boss.format_private_boss.line_03')
    return text


@editorial_screen
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
                    text=editorial_text('boss.boss_private_menu.line_10'),
                    callback_data=f"miniboss:leave:{world_id}:{boss['id']}",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=editorial_text('boss.boss_private_menu.line_11'),
                    callback_data=f"miniboss:join:{world_id}:{boss['id']}",
                )
            ])

    if joined and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_03'),
                callback_data=f"miniboss:heroes:{world_id}:{user_id}:{boss['id']}:0",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=editorial_text('boss.boss_private_menu.line_01'),
            callback_data=f"miniboss:list:{world_id}:{boss['id']}",
        )
    ])

    if (is_admin or boss.get("created_by_user_id")==user_id) and boss["status"] == "announced":
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_04'),
                callback_data=(
                    f"miniboss:close:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if (is_admin or joined) and boss["status"] == "ready":
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_05'),
                callback_data=(
                    f"miniboss:start:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
    if (is_admin or boss.get("created_by_user_id")==user_id) and boss["status"]=="ready":
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_06'),
                callback_data=(
                    f"miniboss:reopen:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] == "fighting":
        from app.mini.boss.operations import turn_token
        rows.append([InlineKeyboardButton(text='⏭ Пропустить ход',callback_data=f"miniboss:skip:{world_id}:{boss['id']}:{turn_token(boss)}")])
    if is_admin and boss["status"] == "fighting":
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_07'),
                callback_data=(
                    f"miniboss:forcefinish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_08'),
                callback_data=(
                    f"miniboss:republish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
    if (is_admin or boss.get('created_by_user_id')==user_id) and boss['status'] in {'announced','ready'}:
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.boss_private_menu.line_09'),
                callback_data=(
                    f"miniboss:cancel:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=editorial_text('boss.boss_private_menu.line_02'),
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
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
                text=editorial_text('boss.no_boss_menu.line_02'),
                callback_data=f"miniboss:create:{world_id}:{user_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=editorial_text('boss.no_boss_menu.line_01'),
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
def template_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for boss in list_boss_templates():
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('boss.template_menu.line_02', name=f"{boss['name']}", max_hp=f"{boss['max_hp']}"),
                callback_data=(
                    f"miniboss:preview:{world_id}:{user_id}:{boss['code']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=editorial_text('boss.template_menu.line_01'),
            callback_data=f"mini:boss:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
def preview_menu(
    world_id: int,
    user_id: int,
    code: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('boss.preview_menu.line_01'),
                    callback_data=(
                        f"miniboss:announce:{world_id}:{user_id}:{code}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=editorial_text('boss.preview_menu.line_02'),
                    callback_data=f"miniboss:create:{world_id}:{user_id}",
                )
            ],
        ]
    )


@editorial_screen
def battle_hero_menu(world_id: int, user_id: int, boss_id: int, heroes: list[dict], page: int):
    # Compatibility export; live entry screens show favorites via the same renderer.
    from app.mini.ui.hero_selector import render_selector
    return render_selector(heroes,[],'b',world_id,user_id,boss_id,view='all',page=page)[1]


@editorial_screen
def selection_details(heroes: list[dict], page: int) -> str:
    return editorial_text('boss.selection_details.line_01')
