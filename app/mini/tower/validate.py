from app.mini.tower.catalog import load_catalog
from app.mini.tower.balance import load_balance
from app.mini.equipment.catalog import load_catalog as equipment_catalog


def main():
    print(f"OK: Tower floors={len(load_catalog()['floors'])}, reward bands={len(load_balance()['bands'])}")
    print(f"OK: Equipment items={len(equipment_catalog()['items'])}")


if __name__ == "__main__":
    main()
