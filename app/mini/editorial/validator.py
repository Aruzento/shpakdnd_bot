"""python -m app.mini.editorial.validator [--directory /external/ru]"""
import argparse
import os
from pathlib import Path
from .loader import Loader
from .validation import EditorialError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=os.getenv("MINI_EDITORIAL_DIR"))
    args = parser.parse_args()
    try:
        loader = Loader(None)
        if args.directory:
            # Unlike runtime fallback, CLI exits nonzero for a rejected revision.
            directory = Path(args.directory)
            if not directory.is_dir():
                raise EditorialError(f"{directory}: directory does not exist")
            from .validation import read_json, validate_entry
            candidate = dict(loader.defaults)
            for file in sorted(directory.glob("*.json")):
                if file.name not in loader.files:
                    raise EditorialError(f"{file}: unknown catalog")
                for key, value in read_json(file).items():
                    if key not in loader.files[file.name]:
                        raise EditorialError(f"{file}: {key}: unknown key or wrong catalog")
                    if loader.schemas[key]["kind"] in {"icon", "glossary"} and isinstance(value, dict):
                        value = {**candidate[key], **value}
                    validate_entry(file, key, value, loader.schemas[key])
                    candidate[key] = value
            for key, value in candidate.items():
                if loader.schemas[key]["kind"] == "glossary" and loader.schemas.get(value["icon_key"], {}).get("kind") != "icon":
                    raise EditorialError(f"glossary.json: {key}: unknown icon_key")
            Loader(directory)
        print(f"OK: Editorial files={len(loader.files)} keys={len(loader.defaults)}")
    except (EditorialError, OSError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
