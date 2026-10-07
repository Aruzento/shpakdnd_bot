"""Shared Russian labels for Combat v2 cards; balance stays in catalogs."""

RARITY_EMOJI = {
    "mythic": "✨", "shadow": "🌑",
    "common": "⚪", "uncommon": "🟢", "rare": "🟣", "legendary": "🟡",
}


def rarity_emoji(rarity: str | None) -> str:
    """Shared rarity display; keep the collection's legacy white fallback."""
    return RARITY_EMOJI.get(rarity, "⚪")


FACTION_LABELS = {
    "commoners": "🧑 Простолюдины", "beasts": "🐾 Звери",
    "monsters": "👾 Монстры", "warriors": "⚔️ Воины",
    "dark": "🌑 Тьма", "neutral": "⚖️ Нейтральная",
}
DAMAGE_LABELS = {
    "slashing": "Режущий", "piercing": "Колющий",
    "bludgeoning": "Дробящий", "magic": "Магический",
}
CLASS_LABELS = {
    "none": "Без класса", "magical": "Магический класс",
    "technical": "Технический класс", "martial": "Боевой класс",
    "warrior": "Воин", "guardian": "Страж", "mage": "Маг",
    "ranger": "Следопыт", "sneaky": "Плут", "healer": "Целитель", "beast": "Зверь",
}
RANGE_LABELS = {"melee": "Ближний бой", "ranged": "Дальний бой"}
TRAIT_LABELS = {
    "none": "Нет особого свойства", "undead": "Нежить", "construct": "Конструкт",
    "flying": "Летающий", "demon": "Демон", "armored": "Бронированный",
    "holy": "Святой", "poisonous": "Ядовитый", "demonic": "Демонический",
}


def tag_label(value: str, labels: dict) -> str:
    return labels.get(value, str(value or "Нет").replace("_", " "))


def faction_label(value: str) -> str:
    return FACTION_LABELS.get(value, "❓ Неизвестная фракция")


def hero_heading(hero: dict) -> str:
    return f"{hero['name']} • {faction_label(hero.get('faction', 'commoners'))} • ⭐ {int(hero.get('stars', 0))}"


def hero_trait_lines(hero: dict) -> list[str]:
    return [
        f"{tag_label(hero.get('damage_type', 'slashing'), DAMAGE_LABELS)} | "
        f"{tag_label(hero.get('class_tag', 'none'), CLASS_LABELS)}",
        f"{tag_label(hero.get('attack_range', 'melee'), RANGE_LABELS)} | "
        f"{tag_label(hero.get('special_trait', 'none'), TRAIT_LABELS)}",
    ]


FEATURE_DESCRIPTIONS = {
    "armored": "Броня: −10% физического урона и ещё −10% за ближний бой",
    "undead": "Нежить: один раз воскресает с 25% HP",
    "construct": "Конструкт: +10% HP в конце раунда; технический герой отключает",
    "flying": "Полёт: доступен дальнему бою или летающему герою",
    "poisonous": "Яд: повреждает награду даже при потере щита",
    "holy": "Святость: +25% HP после хода; демонический герой отключает",
    "demonic": "Демоническая порча: −10 п.п. временного запаса за ход; победа снимает",
}
TRAIT_DESCRIPTIONS = {
    "undead": "+15% временного запаса награды при старте, без увеличения выплаты",
    "armored": "+1 к текущим и максимальным щитам при старте",
    "construct": "10% шанс восстановить щит после собственного хода",
    "flying": "Позволяет ближним боем попадать по летающему боссу",
    "poisonous": "25% шанс отравить после попадания: 2% максимального HP в конце хода босса",
    "holy": "Против нежити/демонов после попадания: 25% промаха следующей атаки босса",
    "demonic": "Отключает регенерацию святого босса",
    "demon": "Отключает регенерацию святого босса",
}


def format_player_mention(player, db_path=None, *, now=None, conn=None, include_title=True):
    """Format a Mini identity; usernames and authorization data stay untouched.

    Accept player rows (id/world_id/telegram_user_id), participant/event rows
    (player_id), or an identity without a database reference for plain fallback.
    An existing transaction may be supplied by combat notices.
    """
    from app.config import DB_PATH
    from app.mini.titles.service import get_active_title
    row=dict(player or {})
    username=str(row.get('username') or '').strip()
    if not username:
        return str(row.get('character_name') or row.get('hero_name') or 'Игрок')
    if not username.startswith('@'):
        username='@'+username
    player_id=row.get('player_id')
    if player_id is None and 'world_id' in row and 'telegram_user_id' in row:
        player_id=row.get('id')
    target_path=DB_PATH if db_path is None else db_path
    if include_title and player_id is None and row.get('world_id') is not None:
        from app.mini.db import connect_mini_db
        def find_id(connection):
            rows=connection.execute('SELECT id FROM mini_players WHERE world_id=? AND lower(username)=?',
                (row['world_id'],username.lower())).fetchall()
            return rows[0][0] if len(rows)==1 else None
        if conn is not None:
            player_id=find_id(conn)
        else:
            with connect_mini_db(target_path) as connection:
                player_id=find_id(connection)
    title=get_active_title(player_id,target_path,now=now,conn=conn) if include_title and player_id is not None else None
    return f'[{title}]{username}' if title else username


def faction_name(value):
    """The existing faction label without its decorative icon."""
    return faction_label(value).split(' ',1)[-1]
