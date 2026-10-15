"""Inventory the exact unittest discovery used by the release CLI."""
import inspect
import json
from pathlib import Path
import sys
import unittest


def inventory(suite, root):
    records = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            records.extend(inventory(test, root))
            continue
        if isinstance(test, unittest.loader._FailedTest):
            raise RuntimeError(f"unittest discovery failed: {test.id()}: {test._exception}")
        method = inspect.unwrap(getattr(type(test), test._testMethodName))
        path = Path(inspect.getsourcefile(method)).resolve().relative_to(root).as_posix()
        records.append({"id": test.id(), "path": path, "symbol": method.__qualname__})
    return records


def main():
    root = Path.cwd().resolve()
    records = inventory(unittest.defaultTestLoader.discover("tests"), root)
    Path(sys.argv[1]).write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"OK: unittest discovery count={len(records)}")


if __name__ == "__main__":
    main()
