"""V1.5 content diagnostics. Missing assets are blockers, never generated placeholders."""

import argparse
import json
import struct
import zlib
from collections import Counter
from pathlib import Path
from app.mini.catalog import HERO_IMAGES_DIR, load_hero_catalog
from app.mini.combat.modes import ELEMENTS

BASE_ATTACK = {
    "ice": 1,
    "lightning": 2,
    "dark": 2,
    "fire": 3,
    "star": 2,
    "earth": 3,
    "moon": 1,
    "sun": 1,
}
STAGES = ("egg", "young", "adult")


def png(path, *, background=None):
    """Validate CRCs, square dimensions, decode noninterlaced 8-bit PNG corners."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ValueError("PNG unavailable: " + str(path)) from error
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not PNG: " + str(path))
    pos = 8
    chunks = {}
    packed = []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("Truncated PNG")
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + size]
        if (
            pos + size + 12 > len(data)
            or zlib.crc32(kind + body) & 0xFFFFFFFF
            != struct.unpack(">I", data[pos + 8 + size : pos + 12 + size])[0]
        ):
            raise ValueError("PNG CRC/truncation")
        chunks[kind] = body
        if kind == b"IDAT":
            packed.append(body)
        pos += size + 12
        if kind == b"IEND":
            break
    if b"IHDR" not in chunks or b"IEND" not in chunks or not packed:
        raise ValueError("Incomplete PNG")
    width, height, depth, color, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    if width != height or width < 1 or width > 8192:
        raise ValueError("PNG must be square, 1..8192 pixels")
    if background is None:
        return {"width": width, "height": height}
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or not channels or interlace or compression or filter_method:
        raise ValueError("Background validation requires noninterlaced 8-bit PNG")
    raw = zlib.decompress(b"".join(packed))
    stride = width * channels
    if len(raw) != (stride + 1) * height:
        raise ValueError("Invalid PNG scanlines")
    previous = bytearray(stride)
    corners = []
    for y in range(height):
        offset = y * (stride + 1)
        method = raw[offset]
        row = bytearray(raw[offset + 1 : offset + 1 + stride])
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if method == 1:
                predict = a
            elif method == 2:
                predict = b
            elif method == 3:
                predict = (a + b) // 2
            elif method == 4:
                p = a + b - c
                da, db, dc = abs(p - a), abs(p - b), abs(p - c)
                predict = a if da <= db and da <= dc else (b if db <= dc else c)
            elif method == 0:
                predict = 0
            else:
                raise ValueError("Invalid PNG filter")
            row[i] = (row[i] + predict) % 256
        if y in (0, height - 1):
            for x in (0, width - 1):
                pixel = row[x * channels : (x + 1) * channels]
                if color == 3:
                    index = pixel[0]
                    palette = chunks.get(b"PLTE", b"")
                    rgb = tuple(palette[index * 3 : index * 3 + 3])
                    alpha = chunks.get(b"tRNS", b"")[index : index + 1] or b"\xff"
                    alpha = alpha[0]
                elif color in (0, 4):
                    rgb = (pixel[0],) * 3
                    alpha = pixel[1] if color == 4 else 255
                else:
                    rgb = tuple(pixel[:3])
                    alpha = pixel[3] if color == 6 else 255
                corners.append((rgb, alpha))
        previous = row
    target = (background,) * 3
    if any(alpha != 255 or rgb != target for rgb, alpha in corners):
        raise ValueError(
            "PNG corners must be opaque "
            + ("black" if background == 0 else "white")
            + ": "
            + str(path)
        )
    return {"width": width, "height": height, "background": background}


def pet_image(element, stage, images=HERO_IMAGES_DIR):
    stage = {"wonder": "young"}.get(stage, stage)
    if element not in ELEMENTS or stage not in STAGES:
        raise ValueError("Invalid pet asset")
    path = Path(images) / f"{element}_{stage}.png"
    return path if path.is_file() else None


def ready_hero(hero):
    image = pet_image(hero["mythic_element"], "adult")
    return bool(image and hero.get("image") == image.name)


def validate_mythics(heroes, *, images=HERO_IMAGES_DIR):
    values = [h for h in heroes if h["rarity"] == "mythic"]
    if not values:
        return [
            "Missing real 8-hero Mythic catalog (pet PNG alone do not create hero records)"
        ]
    if len(values) != 8 or {h.get("mythic_element") for h in values} != ELEMENTS:
        raise ValueError("Mythic catalog needs exactly eight distinct elements")
    missing = []
    for hero in values:
        element = hero["mythic_element"]
        if hero["attack"] != BASE_ATTACK[element]:
            raise ValueError("Incorrect fixed Mythic base attack")
        if any(
            hero["passives"][mode]["key"] != "mythic_" + element
            for mode in ("normal", "raid")
        ):
            raise ValueError("Mythic needs both authored mode properties")
        if hero.get("image") != element + "_adult.png":
            raise ValueError("Adult pet/hero must share image")
        for stage in STAGES:
            path = Path(images) / f"{element}_{stage}.png"
            if not path.is_file():
                missing.append(str(path))
            else:
                png(path, background=0)
    return missing


def s5(source, images):
    data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    heroes = data.get("heroes")
    if not isinstance(heroes, list) or Counter(
        h.get("rarity") for h in heroes
    ) != Counter(common=5, uncommon=6, rare=4, legendary=2):
        raise ValueError("S5 needs exactly 5/6/4/2 heroes")
    codes = [h.get("code") for h in heroes]
    if len(set(codes)) != 17:
        raise ValueError("S5 duplicate code")
    existing = load_hero_catalog()
    if set(codes) & {h["code"] for h in existing["heroes"]}:
        raise ValueError("S5 must not replace existing codes")
    for hero in heroes:
        image = hero.get("image", "")
        if not image or Path(image).name != image or not image.endswith(".png"):
            raise ValueError("Invalid S5 PNG")
        png(Path(images) / image, background=255)
    # Reuse the real full-catalog validator without modifying the checkout.
    from unittest.mock import patch
    from app.mini.catalog import load_hero_catalog as validate

    with patch(
        "app.mini.catalog._read_hero_catalog",
        return_value={**existing, "heroes": existing["heroes"] + heroes},
    ):
        validate()
    return heroes


def report():
    from app.mini.mythic.raid.catalog import load
    from app.stories.catalog import discover

    heroes = load_hero_catalog()["heroes"]
    blocked = validate_mythics(heroes, images=HERO_IMAGES_DIR)
    missing_inputs = []
    for element in sorted(ELEMENTS):
        for stage in STAGES:
            path = HERO_IMAGES_DIR / f"{element}_{stage}.png"
            if not path.is_file():
                missing_inputs.append(
                    {"path": str(path), "reason": "missing real Mythic PNG"}
                )
    manifest = Path(__file__).resolve().parents[3] / "docs/v1.5/S5_IMPORT_MANIFEST.json"
    if manifest.exists():
        s5_heroes = json.loads(manifest.read_text(encoding="utf-8"))["heroes"]
        for hero in s5_heroes:
            path = HERO_IMAGES_DIR / hero["image"]
            if not path.is_file():
                missing_inputs.append(
                    {"path": str(path), "reason": "missing real S5 PNG"}
                )
                blocked.append("Missing S5 PNG: " + str(path))
            else:
                png(path, background=255)
        missing = [
            h["code"] for h in s5_heroes if h["code"] not in {e["code"] for e in heroes}
        ]
        if missing:
            blocked.append("S5 not imported: " + ", ".join(missing))
    bosses = load(require_images=False)
    from app.mini.mythic.raid.catalog import IMAGES as RAID_IMAGES

    for boss in bosses:
        path = RAID_IMAGES / boss["image"]
        if not path.is_file():
            blocked.append("Missing raid PNG: " + str(path))
            missing_inputs.append(
                {"path": str(path), "reason": "missing real raid PNG"}
            )
        else:
            png(path)
    if not bosses:
        blocked.append(
            "Missing four real raid boss records: app/mini/mythic/raid/bosses.json; supplied images do not create records"
        )
    stories, errors = discover()
    if errors:
        raise ValueError(str(errors))
    if not stories:
        blocked.append("No real stories in external STORIES_DIR")
    return {
        "status": "BLOCKED_BY_INPUT" if blocked else "validated",
        "hero_count": len(heroes),
        "raid_bosses": len(bosses),
        "stories": len(stories),
        "blockers": blocked,
        "missing_inputs": missing_inputs,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-content", action="store_true")
    args = parser.parse_args()
    try:
        result = report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if args.strict_content and result["blockers"] else 0
    except (ValueError, OSError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
