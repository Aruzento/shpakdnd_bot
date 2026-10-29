"""Import the approved S5 manifest in a feature checkout, never a server/SQLite.

Existing identical PNGs are reused without writes. Complete identical records are
an idempotent no-op; partial/conflicting catalogs or assets fail before any write.
All replacements are staged on the target filesystem and restored on failure.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.mini.catalog import (
    HERO_CATALOG_DIR,
    HERO_IMAGES_DIR,
    HEROES_PATH,
    RARITIES,
    load_hero_catalog,
)
from app.mini.mythic.content import s5

APPROVED_SOURCE = ROOT / "docs/v1.5/S5_IMPORT_MANIFEST.json"


def prepare(source, images):
    source, images = Path(source).resolve(), Path(images).resolve()
    if source != APPROVED_SOURCE.resolve():
        raise ValueError("Only docs/v1.5/S5_IMPORT_MANIFEST.json is approved")
    manifest_bytes = source.read_bytes()
    data = json.loads(manifest_bytes.decode("utf-8-sig"))
    initial = data.get("heroes") if isinstance(data, dict) else None
    if not isinstance(initial, list) or any(not isinstance(h, dict) for h in initial):
        raise ValueError("S5 manifest must contain hero objects")
    guards = {source: manifest_bytes}
    for hero in initial:
        image = hero.get("image", "")
        if (
            not isinstance(image, str)
            or "/" in image
            or "\\" in image
            or not image.endswith(".png")
        ):
            raise ValueError("Invalid S5 PNG basename")
        path = images / image
        if path.is_symlink() or path.resolve().parent != images:
            raise ValueError("S5 input must be local")
        guards[path] = path.read_bytes()
    for path in [
        HEROES_PATH,
        *(HERO_CATALOG_DIR / (rarity + ".json") for rarity in RARITIES),
    ]:
        if path.is_symlink():
            raise ValueError("S5 catalog must be local")
        guards[path] = path.read_bytes()
    heroes = s5(source, images)
    if any(path.is_symlink() or _read(path) != value for path, value in guards.items()):
        raise ValueError("S5 inputs changed during validation")
    catalog = load_hero_catalog()
    codes = {h["code"] for h in heroes}
    imported = codes <= {h["code"] for h in catalog["heroes"]}
    changes = {}
    for rarity in ("common", "uncommon", "rare", "legendary"):
        path = HERO_CATALOG_DIR / (rarity + ".json")
        if path.is_symlink():
            raise ValueError("S5 catalog must not be a symlink")
        raw = guards[path]
        if not imported:
            data = json.loads(raw.decode("utf-8"))
            if any(h["code"] in codes for h in data["heroes"]):
                raise ValueError("S5 catalog changed during validation")
            data["heroes"].extend(h for h in heroes if h["rarity"] == rarity)
            changes[path] = (
                json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
    reused = []
    for hero in heroes:
        origin = images / hero["image"]
        destination = HERO_IMAGES_DIR / hero["image"]
        raw = guards[origin]
        if (
            destination.is_symlink()
            or destination.resolve().parent != HERO_IMAGES_DIR.resolve()
        ):
            raise ValueError("S5 destination must be a local PNG basename")
        if destination.exists():
            existing = destination.read_bytes()
            if existing != raw:
                raise ValueError("Conflicting existing S5 PNG: " + str(destination))
            # The source was already decoded/validated. Equal bytes prove the
            # destination passes that same contract, including source == target.
            guards[destination] = existing
            reused.append(hero["image"])
        else:
            guards[destination] = None
            changes[destination] = raw
    return dict(
        heroes=heroes,
        changes=changes,
        guards=guards,
        already_imported=imported,
        reused_images=reused,
    )


def _read(path):
    return path.read_bytes() if path.exists() else None


@contextmanager
def import_lock():
    path = HERO_CATALOG_DIR.parent / ".s5-import.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(
            "S5 import is already locked; inspect the previous run"
        ) from error
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def _staged_write(path, value):
    with path.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def apply(plan):
    # Recheck every input before replacing any catalog. The tool lock excludes
    # concurrent invocations; this also detects edits after preflight.
    for path, expected in plan["guards"].items():
        if path.is_symlink() or _read(path) != expected:
            raise ValueError("S5 input changed after validation: " + str(path))
    if not plan["changes"]:
        return
    stage = Path(tempfile.mkdtemp(prefix=".s5-import-", dir=HERO_CATALOG_DIR.parent))
    installed = []
    keep_backups = False
    try:
        staged = {}
        backups = {}
        for index, (path, value) in enumerate(plan["changes"].items()):
            staged[path] = stage / (str(index) + ".new")
            _staged_write(staged[path], value)
            old = plan["guards"][path]
            if old is not None:
                backups[path] = stage / (str(index) + ".old")
                _staged_write(backups[path], old)
        try:
            for path, new in staged.items():
                if path.is_symlink() or _read(path) != plan["guards"][path]:
                    raise ValueError(
                        "S5 target changed before replacement: " + str(path)
                    )
                os.replace(new, path)
                installed.append(path)
        except BaseException:
            for path in reversed(installed):
                try:
                    if path in backups:
                        os.replace(backups[path], path)
                    else:
                        path.unlink()
                except OSError:
                    keep_backups = True
            if keep_backups:
                raise RuntimeError(
                    "S5 rollback could not finish; retained backups: " + str(stage)
                )
            raise
    finally:
        if not keep_backups:
            # Only our own flat staging files, never user directories.
            for item in stage.iterdir():
                item.unlink()
            stage.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--images",
        type=Path,
        required=True,
        help="Explicitly selected trusted input directory; only manifest PNG basenames are read",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.apply:
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"], cwd=ROOT, text=True
            ).strip()
            if branch != "codex/v1.5":
                raise ValueError("S5 --apply is allowed only in codex/v1.5")
            with import_lock():
                plan = prepare(args.source, args.images)
                apply(plan)
        else:
            plan = prepare(args.source, args.images)
    except (ValueError, OSError) as error:
        print("BLOCKED:", error)
        return 2
    print("Validated real S5:", dict(Counter(h["rarity"] for h in plan["heroes"])))
    print("Existing identical PNGs reused:", len(plan["reused_images"]))
    if not args.apply:
        print("Read-only validation; no files changed.")
    elif not plan["changes"]:
        print("Already imported: identical 17 S5 records and PNGs; no-op.")
    else:
        print("Imported approved S5; existing PNGs were not overwritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
