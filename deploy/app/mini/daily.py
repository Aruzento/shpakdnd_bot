import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.config import DB_PATH, TIMEZONE


ENEMIES = (
    "гоблина",
    "кобольда",
    "скелета",
    "зомби",
    "разбойника",
    "гигантскую крысу",
    "сердитого мимика",
    "очень маленького огра",
    "слишком уверенного в себе культиста",
    "волка, который явно что-то задумал",
    "гремлина",
    "гарпию",
    "оживший доспех",
    "пьяного орка",
    "бандита в подозрительно хорошем плаще",
    "гоблина-бухгалтера",
    "кобольда с кастрюлей вместо шлема",
    "скелета с ржавой саблей",
    "злого болотного духа",
    "неприятно бодрого упыря",
    "летающий меч",
    "дворфа-разбойника",
    "мага-недоучку",
    "разъярённого кабана",
    "слизь размером с хороший арбуз",
)

OPENINGS = (
    "{name} спокойно шёл по своим делам, пока не встретил {enemy}.",
    "{name} решил, что сегодня будет тихий день. {enemy_cap} решил иначе.",
    "На пути {name} внезапно появился {enemy}. Начало дня сразу стало интереснее.",
    "{name} услышал подозрительный шум. Источником оказался {enemy}.",
    "{name} заглянул за угол и обнаружил там {enemy}. Отступать было уже неловко.",
    "Планы {name} на спокойную прогулку испортил {enemy}.",
    "{name} нашёл сундук. Возле сундука, конечно же, стоял {enemy}.",
    "{name} почти прошёл мимо опасности, но {enemy} слишком выразительно посмотрел вслед.",
    "Утро {name} началось с кофе, а продолжилось встречей с {enemy}.",
    "{name} услышал боевой клич. Через секунду показался {enemy}.",
    "{name} обнаружил на дороге {enemy}. Дорога была одна.",
    "{name} решил сократить путь через переулок. Там уже ждал {enemy}.",
    "Кто-то крикнул «держи его!». Через мгновение перед {name} оказался {enemy}.",
    "{name} нашёл подозрительные следы и зачем-то пошёл по ним. В конце был {enemy}.",
    "{name} увидел блеск монеты на земле. Рядом почему-то прятался {enemy}.",
    "{name} услышал хруст ветки. Потом ещё один. Потом появился {enemy}.",
    "{name} случайно наступил кому-то на ногу. Этим кем-то оказался {enemy}.",
    "{name} открыл не ту дверь и встретил {enemy}. Дверь за спиной сразу захлопнулась.",
    "{name} решил проверить старую легенду. Легенда оказалась {enemy}.",
    "{name} просто хотел перекусить, но еду уже делил {enemy}.",
)

TWISTS = (
    "Противник выглядел грозно, но начал бой с того, что чуть не споткнулся.",
    "Сражение оказалось серьёзнее, чем хотелось бы признавать.",
    "Первый обмен ударами ничего не решил, зато сильно испортил настроение обеим сторонам.",
    "В какой-то момент стало непонятно, кто на кого вообще напал.",
    "Противник попытался произнести эффектную угрозу и забыл последнюю половину фразы.",
    "Бой быстро превратился в смесь тактики, импровизации и сомнительных решений.",
    "Один особенно красивый удар ушёл строго мимо, но выглядел убедительно.",
    "Противник оказался крепче, чем выглядел. К счастью, умнее он не оказался.",
    "Где-то рядом кто-то начал делать ставки.",
    "Ситуация выглядела плохо ровно до того момента, пока противник не совершил очевидную ошибку.",
    "Пыль стояла столбом, достоинство — под вопросом, но бой продолжался.",
    "План был отличный. Правда, придумать его удалось уже после первого удара.",
    "Противник явно готовился к этой встрече. {name} — явно нет.",
    "На секунду показалось, что всё закончено. Оказалось — это была только середина.",
    "Особенно напряжённым моментом стала попытка понять, куда делось оружие.",
    "Противник сделал страшное лицо. Это действительно немного помогло.",
    "Тактическое отступление быстро сменилось тактическим возвращением.",
    "Бой сопровождался звуками, которые никто из участников потом не смог объяснить.",
    "В какой-то момент победа зависела исключительно от наглости.",
    "Сражение было достойно баллады. Очень короткой и довольно глупой баллады.",
)

FINISHES = (
    "{name} победил и постарался выглядеть так, будто всё шло по плану.",
    "В итоге {name} остался стоять, а противник — уже нет.",
    "{name} одержал победу. Свидетели договорились не обсуждать детали.",
    "Последний удар решил исход боя. {name} победил.",
    "Противник отступил. {name} благоразумно не стал уточнять почему.",
    "{name} победил благодаря мастерству, удаче и одному очень спорному манёвру.",
    "Через несколько минут всё закончилось победой {name}. И небольшим бардаком.",
    "{name} выиграл бой и даже сохранил большую часть достоинства.",
    "Победа досталась {name}. Окрестности пережили это хуже.",
    "{name} вышел победителем и немедленно объявил произошедшее частью плана.",
    "Противник был повержен. {name} решил не задерживаться для реванша.",
    "{name} победил. Никто не пострадал сильнее, чем гордость противника.",
    "Бой закончился в пользу {name}. Именно так это и запишут в хрониках.",
    "{name} поставил эффектную точку в этом совершенно неэффектном приключении.",
    "Победа! {name} получил право уйти отсюда с высоко поднятой головой.",
    "{name} справился и на всякий случай быстро покинул место событий.",
    "Противник сдался первым. {name} решил считать это безоговорочной победой.",
    "{name} выиграл. Тактика останется секретом даже для самого {name}.",
    "Сражение закончилось победой {name} и глубокими вопросами к устройству мира.",
    "{name} победил. Где-то далеко мастер довольно кивнул.",
)

RARE_ENDINGS = (
    "✨ Всё закончилось неожиданно удачно: у противника обнаружился набитый монетами кошель.",
    "✨ Победа оказалась особенно прибыльной — рядом нашёлся забытый тайник.",
    "✨ Сегодня удача явно была на стороне {name}: добыча оказалась заметно богаче обычной.",
    "✨ После боя {name} обнаружил спрятанный кошель. Его прежнему владельцу он уже не понадобится.",
    "✨ Противник оказался участником какого-то очень плохо организованного ограбления. Монеты достались победителю.",
)

LEGENDARY_ENDINGS = (
    "🌟 Сегодня сама судьба решила выдать премию. {name} нашёл небольшой клад и очень большой повод вернуться завтра.",
    "🌟 Невероятная удача! После боя обнаружился тайник, который явно ждал именно этого дня.",
    "🌟 Это был тот самый один день из сотни, когда приключение внезапно оказалось очень прибыльным.",
)


def _today_string() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def _normalize_claim_date(value: date | str | None) -> str:
    if value is None:
        return _today_string()

    if isinstance(value, date):
        return value.isoformat()

    return str(value)


def _pick_reward() -> tuple[str, int]:
    roll = secrets.randbelow(100)

    if roll == 0:
        return "legendary", 15

    if roll < 10:
        return "rare", 7 + secrets.randbelow(4)

    return "common", 3 + secrets.randbelow(4)


def _build_story(character_name: str, rarity: str) -> tuple[str, str]:
    enemy = secrets.choice(ENEMIES)
    enemy_cap = enemy[0].upper() + enemy[1:]

    opening_index = secrets.randbelow(len(OPENINGS))
    twist_index = secrets.randbelow(len(TWISTS))
    finish_index = secrets.randbelow(len(FINISHES))

    values = {
        "name": character_name,
        "enemy": enemy,
        "enemy_cap": enemy_cap,
    }

    opening = OPENINGS[opening_index].format(**values)
    twist = TWISTS[twist_index].format(**values)
    finish = FINISHES[finish_index].format(**values)

    extra = ""

    if rarity == "rare":
        extra = "\n\n" + secrets.choice(RARE_ENDINGS).format(**values)
    elif rarity == "legendary":
        extra = "\n\n" + secrets.choice(LEGENDARY_ENDINGS).format(**values)

    encounter_key = (
        f"{rarity}:"
        f"{ENEMIES.index(enemy)}:"
        f"{opening_index}:"
        f"{twist_index}:"
        f"{finish_index}"
    )

    story = f"{opening}\n\n{twist}\n\n{finish}{extra}"
    return encounter_key, story


def get_daily_claim(
    player_id: int,
    claim_date: date | str | None = None,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    day = _normalize_claim_date(claim_date)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT
                id,
                player_id,
                claim_date,
                encounter_key,
                story_text,
                coins_earned,
                item_id,
                created_at
            FROM mini_daily_claims
            WHERE player_id = ?
              AND claim_date = ?
            """,
            (
                int(player_id),
                day,
            ),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["rarity"] = (
        result["encounter_key"].split(":", 1)[0]
        if result["encounter_key"]
        else "common"
    )
    return result


def claim_daily(
    player_id: int,
    character_name: str,
    claim_date: date | str | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    """
    Выдаёт дейлик максимум один раз за календарный день в BOT_TIMEZONE.

    История, начисление монет и запись в кошелёк выполняются одной
    SQLite-транзакцией. Повторное нажатие возвращает тот же результат,
    но повторно монеты не начисляет.
    """
    day = _normalize_claim_date(claim_date)
    character_name = " ".join(character_name.strip().split())

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT
                id,
                claim_date,
                encounter_key,
                story_text,
                coins_earned,
                created_at
            FROM mini_daily_claims
            WHERE player_id = ?
              AND claim_date = ?
            """,
            (
                int(player_id),
                day,
            ),
        ).fetchone()

        player_row = conn.execute(
            """
            SELECT coins
            FROM mini_players
            WHERE id = ?
            """,
            (int(player_id),),
        ).fetchone()

        if player_row is None:
            conn.rollback()
            raise ValueError("Mini-игрок не найден.")

        current_balance = int(player_row["coins"])

        if existing is not None:
            conn.commit()

            encounter_key = str(existing["encounter_key"])
            return {
                "claimed": False,
                "claim_id": int(existing["id"]),
                "claim_date": str(existing["claim_date"]),
                "encounter_key": encounter_key,
                "rarity": encounter_key.split(":", 1)[0],
                "story_text": str(existing["story_text"]),
                "coins_earned": int(existing["coins_earned"]),
                "balance": current_balance,
            }

        rarity, coins = _pick_reward()
        encounter_key, story = _build_story(
            character_name,
            rarity,
        )

        claim_cursor = conn.execute(
            """
            INSERT INTO mini_daily_claims (
                player_id,
                claim_date,
                encounter_key,
                story_text,
                coins_earned
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(player_id),
                day,
                encounter_key,
                story,
                coins,
            ),
        )

        claim_id = int(claim_cursor.lastrowid)
        new_balance = current_balance + coins

        conn.execute(
            """
            UPDATE mini_players
            SET coins = ?
            WHERE id = ?
            """,
            (
                new_balance,
                int(player_id),
            ),
        )

        conn.execute(
            """
            INSERT INTO mini_wallet_transactions (
                player_id,
                amount,
                balance_after,
                reason,
                reference_type,
                reference_id,
                operation_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(player_id),
                coins,
                new_balance,
                "Ежедневное приключение",
                "daily",
                claim_id,
                f"daily:{player_id}:{day}",
            ),
        )

        conn.commit()

    return {
        "claimed": True,
        "claim_id": claim_id,
        "claim_date": day,
        "encounter_key": encounter_key,
        "rarity": rarity,
        "story_text": story,
        "coins_earned": coins,
        "balance": new_balance,
    }
