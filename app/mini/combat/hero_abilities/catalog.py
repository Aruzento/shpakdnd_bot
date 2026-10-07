import json
from pathlib import Path


ABILITIES_DIR = Path(__file__).resolve().parent
ABILITIES_JSON = ABILITIES_DIR / "abilities.json"


class AbilityCatalogError(ValueError):
    pass


def load_ability_catalog() -> dict:
    try:
        data = json.loads(ABILITIES_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AbilityCatalogError(
            f"Не найден каталог пассивок: {ABILITIES_JSON}"
        ) from error
    except json.JSONDecodeError as error:
        raise AbilityCatalogError(
            f"Некорректный JSON пассивок: {error}"
        ) from error

    if not isinstance(data, dict):
        raise AbilityCatalogError("Ability catalog must be an object.")
    abilities = data.get("abilities")
    if not isinstance(abilities, dict):
        raise AbilityCatalogError(
            "В abilities.json поле abilities должно быть объектом."
        )

    for key, ability in abilities.items():
        if not str(key).strip() or not isinstance(ability, dict):
            raise AbilityCatalogError("Некорректная запись пассивки.")
        effects = ability.get("effects", [])
        if not isinstance(effects, list):
            raise AbilityCatalogError(
                f"У пассивки {key} поле effects должно быть списком."
            )

        new_effects = {
            'every_n_pure_base_hit': ('attack', {'every': (1,1000), 'multiplier_percent': (1,1000)}),
            'every_n_zero_then_double_hit': ('attack', {'every': (2,1000)}),
            'pure_primary_hit': ('attack', {}),
            'copy_boss_form': ('battle_start', {}),
            'shadow_extraction': ('victory', {'chance_percent': (0,100)}),
            'every_n_decoy': ('after_attack', {'every': (1,1000), 'chance_percent': (0,100)}),
            'save_shield': ('boss_attack', {'chance_percent': (0,100)}),
            'every_n_seal': ('after_attack', {'every': (1,1000)}),
            'every_n_max_hp_damage': ('attack', {'every': (1,1000), 'damage_percent': (0,100)}),
            'every_n_extra_hits': ('attack', {'every': (1,1000), 'damage_percent': (1,100), 'extra_attacks': (1,10)}),
        }
        for effect in effects:
            if not isinstance(effect, dict):
                raise AbilityCatalogError(f'Ability {key}: effect must be an object.')
            spec = new_effects.get(effect.get('type'))
            if spec:
                trigger, fields = spec
                if effect.get('trigger') != trigger:
                    raise AbilityCatalogError(f'Ability {key}: invalid trigger.')
                for field, (low, high) in fields.items():
                    value = effect.get(field)
                    if type(value) is not int or not low <= value <= high:
                        raise AbilityCatalogError(f'Ability {key}: invalid {field}.')

    return data


def get_ability(passive_key: str) -> dict:
    key = str(passive_key or "none").strip() or "none"
    catalog = load_ability_catalog()
    ability = catalog["abilities"].get(key)
    if ability is None:
        # Не ломаем бой из-за нового passive_key: герой просто бьёт базовой атакой.
        return {
            "key": key,
            "name": key,
            "description": "Пассивка ещё не настроена для боя.",
            "effects": [],
            "configured": False,
        }
    result = dict(ability)
    result["key"] = key
    result["configured"] = True
    result.setdefault("name", key)
    result.setdefault("description", "")
    result.setdefault("effects", [])
    return result


def configured_ability_keys() -> set[str]:
    return set(load_ability_catalog()["abilities"].keys())
