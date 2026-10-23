"""Personal Mythic inventory/care UI; actions always revalidate in services."""

from app.mini.editorial import text as editorial_text, screen as editorial_screen
import json
import secrets
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.ui.context import (
    load_personal_context,
    load_extended_context,
    personal_callback,
)
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.mythic import pets
from app.mini.mythic.wallet import transaction

router = Router(name="mini_mythic")
from app.mini.editorial.registry import LiveMapping

NAMES = LiveMapping({key: "v15.element." + key for key in pets.ELEMENTS})
STAGES = LiveMapping(
    {key: "v15.pet.stage." + key for key in ("egg", "wonder", "adult")}
)
CARE_NAMES = LiveMapping({key: "v15.pet.care." + key for key in pets.CARE})


def button(world, user, label, payload):
    data = f"mini:myth:{world}:{user}:{payload}"
    if len(data.encode()) > 64:
        raise ValueError("Слишком длинный callback.")
    return Button(text=label, callback_data=data)


@editorial_screen
def screen(pid, wid, uid, *, view="home", pet_id=None):
    state = pets.state(pid)
    rows = []
    token = secrets.token_hex(3)
    b = lambda label, payload: button(wid, uid, label, payload)
    text = editorial_text("v15.mythic.balance", balance=state["mythic_shards"])
    locations = {"inventory": "Инвентарь", "active": "Активный", "reserve": "Резерв"}
    if view == "home":
        active = next((p for p in state["pets"] if p["location"] == "active"), None)
        text += "\n" + (
            f"Активен: {NAMES[active['element']]} · {STAGES[active['stage']]} · настроение {active['mood']}%"
            if active
            else "Активного зверя нет. Полученные яйца находятся в инвентаре."
        )
        for pet in state["pets"]:
            label = f"{NAMES[pet['element']]} · {STAGES[pet['stage']]} · {locations[pet['location']]}"
            rows.append([b(label, f'pet.{pet["id"]}')])
        if not state["pets"]:
            text += (
                "\nПервое бесплатное яйцо — за первый успешный удар в мифическом рейде."
            )
        if 0 < len(state["pets"]) < 8:
            rows.append([b("Купить случайное новое яйцо — 75", f"buyask.{token}")])
        rows.append(
            [Button(text="👹 Мифический рейд", callback_data=f"mini:raids:{wid}:{uid}")]
        )
    else:
        pet = next((p for p in state["pets"] if p["id"] == pet_id), None)
        if not pet:
            raise ValueError("Зверь не найден.")
        text += f"\n{NAMES[pet['element']]} · {STAGES[pet['stage']]}\nНастроение: {pet['mood']}%\nРасположение: {locations[pet['location']]}\nЗачтено роста: {pet['stage_seconds']//3600} ч."
        if pet["evolution_pending"]:
            text += "\nЭволюция ожидает реального каталога и изображения героя."
        if pet["location"] != "active":
            rows.append([b("Сделать активным", f"activate.{pet_id}.{token}")])
        else:
            cds = json.loads(pet["cooldowns_json"])
            for action, label in CARE_NAMES.items():
                left = max(0, cds.get(action, 0) - pet["active_seconds"])
                rows.append(
                    [
                        b(
                            label
                            + (f" · {left//3600}ч {left%3600//60}м" if left else ""),
                            f"care.{pet_id}.{action}.{token}",
                        )
                    ]
                )
            if pet["stage"] == "wonder":
                rows.append(
                    [b("Поглотить ненадетую экипировку", f"equipment.{pet_id}")]
                )
            if pet["stage"] == "adult":
                text += f"\nИзменение урона Boss/Raid: {pet['mood']-50:+}"
                rows.append([b("Получить 1 мифический осколок", f"claim.{token}")])
                if pet["hero_id"]:
                    rows.append(
                        [
                            Button(
                                text="Боевой герой",
                                callback_data=f'mini:hero:{wid}:{uid}:{pet["hero_id"]}',
                            )
                        ]
                    )
        if pet["location"] != "reserve":
            rows.append([b("Убрать в резерв", f"reserve.{pet_id}.{token}")])
        rows.append([b(editorial_text("navigation.back"), "home")])
    rows.append(
        [
            Button(
                text=editorial_text("navigation.home"),
                callback_data=personal_callback("home", wid, uid),
            )
        ]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("mini:mythic:"))
async def home(callback):
    context = await load_personal_context(callback)
    if not context:
        return
    world, player = context
    await callback.answer()
    text, markup = screen(player["id"], world["id"], callback.from_user.id)
    await send_private_text_from_callback(callback, world, text, markup)


@router.callback_query(F.data.startswith("mini:myth:"))
async def action(callback):
    context = await load_extended_context(callback, "myth")
    if not context:
        return
    world, player, payload = context
    parts = payload.split(".")
    op = parts[0]
    pid = player["id"]
    wid = world["id"]
    uid = callback.from_user.id
    key = f"myth:{wid}:{uid}:{payload}"
    b = lambda label, value: button(wid, uid, label, value)
    try:
        if op == "buyask":
            text = "Потратить 75 мифических осколков на случайную ещё не полученную стихию? Яйцо попадёт в инвентарь."
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [b("Подтвердить", "buy." + parts[1])],
                    [b("Отмена", "home")],
                ]
            )
        elif op == "equipment":
            pet_id = int(parts[1])
            rows = []
            with transaction() as conn:
                items = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT e.rowid AS item_id,e.name,e.code,o.quantity-(SELECT COUNT(*) FROM mini_equipment_slots s WHERE s.player_id=o.player_id AND s.code=o.code) AS quantity FROM mini_equipment_owned o JOIN mini_equipment e ON e.code=o.code WHERE o.player_id=? AND o.quantity>(SELECT COUNT(*) FROM mini_equipment_slots s WHERE s.player_id=o.player_id AND s.code=o.code) ORDER BY e.code",
                        (pid,),
                    )
                ]
            page = max(0, int(parts[2])) if len(parts) > 2 else 0
            page = min(page, max(0, (len(items) - 1) // 8))
            for item in items[page * 8 : (page + 1) * 8]:
                rows.append(
                    [
                        b(
                            item["name"] + f" ×{item['quantity']}",
                            f'eatask.{pet_id}.{item["item_id"]}.{secrets.token_hex(3)}',
                        )
                    ]
                )
            if page:
                rows.append([b("⬅️", f"equipment.{pet_id}.{page-1}")])
            if (page + 1) * 8 < len(items):
                rows.append([b("➡️", f"equipment.{pet_id}.{page+1}")])
            rows.append([b("Назад", f"pet.{pet_id}")])
            text = "Одна ненадетая вещь → 1 мифический осколок. До 5 вещей/сутки."
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
        elif op == "eatask":
            text = "Подтвердить уничтожение одной вещи? Отменить расход после подтверждения нельзя."
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [b("Уничтожить одну вещь", "eat." + ".".join(parts[1:]))],
                    [b("Отмена", "pet." + parts[1])],
                ]
            )
        else:
            pet_id = None
            view = "home"
            if op == "buy":
                pets.buy(pid, key)
            elif op == "activate":
                pet_id = int(parts[1])
                pets.activate(pid, pet_id, key)
                view = "pet"
            elif op == "reserve":
                pet_id = int(parts[1])
                pets.reserve(pid, pet_id, key)
                view = "pet"
            elif op == "care":
                pet_id = int(parts[1])
                pets.care(pid, pet_id, parts[2], key)
                view = "pet"
            elif op == "claim":
                pets.claim(pid, key)
            elif op == "eat":
                pet_id = int(parts[1])
                with transaction() as conn:
                    row = conn.execute(
                        "SELECT code FROM mini_equipment WHERE rowid=?",
                        (int(parts[2]),),
                    ).fetchone()
                if not row:
                    raise ValueError("Вещь не найдена.")
                pets.consume(pid, pet_id, row["code"], key, confirmed=True)
                view = "pet"
            elif op == "pet":
                pet_id = int(parts[1])
                view = "pet"
            elif op != "home":
                raise ValueError("Неизвестное действие Mythic.")
            text, markup = screen(pid, wid, uid, view=view, pet_id=pet_id)
        await callback.answer()
        if op in ("pet", "care", "activate", "eat", "reserve"):
            await send_pet(callback, world, pid, int(parts[1]), text, markup)
        else:
            await send_private_text_from_callback(callback, world, text, markup)
    except (ValueError, IndexError) as error:
        await callback.answer(str(error), show_alert=True)


from app.mini.mythic.raid.handlers import router as raid_router

router.include_router(raid_router)


async def send_pet(callback, world, pid, pet_id, text, markup):
    from app.mini.mythic.content import pet_image

    state = pets.state(pid)
    pet = next((p for p in state["pets"] if p["id"] == pet_id), None)
    image = pet_image(pet["element"], pet["stage"]) if pet else None
    if image:
        from app.mini.ui.hero_cards import send_hero_card

        return await send_hero_card(
            callback,
            world,
            {"name": NAMES[pet["element"]], "image_path": image.name},
            text,
            markup,
        )
    return await send_private_text_from_callback(callback, world, text, markup)
