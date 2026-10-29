"""Read-only Story validation against an explicitly selected external test directory."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.stories.catalog import directory, discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = directory(args.directory)
        if not path.is_dir():
            raise ValueError("The selected external test directory does not exist")
        stories, errors = discover(path)
        result = dict(
            status=(
                "INVALID" if errors else "VALIDATED" if stories else "BLOCKED_BY_INPUT"
            ),
            directory=str(path),
            codes=sorted(stories),
            errors=errors,
            production_acceptance=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if errors else 0 if stories else 2
    except (ValueError, OSError) as error:
        print(json.dumps(dict(status="INVALID", error=str(error)), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
