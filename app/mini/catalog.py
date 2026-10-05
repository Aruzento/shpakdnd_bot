import json
from pathlib import Path


CONTENT_DIR = Path(__file__).resolve().parent / "content"
SHOP_PATH = CONTENT_DIR / "shop.json"
HEROES_PATH = CONTENT_DIR / "heroes.json"
HERO_IMAGES_DIR = CONTENT_DIR / "hero_images"

ALLOWED_DELIVERY_TYPES = {"inventory", "certificate"}
ALLOWED_RARITIES = {"common", "uncommon", "rare", "legendary"}


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

        product_codes.add(code)

    return data


def load_hero_catalog() -> dict:
    data = _read_json(HEROES_PATH)
    settings = data.get("settings")
    heroes = data.get("heroes")

    if not isinstance(settings, dict) or not isinstance(heroes, list):
        raise ValueError("heroes.json: нужны объект settings и массив heroes.")

    codes = set()
    for hero in heroes:
        if not isinstance(hero, dict):
            raise ValueError("heroes.json: герой должен быть объектом.")

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
