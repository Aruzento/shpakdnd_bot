from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.registry import LiveMapping, glossary

"""Shared Russian labels for Combat v2 cards; balance stays in catalogs."""

RARITY_EMOJI = LiveMapping({k: "rarity."+k for k in ('mythic', 'shadow', 'common', 'uncommon', 'rare', 'legendary')}, terms=True, variant="emoji")


@editorial_screen
def rarity_emoji(rarity: str | None) -> str:
    """Shared rarity display; keep the collection's legacy white fallback."""
    return RARITY_EMOJI.get(rarity, "⚪")


FACTION_LABELS = LiveMapping({'commoners': 'faction.commoners', 'beasts': 'faction.beasts', 'monsters': 'faction.monsters', 'warriors': 'faction.warriors', 'dark': 'faction.dark', 'neutral': 'faction.neutral'}, terms=True, decorated=True)
DAMAGE_LABELS = LiveMapping({'slashing': 'damage.slashing', 'piercing': 'damage.piercing', 'bludgeoning': 'damage.bludgeoning', 'magic': 'damage.magic'}, terms=True)
CLASS_LABELS = LiveMapping({'none': 'class.none', 'magical': 'class.magical', 'technical': 'class.technical', 'martial': 'class.martial', 'warrior': 'class.warrior', 'guardian': 'class.guardian', 'mage': 'class.mage', 'ranger': 'class.ranger', 'sneaky': 'class.sneaky', 'healer': 'class.healer', 'beast': 'class.beast'}, terms=True)
RANGE_LABELS = LiveMapping({'melee': 'range.melee', 'ranged': 'range.ranged'}, terms=True)
TRAIT_LABELS = LiveMapping({'none': 'trait.none', 'undead': 'trait.undead', 'construct': 'trait.construct', 'flying': 'trait.flying', 'demon': 'trait.demon', 'armored': 'trait.armored', 'holy': 'trait.holy', 'poisonous': 'trait.poisonous', 'demonic': 'trait.demonic'}, terms=True)


@editorial_screen
def tag_label(value: str, labels: dict) -> str:
    return labels.get(value, str(value or editorial_text('presentation.tag_label.line_01')).replace("_", " "))


@editorial_screen
def faction_label(value: str) -> str:
    return FACTION_LABELS.get(value, editorial_text('presentation.faction_label.line_01'))


@editorial_screen
def hero_heading(hero: dict) -> str:
    return editorial_text('presentation.hero_heading.line_01', name=f"{hero['name']}", value_2=f"{faction_label(hero.get('faction', 'commoners'))}", value_3=f"{int(hero.get('stars', 0))}")


@editorial_screen
def hero_trait_lines(hero: dict) -> list[str]:
    return [
        f"{tag_label(hero.get('damage_type', 'slashing'), DAMAGE_LABELS)} | "
        f"{tag_label(hero.get('class_tag', 'none'), CLASS_LABELS)}",
        f"{tag_label(hero.get('attack_range', 'melee'), RANGE_LABELS)} | "
        f"{tag_label(hero.get('special_trait', 'none'), TRAIT_LABELS)}",
    ]


FEATURE_DESCRIPTIONS = LiveMapping({'armored': 'presentation.feature_descriptions.armored', 'undead': 'presentation.feature_descriptions.undead', 'construct': 'presentation.feature_descriptions.construct', 'flying': 'presentation.feature_descriptions.flying', 'poisonous': 'presentation.feature_descriptions.poisonous', 'holy': 'presentation.feature_descriptions.holy', 'demonic': 'presentation.feature_descriptions.demonic'})
TRAIT_DESCRIPTIONS = LiveMapping({'undead': 'presentation.trait_descriptions.undead', 'armored': 'presentation.trait_descriptions.armored', 'construct': 'presentation.trait_descriptions.construct', 'flying': 'presentation.trait_descriptions.flying', 'poisonous': 'presentation.trait_descriptions.poisonous', 'holy': 'presentation.trait_descriptions.holy', 'demonic': 'presentation.trait_descriptions.demonic', 'demon': 'presentation.trait_descriptions.demon'})


@editorial_screen
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
        return str(row.get('character_name') or row.get('hero_name') or editorial_text('presentation.format_player_mention.line_01'))
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


@editorial_screen
def faction_name(value):
    """The existing faction label without its decorative icon."""
    return glossary("faction."+value) if value in FACTION_LABELS else editorial_text("presentation.unknown_faction")
