import json
from pathlib import Path


BOSS_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BOSS_DIR / "content"
BOSSES_JSON = CONTENT_DIR / "bosses.json"
IMAGES_DIR = CONTENT_DIR / "images"


class BossCatalogError(ValueError):
    pass


def load_boss_catalog() -> dict:
    try:
        data = json.loads(BOSSES_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BossCatalogError(f"Не найден каталог боссов: {BOSSES_JSON}") from error
    except json.JSONDecodeError as error:
        raise BossCatalogError(f"Некорректный JSON боссов: {error}") from error

    bosses = data.get("bosses")
    if not isinstance(bosses, list):
        raise BossCatalogError("В bosses.json поле bosses должно быть списком.")

    seen = set()
    for boss in bosses:
        if not isinstance(boss, dict):
            raise BossCatalogError("Каждый босс должен быть JSON-объектом.")
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

    return data


def list_boss_templates(*, active_only: bool = True) -> list[dict]:
    result = []
    for raw in load_boss_catalog()["bosses"]:
        boss = dict(raw)
        boss.setdefault("description", "")
        boss.setdefault("image", "")
        boss.setdefault("reward_coins", 0)
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
