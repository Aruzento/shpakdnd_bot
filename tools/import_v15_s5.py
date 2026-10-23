"""Validate all real S5 JSON/PNG before importing into a feature checkout.

Default is read-only. --apply installs the whole validated set and rolls back
all changed files if a local write fails. No server/SQLite operations.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.mini.catalog import HERO_CATALOG_DIR, HERO_IMAGES_DIR
from app.mini.mythic.content import s5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        heroes = s5(args.source, args.images)
    except (ValueError, OSError) as error:
        print("BLOCKED:", error)
        return 2
    print("Validated real S5:", dict(Counter(h["rarity"] for h in heroes)))
    if not args.apply:
        print("Read-only validation; no files changed.")
        return 0
    changes = {}
    for rarity in {h["rarity"] for h in heroes}:
        path = HERO_CATALOG_DIR / (rarity + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["heroes"].extend(h for h in heroes if h["rarity"] == rarity)
        changes[path] = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    for hero in heroes:
        path = HERO_IMAGES_DIR / hero["image"]
        if path.exists():
            raise ValueError("Refusing to overwrite existing asset: " + str(path))
        changes[path] = (args.images / hero["image"]).read_bytes()
    originals = {p: p.read_bytes() if p.exists() else None for p in changes}
    try:
        for path, value in changes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
    except BaseException:
        for path, value in originals.items():
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(value)
        raise
    print(
        "Imported 17 approved heroes and their real images. Run all validators/tests before commit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
