"""V1.5 content diagnostics. Missing assets are blockers, never generated placeholders."""

import argparse
import json
import hashlib
from copy import deepcopy
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
WHITE_BACKGROUND_TOLERANCE = 3
BLACK_BACKGROUND_TOLERANCE = 4


def png(path, *, background=None, include_corners=False):
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
    if len(chunks[b"IHDR"]) != 13:
        raise ValueError("Invalid PNG IHDR")
    width, height, depth, color, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    if width != height or width < 1 or width > 8192:
        raise ValueError("PNG must be square, 1..8192 pixels")
    if background is None and not include_corners:
        return {"width": width, "height": height}
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or not channels or interlace or compression or filter_method:
        raise ValueError("Background validation requires noninterlaced 8-bit PNG")
    try:
        raw = zlib.decompress(b"".join(packed))
    except zlib.error as error:
        raise ValueError("Invalid PNG compressed scanlines") from error
    transparency = chunks.get(b"tRNS")
    if transparency is not None and (
        (color == 0 and len(transparency) != 2)
        or (color == 2 and len(transparency) != 6)
        or color in (4, 6)
        or (color == 3 and len(transparency) > len(chunks.get(b"PLTE", b"")) // 3)
    ):
        raise ValueError("Invalid PNG transparency")
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
                if transparency is not None and color in (0, 2):
                    transparent = struct.unpack(
                        ">H" if color == 0 else ">HHH", transparency
                    )
                    if rgb == ((transparent[0],) * 3 if color == 0 else transparent):
                        alpha = 0
                corners.append((rgb, alpha))
        previous = row
    # Applied only by callers that explicitly request an approved background.
    tolerance = {255: WHITE_BACKGROUND_TOLERANCE, 0: BLACK_BACKGROUND_TOLERANCE}.get(
        background, 0
    )
    if any(
        alpha != 255
        or len(rgb) != 3
        or (
            background is not None
            and any(abs(channel - background) > tolerance for channel in rgb)
        )
        for rgb, alpha in corners
    ):
        raise ValueError(
            "PNG corners must be opaque "
            + ("black (RGB 0..4)" if background == 0 else "white (RGB 252..255)")
            + ": "
            + str(path)
        )
    result = {"width": width, "height": height}
    if background is not None:
        result["background"] = background
    if include_corners:
        result.update(
            bit_depth=depth,
            color_type=color,
            corners=[
                {"x": x, "y": y, "rgba": [*rgb, alpha]}
                for (x, y), (rgb, alpha) in zip(
                    ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)),
                    corners,
                )
            ],
        )
    return result


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
    from app.mini.catalog import validate_mythic_fields

    for hero in values:
        validate_mythic_fields(hero)
    for field in ("code", "name"):
        if len({h.get(field) for h in values}) != 8:
            raise ValueError("Duplicate Mythic " + field)
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


def _s5_records(heroes, existing):
    """Validate both fresh and identical imported records without replacing any code."""
    if (
        not isinstance(heroes, list)
        or any(not isinstance(h, dict) for h in heroes)
        or Counter(h.get("rarity") for h in heroes)
        != Counter(common=5, uncommon=6, rare=4, legendary=2)
    ):
        raise ValueError("S5 needs exactly 5/6/4/2 heroes")
    codes = [h.get("code") for h in heroes]
    if (
        any(not isinstance(c, str) or not c.strip() for c in codes)
        or len(set(codes)) != 17
    ):
        raise ValueError("S5 invalid/duplicate code")
    for hero in heroes:
        image = hero.get("image", "")
        if (
            not isinstance(image, str)
            or not image.endswith(".png")
            or "/" in image
            or "\\" in image
            or Path(image).name != image
        ):
            raise ValueError("Invalid S5 PNG basename")
    present = {h["code"]: h for h in existing["heroes"] if h["code"] in codes}
    if present and len(present) != 17:
        raise ValueError("Partial S5 catalog: refusing changes")
    from unittest.mock import patch
    from app.mini.catalog import load_hero_catalog as validate

    candidate = {
        **existing,
        "heroes": [h for h in existing["heroes"] if h["code"] not in codes]
        + deepcopy(heroes),
    }
    with patch("app.mini.catalog._read_hero_catalog", return_value=candidate):
        expected = {h["code"]: h for h in validate()["heroes"] if h["code"] in codes}
    if present and present != expected:
        raise ValueError("Conflicting S5 catalog: refusing changes")
    return bool(present)


def s5(source, images):
    data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    heroes = data.get("heroes") if isinstance(data, dict) else None
    _s5_records(heroes, load_hero_catalog())
    root = Path(images).resolve()
    for hero in heroes:
        path = root / hero["image"]
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError("S5 PNG must be local to the selected input directory")
        png(path, background=255)
    return heroes


def report():
    from app.mini.mythic.raid.catalog import load, IMAGES as RAID_IMAGES
    from app.stories.catalog import discover

    heroes = load_hero_catalog()["heroes"]
    blocked = validate_mythics(heroes, images=HERO_IMAGES_DIR)
    missing_inputs, assets = [], []
    manifest = Path(__file__).resolve().parents[3] / "docs/v1.5/S5_IMPORT_MANIFEST.json"
    s5_heroes = json.loads(manifest.read_text(encoding="utf-8"))["heroes"]
    imported = _s5_records(
        s5_heroes, {"heroes": heroes, "settings": load_hero_catalog()["settings"]}
    )
    expected = [
        ("mythic", HERO_IMAGES_DIR / f"{element}_{stage}.png", 0)
        for element in sorted(ELEMENTS)
        for stage in STAGES
    ]
    expected += [("s5", HERO_IMAGES_DIR / h["image"], 255) for h in s5_heroes]
    expected += [
        ("raid", RAID_IMAGES / f"raid_boss_{index}.png", 0) for index in range(1, 5)
    ]
    for kind, path, background in expected:
        if not path.is_file():
            reason = "Missing " + kind + " PNG: " + str(path)
            blocked.append(reason)
            missing_inputs.append({"path": str(path), "reason": reason})
            continue
        if path.is_symlink():
            raise ValueError("PNG must be a local file: " + str(path))
        info = png(path, background=background, include_corners=True)
        assets.append(
            {
                "kind": kind,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                **info,
            }
        )
    if not imported:
        blocked.append("S5 not imported: " + ", ".join(h["code"] for h in s5_heroes))
    bosses = load(require_images=False)
    for boss in bosses:
        path = RAID_IMAGES / boss["image"]
        if not path.is_file():
            blocked.append("Missing raid PNG: " + str(path))
            missing_inputs.append(
                {"path": str(path), "reason": "missing real raid PNG"}
            )
        else:
            png(path, background=0)
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
        "components": {
            "PNG_PASS": "PASS" if len(assets) == 45 else "BLOCKED_BY_INPUT",
            "S5_IMPORTED": "PASS" if imported else "BLOCKED_BY_INPUT",
            "MYTHIC_RECORDS_READY": (
                "PASS"
                if len([h for h in heroes if h["rarity"] == "mythic"]) == 8
                else "BLOCKED_BY_INPUT"
            ),
            "RAID_RECORDS_READY": "PASS" if len(bosses) == 4 else "BLOCKED_BY_INPUT",
            "STORIES_READY": "PASS" if stories else "BLOCKED_BY_INPUT",
            "STRICT_CONTENT_PASS": "PASS" if not blocked else "BLOCKED_BY_INPUT",
        },
        "assets": assets,
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
