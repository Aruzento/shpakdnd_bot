"""Preserve stable-release code, catalogs, tests and handler registration."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

BASELINE_SHA = "f06c0d129fdad0fef4fde0889915faac26b94f4a"
ALLOWLIST_PATH = "docs/release/approved-removals.json"
HANDLERS_PATH = "app/handlers/__init__.py"


class GuardError(ValueError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, encoding="utf-8")
    if result.returncode:
        raise GuardError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def tree_files(repo: Path, revision: str) -> set[str]:
    return set(filter(None, git(repo, "ls-tree", "-r", "--name-only", "-z", revision).split("\0")))


def source(repo: Path, revision: str, path: str) -> str:
    return git(repo, "show", f"{revision}:{path}").lstrip("\ufeff")


def protected(path: str) -> bool:
    return (path == "bot.py" or path.endswith(".json") and path != ALLOWLIST_PATH
            or path.endswith(".py") and path.startswith(("app/", "tests/")))


def test_symbols(text: str, path: str) -> set[str]:
    """Qualified names include classes and nested scopes, not just test counts."""
    symbols = set()

    def visit(node: ast.AST, scope: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scope += (node.name,)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                name = ".".join(scope)
                if name in symbols:
                    raise GuardError(f"duplicate test symbol: {path}::{name}")
                symbols.add(name)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    try:
        visit(ast.parse(text, filename=path))
    except SyntaxError as error:
        raise GuardError(f"invalid Python: {path}: {error}") from error
    return symbols


def handler_inventory(text: str) -> tuple[set[str], set[str]]:
    imports, routers = set(), set()
    try:
        tree = ast.parse(text, filename=HANDLERS_PATH)
    except SyntaxError as error:
        raise GuardError(f"invalid handlers AST: {error}") from error
    assignments = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                suffix = f" as {alias.asname}" if alias.asname else ""
                imports.add(f"from {module} import {alias.name}{suffix}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                suffix = f" as {alias.asname}" if alias.asname else ""
                imports.add(f"import {alias.name}{suffix}")
        value = None
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ROUTERS" for t in node.targets):
            value = node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name) and node.target.id == "ROUTERS":
            value = node.value
        if value is not None:
            assignments += 1
            if assignments > 1 or isinstance(node, ast.AugAssign):
                raise GuardError("ROUTERS must have exactly one explicit assignment")
            if not isinstance(value, (ast.List, ast.Tuple)) or not all(isinstance(item, ast.Name) for item in value.elts):
                raise GuardError("ROUTERS must be an explicit list/tuple of router names for review")
            routers.update(item.id for item in value.elts)
    return imports, routers


def inventory(repo: Path, revision: str) -> dict:
    files = tree_files(repo, revision)
    tests = {path: test_symbols(source(repo, revision, path), path)
             for path in sorted(files) if path.startswith("tests/") and path.endswith(".py")}
    imports, routers = handler_inventory(source(repo, revision, HANDLERS_PATH)) if HANDLERS_PATH in files else (set(), set())
    return {"files": {p for p in files if protected(p)}, "tests": tests,
            "imports": imports, "routers": routers}


def removals(before: dict, after: dict) -> set[tuple[str, str, str]]:
    missing = {("file", path, "") for path in before["files"] - after["files"]}
    # A reviewed file removal covers its contained symbols, not other files.
    for path, symbols in before["tests"].items():
        if path in after["files"]:
            missing.update(("test", path, name) for name in symbols - after["tests"].get(path, set()))
    if HANDLERS_PATH in after["files"]:
        for kind, key in (("import", "imports"), ("router", "routers")):
            missing.update((kind, HANDLERS_PATH, name) for name in before[key] - after[key])
    return missing


def load_allowlist(text: str, missing: set[tuple[str, str, str]]) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GuardError(f"duplicate allowlist field: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=unique_pairs)
    except (ValueError, TypeError) as error:
        raise GuardError(f"invalid allowlist: {error}") from error
    if not isinstance(data, dict) or set(data) != {"removals"} or not isinstance(data["removals"], list):
        raise GuardError("invalid allowlist: expected an object with a removals list")
    approved = {}
    for entry in data["removals"]:
        if not isinstance(entry, dict):
            raise GuardError("invalid allowlist entry: expected an object")
        kind = entry.get("kind")
        fields = {"kind", "path", "reason"} | ({"symbol"} if kind != "file" else set())
        if kind not in {"file", "test", "import", "router"} or set(entry) != fields:
            raise GuardError("invalid allowlist entry fields/kind")
        path, symbol, reason = entry["path"], entry.get("symbol", ""), entry["reason"]
        if not all(isinstance(value, str) for value in (path, symbol, reason)):
            raise GuardError("allowlist path, symbol and reason must be strings")
        if (not path or path != PurePosixPath(path).as_posix() or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts or "\\" in path or ":" in path
                or any(char in path + symbol for char in "*?[]")
                or kind != "file" and not symbol.strip()):
            raise GuardError("allowlist requires an exact relative path and exact symbol; wildcards forbidden")
        if len(reason.strip()) < 12 or len(reason.split()) < 3 or reason.strip().lower() in {"todo", "test", "approved"}:
            raise GuardError("allowlist requires a meaningful reason (at least three words and 12 characters)")
        key = (kind, path, symbol)
        if key in approved:
            raise GuardError(f"duplicate allowlist removal: {key}")
        if key not in missing:
            raise GuardError(f"stale or nonexistent allowlist removal: {key}")
        approved[key] = reason.strip()
    return approved


def check(repo: Path, baseline: str = BASELINE_SHA, target: str = "HEAD") -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        raise GuardError("baseline must be an explicit full stable-release SHA")
    try:
        git(repo, "cat-file", "-e", f"{baseline}^{{commit}}")
    except GuardError as error:
        raise GuardError(f"baseline unavailable: {baseline}; fetch complete history") from error
    head = git(repo, "rev-parse", "--verify", f"{target}^{{commit}}").strip()
    git(repo, "merge-base", "--is-ancestor", baseline, head)
    before, after = inventory(repo, baseline), inventory(repo, head)
    if not before["tests"] or not before["files"] or not before["routers"]:
        raise GuardError("baseline inventory is incomplete: application, tests and ROUTERS required")
    missing = removals(before, after)
    try:
        allowlist_text = source(repo, head, ALLOWLIST_PATH)
    except GuardError as error:
        raise GuardError(f"allowlist missing: {ALLOWLIST_PATH}") from error
    approved = load_allowlist(allowlist_text, missing)
    unexplained = sorted(missing - approved.keys())
    if unexplained:
        detail = "\n".join(f"  {kind}: {path}" + (f"::{symbol}" if symbol else "") for kind, path, symbol in unexplained)
        raise GuardError(f"unapproved removals from stable baseline {baseline}:\n{detail}")
    return {"baseline": baseline, "head": head, "approved_removals": len(approved),
            "baseline_test_symbols": sum(map(len, before["tests"].values())),
            "head_test_symbols": sum(map(len, after["tests"].values())),
            "protected_files": len(after["files"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", default=BASELINE_SHA, help="explicit stable SHA; override for synthetic fixtures only")
    parser.add_argument("--target", default="HEAD")
    args = parser.parse_args(argv)
    try:
        report = check(args.repo, args.baseline, args.target)
    except (GuardError, OSError) as error:
        print(f"ERROR: release guard: {error}", file=sys.stderr)
        return 1
    print("OK: release guard " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
