from app.mini.effects.registry import validate_effect_key
from app.mini.combat.tags import is_open_tag
import json

from app.mini.combat.matchups import FACTIONS
from app.mini.boss.boss_abilities.catalog import get_ability, validate_config
from pathlib import Path


BOSS_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BOSS_DIR / "content"
BOSSES_JSON = CONTENT_DIR / "bosses.json"
ITEMS_JSON = CONTENT_DIR / "items.json"
IMAGES_DIR = CONTENT_DIR / "images"


class BossCatalogError(ValueError):
    pass


def load_boss_item_catalog() -> dict:
    try:
        data = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BossCatalogError(f"Не найден каталог предметов босса: {ITEMS_JSON}") from error
    except json.JSONDecodeError as error:
        raise BossCatalogError(f"Некорректный JSON предметов босса: {error}") from error

    items = data.get("items")
    if not isinstance(items, list):
        raise BossCatalogError("В items.json поле items должно быть списком.")

    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise BossCatalogError("Каждый предмет босса должен быть JSON-объектом.")
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code or not name:
            raise BossCatalogError("У каждого предмета обязательны code и name.")
        if code in seen:
            raise BossCatalogError(f"Повторяющийся code предмета босса: {code}")
        validate_effect_key(item.get("effect_key", ""))
        seen.add(code)
    return data


def list_boss_reward_items(*, active_only: bool = True) -> list[dict]:
    result = []
    for raw in load_boss_item_catalog()["items"]:
        item = dict(raw)
        item.setdefault("category", "boss_reward")
        item.setdefault("description", "")
        item.setdefault("effect_key", "")
        item.setdefault("stackable", True)
        item.setdefault("active", True)
        if active_only and not bool(item["active"]):
            continue
        result.append(item)
    return result


def _validate_reward_items(raw_items, known_items: set[str], boss_code: str) -> list[dict]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise BossCatalogError(f"У босса {boss_code} reward_items должен быть списком.")

    result = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BossCatalogError(f"У босса {boss_code} reward_items содержит не объект.")
        code = str(raw.get("code", "")).strip()
        quantity = int(raw.get("quantity", 1))
        if code not in known_items:
            raise BossCatalogError(f"У босса {boss_code} неизвестный reward item: {code}")
        if quantity <= 0:
            raise BossCatalogError(f"У босса {boss_code} quantity предмета должен быть > 0.")
        result.append({"code": code, "quantity": quantity})
    return result


def load_boss_catalog() -> dict:
    try:
        data = json.loads(BOSSES_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BossCatalogError(f"Не найден каталог боссов: {BOSSES_JSON}") from error
    except json.JSONDecodeError as error:
        raise BossCatalogError(f"Некорректный JSON боссов: {error}") from error

    if not isinstance(data, dict):
        raise BossCatalogError("bosses.json: root must be an object.")
    bosses = data.get("bosses")
    if not isinstance(bosses, list):
        raise BossCatalogError("В bosses.json поле bosses должно быть списком.")

    known_items = {item["code"] for item in list_boss_reward_items(active_only=False)}
    seen = set()
    normalized = []
    for raw in bosses:
        if not isinstance(raw, dict):
            raise BossCatalogError("Каждый босс должен быть JSON-объектом.")
        boss = dict(raw)
        code = str(boss.get("code", "")).strip()
        name = str(boss.get("name", "")).strip()
        if not code or not name:
            raise BossCatalogError("У каждого босса обязательны code и name.")
        if code in seen:
            raise BossCatalogError(f"Повторяющийся code босса: {code}")
        seen.add(code)
        if int(boss.get("max_hp", 0)) <= 0:
            raise BossCatalogError(f"У босса {code} max_hp должен быть > 0.")
        if int(boss.get("min_players", 0)) <= 0:
            raise BossCatalogError(f"У босса {code} min_players должен быть > 0.")

        shields = int(boss.get("reward_shields", 3))
        decay = int(boss.get("reward_decay_percent", 10))
        if shields < 0:
            raise BossCatalogError(f"У босса {code} reward_shields должен быть >= 0.")
        if decay <= 0 or decay > 100:
            raise BossCatalogError(
                f"У босса {code} reward_decay_percent должен быть от 1 до 100."
            )

        faction = boss.get("faction")
        if not isinstance(faction, str) or faction not in FACTIONS:
            raise BossCatalogError(f"Boss {code}: invalid faction {faction!r}.")
        try:
            get_ability(boss.get("ability_key"))
            validate_config(boss["ability_key"], boss.get("ability_config", {}))
        except ValueError as error:
            raise BossCatalogError(f"Boss {code}: {error}") from error
        if not isinstance(boss.get("ability_text"), str):
            raise BossCatalogError(f"Boss {code}: ability_text must be a string.")
        if not isinstance(boss.get("features"), list) or any(
            not is_open_tag(tag) for tag in boss["features"]
        ):
            raise BossCatalogError(f"Boss {code}: features must be a list of string tags.")
        boss["reward_items"] = _validate_reward_items(
            boss.get("reward_items", []), known_items, code
        )
        normalized.append(boss)

    result = dict(data)
    result["bosses"] = normalized
    return result


def list_boss_templates(*, active_only: bool = True) -> list[dict]:
    result = []
    for raw in load_boss_catalog()["bosses"]:
        boss = dict(raw)
        boss.setdefault("description", "")
        boss.setdefault("image", "")
        boss.setdefault("reward_coins", 0)
        boss.setdefault("reward_items", [])
        boss.setdefault("reward_shields", 3)
        boss.setdefault("reward_decay_percent", 10)
        boss.setdefault("active", True)
        if active_only and not bool(boss["active"]):
            continue
        result.append(boss)
    return result


def get_boss_template(code: str) -> dict | None:
    code = str(code).strip()
    for boss in list_boss_templates(active_only=False):
        if boss["code"] == code:
            return boss
    return None


def boss_image_path(image_name: str) -> Path | None:
    image_name = str(image_name or "").strip()
    if not image_name:
        return None

    # Картинки босса должны лежать только в boss/content/images.
    if Path(image_name).name != image_name:
        return None

    path = IMAGES_DIR / image_name
    if not path.is_file():
        return None
    return path


def sync_boss_reward_items(db_path=None) -> int:
    """Синхронизирует только предметы-награды boss-модуля в mini_items."""
    from app.config import DB_PATH
    from app.mini.db import connect_mini_db

    target = DB_PATH if db_path is None else db_path
    items = list_boss_reward_items(active_only=False)

    with connect_mini_db(target) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_items'"
        ).fetchone()
        if exists is None:
            return 0

        for item in items:
            conn.execute(
                """
                INSERT INTO mini_items (
                    code, name, category, description, effect_key, stackable, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    description = excluded.description,
                    effect_key = excluded.effect_key,
                    stackable = excluded.stackable,
                    active = excluded.active
                """,
                (
                    item["code"],
                    item["name"],
                    item.get("category", "boss_reward"),
                    item.get("description", ""),
                    item.get("effect_key", ""),
                    1 if item.get("stackable", True) else 0,
                    1 if item.get("active", True) else 0,
                ),
            )
        conn.commit()
    return len(items)
