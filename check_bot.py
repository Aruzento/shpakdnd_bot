"""Локальная проверка проекта без подключения к Telegram polling."""

from app.config import DB_PATH, TIMEZONE_NAME
from app.db.schema import init_db
from app.handlers import ROUTERS
from app.mini.catalog import validate_content
from app.mini.heroes import sync_hero_catalog
from app.mini.schema import init_mini_db
from app.mini.shop import sync_shop_catalog
from app.mini.worlds import sync_configured_mini_worlds


def main():
    init_db()
    init_mini_db()
    worlds = sync_configured_mini_worlds()
    content = validate_content()
    sync_hero_catalog()
    for world in worlds:
        sync_shop_catalog(world["id"])

    print("OK: Python-модули импортированы")
    print(f"OK: база данных: {DB_PATH}")
    print(f"OK: часовой пояс: {TIMEZONE_NAME}")
    print(f"OK: роутеров: {len(ROUTERS)}")
    print(
        "OK: Mini-контент: "
        f"категорий={content['shop_categories']} "
        f"товаров={content['shop_products']} "
        f"героев={content['heroes']}"
    )
    if content["missing_hero_images"]:
        print(
            "WARN: нет картинок у героев: "
            + ", ".join(content["missing_hero_images"])
        )

    if worlds:
        for world in worlds:
            print(
                "OK: D&D Mini: "
                f"chat={world['chat_id']} "
                f"topic={world['thread_id']} "
                f"name={world['name']}"
            )
    else:
        print("WARN: в app/topics.py нет тем с mini=True")


if __name__ == "__main__":
    main()
