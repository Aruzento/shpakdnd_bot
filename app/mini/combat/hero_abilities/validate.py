from app.mini.combat.hero_abilities.catalog import configured_ability_keys
from app.mini.catalog import load_hero_catalog


def missing_hero_passives() -> list[str]:
    hero_keys = {
        str(hero.get("passive_key") or "none").strip() or "none"
        for hero in load_hero_catalog().get("heroes", [])
    }
    return sorted(hero_keys - configured_ability_keys())


def main() -> int:
    missing = missing_hero_passives()
    if missing:
        print("ERROR: для этих passive_key нет боевой настройки:")
        for key in missing:
            print(f"- {key}")
        return 1
    print("OK: все passive_key из единого каталога героев подключены к боевому движку.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
