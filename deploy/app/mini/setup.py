import argparse

from app.config import DB_PATH
from app.mini.schema import MINI_TABLES, init_mini_db
from app.mini.worlds import register_mini_world


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Первичная настройка D&D Mini для Telegram-темы."
    )

    parser.add_argument(
        "--chat-id",
        type=int,
        required=True,
        help="Telegram chat_id",
    )

    parser.add_argument(
        "--thread-id",
        type=int,
        default=0,
        help="Telegram message_thread_id; для чата без тем — 0",
    )

    parser.add_argument(
        "--name",
        default="D&D Mini",
        help="Название Mini-мира",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    init_mini_db()

    world_id = register_mini_world(
        chat_id=args.chat_id,
        thread_id=args.thread_id,
        name=args.name,
    )

    print("D&D Mini настроен.")
    print(f"База: {DB_PATH}")
    print(f"World ID: {world_id}")
    print(f"Chat ID: {args.chat_id}")
    print(f"Thread ID: {args.thread_id}")
    print(f"Название: {args.name}")
    print(f"Создано/проверено таблиц: {len(MINI_TABLES)}")


if __name__ == "__main__":
    main()
