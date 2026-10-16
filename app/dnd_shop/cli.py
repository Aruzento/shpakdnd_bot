"""Server catalog validator/publisher: python -m app.dnd_shop.cli --help."""
import argparse
from pathlib import Path

from app.config import BASE_DIR, DB_PATH, DND_SHOP_CSV_PATH
from app.dnd_shop.catalog import CatalogError, catalog_lock, publish
from app.dnd_shop.service import Shop


def main():
    parser = argparse.ArgumentParser(description='Ordinary D&D shop catalog operations (never starts the bot)')
    parser.add_argument('command', choices=('validate', 'publish'))
    parser.add_argument('source', type=Path)
    parser.add_argument('--target', type=Path, default=DND_SHOP_CSV_PATH)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    args = parser.parse_args()
    if args.target.resolve().is_relative_to(BASE_DIR.resolve()):
        parser.error('Catalog must be outside the Git checkout / watcher tree.')
    if not args.db.is_file():
        parser.error('An initialized existing SQLite is required; no empty database will be created.')
    shop = Shop(args.db, args.target)
    try:
        if args.command == 'publish':
            publish(args.source, args.target, shop.validate_staged)
        else:
            with catalog_lock(args.target):
                shop.validate_staged(args.source)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Catalog rejected: {error}\n')
    print('OK: catalog validated' if args.command == 'validate' else 'OK: catalog atomically published')


if __name__ == '__main__':
    main()
