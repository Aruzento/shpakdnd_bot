from app.mini.effects.contracts import EffectDefinition
from app.mini.effects.builtins import coin_pouch, shard_casket, charged_effect, village_boost, equipment_chest

EFFECT_GACHA_TICKET = "gacha_ticket"

EFFECT_GACHA_LUCK = "gacha_luck"

EFFECT_BOSS_DAMAGE = "boss_damage_boost"

EFFECT_BOSS_PHANTOM = "boss_phantom_participation"

EFFECT_COIN_POUCH = "boss_coin_pouch"

EFFECT_SHARD_CASKET = "boss_shard_casket"


_EFFECTS: dict[str, EffectDefinition] = {}


def register_effect(definition: EffectDefinition) -> None:
    if not isinstance(definition.key, str) or not definition.key.strip() or definition.key != definition.key.strip():
        raise ValueError("Effect key must be a nonempty, trimmed string.")
    if definition.key in _EFFECTS:
        raise ValueError(f"Effect already registered: {definition.key}")
    if not isinstance(definition.description, str) or not definition.description.strip():
        raise ValueError("Effect description must be nonempty.")
    if definition.handler is not None and not callable(definition.handler):
        raise ValueError("Effect use handler must be callable.")
    _EFFECTS[definition.key] = definition


def get_effect(key: str) -> EffectDefinition | None:
    return _EFFECTS.get(key)


def validate_effect_key(key: str, *, allow_empty: bool = True) -> None:
    if allow_empty and key == "":
        return
    if not isinstance(key, str) or key not in _EFFECTS:
        raise ValueError(f"Unknown item effect key: {key!r}")


for definition in (
    EffectDefinition('village_gold_boost','Рынок +25% на 8 часов; повтор продлевает срок.',village_boost,''),
    EffectDefinition('village_shards_boost','Шахта +25% на 8 часов; повтор продлевает срок.',village_boost,''),
    EffectDefinition('tower_equipment_chest','Случайная экипировка Испытаний.',equipment_chest,''),
    EffectDefinition(EFFECT_GACHA_TICKET, 'Одна крутка героя без монет.', None, ''),
    EffectDefinition(EFFECT_GACHA_LUCK, 'Следующая крутка получает усиленные веса редкостей: легендарные +10%, редкие +30%.', charged_effect, '🍀 Удача — следующая крутка'),
    EffectDefinition(EFFECT_BOSS_DAMAGE, 'Следующий бой с боссом: весь твой итоговый урон после пассивки героя +10%.', charged_effect, '🧪 Урон +10% — следующий бой'),
    EffectDefinition(EFFECT_BOSS_PHANTOM, 'Следующий бой с боссом: получишь награду даже если не нанесёшь ни одного удара.', charged_effect, '👻 Фантомное участие — следующий бой'),
    EffectDefinition(EFFECT_COIN_POUCH, 'Открывается и даёт от 10 до 30 монет.', coin_pouch, ''),
    EffectDefinition(EFFECT_SHARD_CASKET, 'Открывается и даёт от 10 до 30 осколков.', shard_casket, ''),
):
    register_effect(definition)


def effect_description(key: str) -> str:
    definition = get_effect(str(key or ""))
    return definition.description if definition else "Эффект предмета не настроен."


def active_effect_title(key: str) -> str:
    key = str(key or "")
    definition = get_effect(key)
    return definition.active_title if definition and definition.active_title else key
