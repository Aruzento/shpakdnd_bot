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
