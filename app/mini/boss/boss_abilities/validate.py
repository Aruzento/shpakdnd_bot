from app.mini.boss.boss_abilities.catalog import load_ability_catalog
from app.mini.boss.catalog import load_boss_catalog


def main() -> int:
    abilities = load_ability_catalog()["abilities"]
    bosses = load_boss_catalog()["bosses"]
    print(f"OK: boss abilities={len(abilities)}, validated bosses={len(bosses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
