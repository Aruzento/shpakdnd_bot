from app.mini.effects.registry import validate_effect_key
from app.mini.combat.tags import is_open_tag
import json
from pathlib import Path


CONTENT_DIR = Path(__file__).resolve().parent / "content"
SHOP_PATH = CONTENT_DIR / "shop.json"
HEROES_PATH = CONTENT_DIR / "heroes.json"
HERO_CATALOG_DIR = CONTENT_DIR / "heroes"
RARITIES = ("common", "uncommon", "rare", "legendary", "mythic", "shadow")
GACHA_RARITIES = frozenset(RARITIES[:4])
HERO_IMAGES_DIR = CONTENT_DIR / "hero_images"

ALLOWED_DELIVERY_TYPES = {"inventory", "certificate"}
ALLOWED_RARITIES = frozenset(RARITIES)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Не найден файл контента: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Ошибка JSON в {path.name}, строка {error.lineno}: {error.msg}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: корень должен быть объектом JSON.")

    return data


def load_shop_catalog() -> dict:
    data = _read_json(SHOP_PATH)
    categories = data.get("categories")
    products = data.get("products")

    if not isinstance(categories, list) or not isinstance(products, list):
        raise ValueError("shop.json: нужны массивы categories и products.")

    category_codes = set()
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("shop.json: категория должна быть объектом.")
        code = str(category.get("code", "")).strip()
        title = str(category.get("title", "")).strip()
        if not code or not title:
            raise ValueError("shop.json: у каждой категории нужны code и title.")
        if code in category_codes:
            raise ValueError(f"shop.json: повтор категории {code}.")
        category_codes.add(code)

    product_codes = set()
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("shop.json: товар должен быть объектом.")

        code = str(product.get("code", "")).strip()
        category = str(product.get("category", "")).strip()
        title = str(product.get("title", "")).strip()
        delivery = str(product.get("delivery", "")).strip()

        if not code or not category or not title:
            raise ValueError(
                "shop.json: у каждого товара нужны code, category и title."
            )
        if code in product_codes:
            raise ValueError(f"shop.json: повтор товара {code}.")
        if category not in category_codes:
            raise ValueError(
                f"shop.json: товар {code} ссылается на неизвестную категорию {category}."
            )
        if delivery not in ALLOWED_DELIVERY_TYPES:
            raise ValueError(
                f"shop.json: товар {code}: delivery должен быть inventory или certificate."
            )

        try:
            price = int(product.get("price", 0))
        except (TypeError, ValueError) as error:
            raise ValueError(f"shop.json: некорректная цена товара {code}.") from error
        if price < 0:
            raise ValueError(f"shop.json: цена товара {code} не может быть отрицательной.")

        for field in ("stock", "max_per_player"):
            value = product.get(field)
            if value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"shop.json: {field} товара {code} должен быть числом или null."
                    ) from error
                if value < 1:
                    raise ValueError(
                        f"shop.json: {field} товара {code} должен быть > 0 или null."
                    )

        if delivery == "inventory":
            item = product.get("item")
            if not isinstance(item, dict):
                raise ValueError(f"shop.json: товару {code} нужен объект item.")
            if not str(item.get("code", "")).strip() or not str(item.get("name", "")).strip():
                raise ValueError(
                    f"shop.json: item товара {code} должен иметь code и name."
                )

        if delivery == "inventory":
            validate_effect_key(product.get("item", {}).get("effect_key", ""))
        product_codes.add(code)

    return data


def _read_hero_catalog() -> dict:
    data = _read_json(HEROES_PATH)
    entries = []
    codes = set()
    for rarity in RARITIES:
        path = HERO_CATALOG_DIR / f"{rarity}.json"
        content = _read_json(path)
        if not isinstance(content.get("heroes"), list):
            raise ValueError(f"{path.name}: нужен массив heroes.")
        for hero in content["heroes"]:
            if not isinstance(hero, dict):
                raise ValueError(f"{path.name}: герой должен быть объектом.")
            code = hero.get("code")
            if not isinstance(code, str) or not code.strip():
                raise ValueError(f"{path.name}: нужен code героя.")
            if code.strip() in codes:
                raise ValueError(f"{path.name}: повтор героя {code} между каталогами.")
            codes.add(code.strip())
            if hero.get("rarity") != rarity:
                raise ValueError(f"{path.name}: rarity героя {code} должна быть {rarity}.")
            entries.append(hero)
    data["heroes"] = entries
    return data


def load_hero_catalog() -> dict:
    from app.mini.combat.matchups import FACTIONS, DAMAGE_TYPES, ATTACK_RANGES
    from app.mini.combat.hero_abilities.catalog import configured_ability_keys
    from app.mini.combat.tags import LEGACY_HERO_TRAITS

    data = _read_hero_catalog()
    settings = data.get("settings")
    heroes = data.get("heroes")

    if not isinstance(settings, dict) or not isinstance(heroes, list):
        raise ValueError("heroes.json: нужны объект settings и массив heroes.")

    try:
        pull_price = int(settings.get("pull_price", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("heroes.json: pull_price должен быть целым числом.") from error
    if pull_price < 0:
        raise ValueError("heroes.json: pull_price не может быть отрицательным.")

    ticket_item_code = str(settings.get("ticket_item_code", "")).strip()
    if not ticket_item_code:
        raise ValueError("heroes.json: нужен settings.ticket_item_code.")

    rarity_weights = settings.get("rarity_weights")
    duplicate_shards = settings.get("duplicate_shards")
    if not isinstance(rarity_weights, dict):
        raise ValueError("heroes.json: settings.rarity_weights должен быть объектом.")
    if not isinstance(duplicate_shards, dict):
        raise ValueError("heroes.json: settings.duplicate_shards должен быть объектом.")

    total_weight = 0
    for rarity in ALLOWED_RARITIES:
        try:
            weight = int(rarity_weights.get(rarity, 0))
            shards = int(duplicate_shards.get(rarity, 0))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"heroes.json: некорректные настройки редкости {rarity}."
            ) from error
        if weight < 0 or shards < 0:
            raise ValueError(
                f"heroes.json: вес и осколки редкости {rarity} не могут быть отрицательными."
            )
        total_weight += weight

    if total_weight <= 0:
        raise ValueError("heroes.json: сумма rarity_weights должна быть больше 0.")

    upgrade = settings.get("upgrade", {})
    if upgrade and not isinstance(upgrade, dict):
        raise ValueError("heroes.json: settings.upgrade должен быть объектом.")

    if upgrade:
        try:
            growth = int(upgrade.get("attack_growth_percent", 50))
            sell_price = int(upgrade.get("shard_sell_price", 1))
            base_cost = int(upgrade.get("star_cost_base", 10))
        except (TypeError, ValueError) as error:
            raise ValueError("heroes.json: некорректные числовые настройки улучшений.") from error

        if growth < 0 or sell_price < 0 or base_cost < 1:
            raise ValueError("heroes.json: некорректные настройки улучшений.")

        max_stars = upgrade.get("max_stars", {})
        multipliers = upgrade.get("cost_multiplier", {})
        if not isinstance(max_stars, dict) or not isinstance(multipliers, dict):
            raise ValueError(
                "heroes.json: max_stars и cost_multiplier должны быть объектами."
            )

        for rarity in ALLOWED_RARITIES:
            maximum = max_stars.get(rarity)
            if maximum is not None and int(maximum) < 1:
                raise ValueError(
                    f"heroes.json: max_stars.{rarity} должен быть > 0 или null."
                )
            if int(multipliers.get(rarity, 1)) < 1:
                raise ValueError(
                    f"heroes.json: cost_multiplier.{rarity} должен быть > 0."
                )

    passive_keys = configured_ability_keys()
    codes = set()
    for hero in heroes:
        if not isinstance(hero, dict):
            raise ValueError("heroes.json: герой должен быть объектом.")

        for field, default in LEGACY_HERO_TRAITS.items():
            hero.setdefault(field, default)
        hero.setdefault("passive_key", "none")
        code = str(hero.get("code", "")).strip()
        name = str(hero.get("name", "")).strip()
        rarity = str(hero.get("rarity", "")).strip()
        image = str(hero.get("image", "")).strip()

        if not code or not name:
            raise ValueError("heroes.json: у каждого героя нужны code и name.")
        if code in codes:
            raise ValueError(f"heroes.json: повтор героя {code}.")
        if rarity not in ALLOWED_RARITIES:
            raise ValueError(
                f"heroes.json: герой {code}: неизвестная редкость {rarity}."
            )
        if image and Path(image).name != image:
            raise ValueError(
                f"heroes.json: герой {code}: image должен содержать только имя файла."
            )

        try:
            attack = int(hero.get("attack", 1))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"heroes.json: герой {code}: attack должен быть целым числом."
            ) from error
        if attack < 1:
            raise ValueError(
                f"heroes.json: герой {code}: attack должен быть больше 0."
            )

        for field, allowed in (
            ("faction", FACTIONS), ("damage_type", DAMAGE_TYPES),
            ("attack_range", ATTACK_RANGES),
        ):
            value = hero.get(field)
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"heroes.json: hero {code}: invalid {field}: {value!r}.")
        for field in ("class_tag", "special_trait"):
            if not is_open_tag(hero.get(field)):
                raise ValueError(f"heroes.json: hero {code}: {field} must be a nonempty string tag.")
        if not isinstance(hero.get("passive_key"), str) or hero["passive_key"] not in passive_keys:
            raise ValueError(f"heroes.json: hero {code}: unknown passive_key {hero.get('passive_key')!r}.")
        if rarity == "mythic" and (type(hero.get("fragment_cost")) is not int or hero["fragment_cost"] <= 0):
            raise ValueError(f"Mythic {code}: fragment_cost должен быть > 0.")
        if "arise_chance_percent" in hero:
            from app.mini.combat.hero_abilities.engine import arise_chance
            chance=hero["arise_chance_percent"]
            if type(chance) is not int or not 0<=chance<=100 or arise_chance({"passive_key":hero["passive_key"]}) is None:
                raise ValueError(f"Hero {code}: invalid arise_chance_percent.")
        codes.add(code)

    return data


def hero_image_path(image_name: str) -> Path | None:
    image_name = str(image_name or "").strip()
    if not image_name:
        return None

    path = HERO_IMAGES_DIR / Path(image_name).name
    return path if path.is_file() else None


def validate_content() -> dict:
    shop = load_shop_catalog()
    heroes = load_hero_catalog()

    missing_images = []
    active_heroes = []
    for hero in heroes["heroes"]:
        if not bool(hero.get("active", True)):
            continue
        active_heroes.append(hero)
        if hero_image_path(hero.get("image", "")) is None:
            missing_images.append(hero["code"])

    return {
        "shop_categories": len(shop["categories"]),
        "shop_products": len(shop["products"]),
        "heroes": len(heroes["heroes"]),
        "active_heroes": len(active_heroes),
        "missing_hero_images": missing_images,
    }
