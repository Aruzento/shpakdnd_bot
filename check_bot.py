"""Локальная проверка проекта без подключения к Telegram polling."""

from app.config import DB_PATH, TIMEZONE_NAME
from app.db.schema import init_db
from app.handlers import ROUTERS
from app.mini.catalog import validate_content
from app.mini.content_safety import validate_combat_content
from app.mini.boss.catalog import load_boss_catalog, load_boss_item_catalog
from app.mini.boss.boss_abilities.catalog import load_ability_catalog
from app.mini.boss.schema import init_boss_db
from app.mini.heroes import sync_hero_catalog
from app.mini.schema import init_mini_db
from app.mini.shop import sync_shop_catalog
from app.mini.worlds import sync_configured_mini_worlds


def main():
    init_db()
    init_mini_db()
    init_boss_db()
    worlds = sync_configured_mini_worlds()
    content = validate_content()
    boss_abilities = load_ability_catalog()
    boss_content = load_boss_catalog()
    boss_items = load_boss_item_catalog()
    combat_content = validate_combat_content(bosses=boss_content["bosses"])
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
    print(
        "OK: Boss-контент: "
        f"боссов={len(boss_content['bosses'])} "
        f"предметов={len(boss_items['items'])}"
    )
    print(f"OK: Combat v2: boss abilities={len(boss_abilities['abilities'])}")
    print(f"OK: Combat content safety: active heroes={combat_content['active_heroes']} active bosses={combat_content['active_bosses']}")
    for warning in combat_content["warnings"]:
        print(f"WARN: {warning}")
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
