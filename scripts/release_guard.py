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
INFRASTRUCTURE_BASELINE_SHA = "49cd326dc7283f0d29b26a5f9307f4dfa6dec3c1"
INFRASTRUCTURE_REQUIRED = {"scripts/release_checks.py", "scripts/release_guard.py", "scripts/test_inventory.py", ".github/workflows/release-checks.yml"}
INFRASTRUCTURE_HEAD_REQUIRED = INFRASTRUCTURE_REQUIRED | {"deploy/release_preflight.py", "deploy/preflight_data.py", "deploy/release_state.py",
    "deploy/systemd_state.py", "deploy/release_lkg.py", "deploy/legacy_lkg.py", "deploy/install-systemd-units.sh", ".github/workflows/systemd-staging.yml", "deploy/ci-systemd/check.sh", "deploy/ci-systemd/Dockerfile", "deploy/ci-systemd/legacy-check.py"}
D_TEST_INVENTORY_HASH = "3312f5c52a90786cfa479b520865ad5df8d5f5ae9472e1b842133c66bd5f6116"
D_TEST_INVENTORY = "docs/release/mandatory-d-tests.json"
STAGE_C_SHA = "436b0cda31887f87ca8a2f627c4b5a8baf4ec6d8"
INFRASTRUCTURE_HEAD_REQUIRED |= {"deploy/staging_topics.py", "deploy/semantic_smoke.py", "deploy/semantic_evidence.py", "scripts/release_report.py", "scripts/github_release.py", "scripts/semantic_smoke_ci.py", "docs/staging-v1.4.1.md", D_TEST_INVENTORY}
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


def infrastructure(path: str) -> bool:
    return (path in INFRASTRUCTURE_REQUIRED or path.startswith("deploy/") and
            path.endswith((".sh", ".py", ".service", ".path", ".service.example")))


def validate_workflow(text: str):
    # Conservative contract for this workflow: changes to these critical lines
    # must update guard + its independent tests under protected-main review.
    required = ("name: Release checks", "  push:", "    branches: ['main', 'codex/**']", "  pull_request:",
                "    branches: [main]", "  contents: read", "    name: Linux release checks",
                "    runs-on: ubuntu-latest", "    timeout-minutes: 20", "      BOT_TOKEN: ci-test-token",
                "          fetch-depth: 0", "          python-version: '3.13'",
                '        run: python scripts/release_checks.py --report "$RUNNER_TEMP/release-checks.json"',
                '        run: sudo -E "$(which python)" scripts/semantic_smoke_ci.py')
    lines = text.splitlines()
    if any(line not in lines for line in required):
        raise GuardError("required CI workflow protection is missing/disabled")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("if:") and stripped != "if: always()":
            raise GuardError("required CI workflow conditional may disable checks")
        if (stripped.startswith("continue-on-error:") and stripped != "continue-on-error: false") or "|| true" in line:
            raise GuardError("required CI workflow ignores failures")
    if not any("uses: actions/checkout@" in line for line in lines) or not any("uses: actions/setup-python@" in line for line in lines):
        raise GuardError("required official CI setup actions are missing")


def validate_systemd_workflow(text):
    required=("name: Isolated systemd staging", "  push:", "    branches: ['main', 'codex/**']", "  pull_request:",
              "    branches: [main]", "    runs-on: ubuntu-latest", "          fetch-depth: 0",
              "      - run: python deploy/ci-systemd/legacy-check.py",
              '          docker exec shpakdnd-systemd-stage bash /source/deploy/ci-systemd/check.sh "$SOURCE_SHA"')
    if any(line not in text.splitlines() for line in required):raise GuardError("required systemd staging disabled")
    if any(line.strip().startswith(("if:","continue-on-error:")) or "|| true" in line for line in text.splitlines()):
        raise GuardError("systemd staging cannot bypass failures")


def validate_mandatory_gates(lkg_text,deploy_text,semantic_text):
    try:
        tree=ast.parse(lkg_text)
        promote=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='promote')
        direct=[n for n in promote.body if isinstance(n,ast.Assign) and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute)
                and isinstance(c.func.value,ast.Name) and c.func.value.id=='semantic' and c.func.attr=='validate_for_lkg' for c in ast.walk(n))]
        if len(direct)!=1:raise ValueError('mandatory direct semantic validation missing')
        semantic_ast=ast.parse(semantic_text)
        fresh=next(n for n in semantic_ast.body if isinstance(n,ast.FunctionDef) and n.name=='fresh')
        required=('STALE','Real Telegram','operator','confirmation hash')
        if not all(word in ast.unparse(fresh) for word in required) or 'if not 0 <= age <= max_age:' not in ast.unparse(fresh):raise ValueError('semantic stale/operator gate disabled')
        if '    (( ! INITIALIZE_LKG )) || readiness_action=gate-legacy-bootstrap' not in deploy_text.splitlines():raise ValueError('pinned legacy gate missing')
        if '    local readiness_action=gate' not in deploy_text.splitlines() or '"$TOOLS/shared/release_report.py" "$readiness_action"' not in deploy_text or deploy_text.index('"$TOOLS/shared/release_report.py" "$readiness_action"')>deploy_text.index('    MAINTENANCE=1'):
            raise ValueError('final readiness gate missing before maintenance')
    except (SyntaxError,StopIteration,ValueError) as error:raise GuardError('mandatory release/LKG semantic gate disabled: '+str(error)) from error


def validate_d_inventory(repo,head):
    import hashlib
    text=source(repo,head,D_TEST_INVENTORY)
    if hashlib.sha256(text.encode()).hexdigest()!=D_TEST_INVENTORY_HASH:raise GuardError('Mandatory D test inventory changed')
    entries=json.loads(text)
    for path,required in entries.items():
        symbols=test_symbols(source(repo,head,path),path)
        if not set(required)<=symbols:raise GuardError('Mandatory D test removed: '+path)


def infrastructure_inventory(repo: Path, revision: str) -> set[str]:
    return {path for path in tree_files(repo, revision) if infrastructure(path)}


def protected(path: str) -> bool:
    return (infrastructure(path) or path == "bot.py" or path.endswith(".json") and path != ALLOWLIST_PATH
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


def check(repo: Path, baseline: str = BASELINE_SHA, target: str = "HEAD", *, infrastructure_baseline: str = INFRASTRUCTURE_BASELINE_SHA) -> dict:
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
    if not re.fullmatch(r"[0-9a-f]{40}", infrastructure_baseline):
        raise GuardError("infrastructure baseline must be an explicit full SHA")
    try:
        git(repo, "cat-file", "-e", f"{infrastructure_baseline}^{{commit}}")
    except GuardError as error:
        raise GuardError(f"infrastructure baseline unavailable: {infrastructure_baseline}") from error
    # Inventory baseline is immutable, not necessarily an ancestor after squash.
    # Game V1.4 ancestry remains mandatory above.
    infra = infrastructure_inventory(repo, infrastructure_baseline)
    if not INFRASTRUCTURE_REQUIRED <= infra:
        raise GuardError("infrastructure baseline inventory is incomplete")
    missing_infra = (infra | INFRASTRUCTURE_HEAD_REQUIRED) - tree_files(repo, head)
    if missing_infra:
        raise GuardError("critical infrastructure removed: " + ", ".join(sorted(missing_infra)))
    validate_workflow(source(repo, head, ".github/workflows/release-checks.yml"))
    validate_systemd_workflow(source(repo, head, ".github/workflows/systemd-staging.yml"))
    # Stage D explicitly adds main push and isolated automatic smoke. All other
    # A executor/discovery semantics remain byte-for-byte reviewed.
    # Stage B does not alter the CI executor or discovery semantics. Keeping
    # their exact reviewed source prevents a no-op runner passing the inventory.
    for path in ("scripts/release_checks.py", "scripts/test_inventory.py", ".github/workflows/release-checks.yml"):
        text=source(repo, head, path)
        if path==".github/workflows/release-checks.yml":
            text=text.replace("branches: ['main', 'codex/**']", "branches: ['codex/**']").replace('      - name: Automatic read-only semantic smoke\n        run: sudo -E "$(which python)" scripts/semantic_smoke_ci.py\n','')
        baseline_text=source(repo,infrastructure_baseline,path)
        if path==".github/workflows/release-checks.yml":
            baseline_text=baseline_text.replace("branches: ['main', 'codex/**']", "branches: ['codex/**']").replace('      - name: Automatic read-only semantic smoke\n        run: sudo -E "$(which python)" scripts/semantic_smoke_ci.py\n','')
        if text != baseline_text:
            raise GuardError("reviewed CI executor/workflow changed or disabled: " + path)
    if infrastructure_baseline==INFRASTRUCTURE_BASELINE_SHA:
        git(repo,"cat-file","-e",STAGE_C_SHA+"^{commit}")
        previous=inventory(repo,STAGE_C_SHA)
        missing_stage_c=removals(previous,after)
        if missing_stage_c:raise GuardError("reviewed A/B/C functionality or tests removed: "+str(sorted(missing_stage_c)))
    if infrastructure_baseline==INFRASTRUCTURE_BASELINE_SHA:
        validate_d_inventory(repo,head)
    validate_mandatory_gates(source(repo,head,"deploy/release_lkg.py"),source(repo,head,"deploy/deploy-shpakdnd.sh"),source(repo,head,"deploy/semantic_evidence.py"))
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
    return {"baseline": baseline, "infrastructure_baseline": infrastructure_baseline, "head": head, "approved_removals": len(approved),
            "baseline_test_symbols": sum(map(len, before["tests"].values())),
            "head_test_symbols": sum(map(len, after["tests"].values())),
            "protected_files": len(after["files"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", default=BASELINE_SHA, help="explicit stable SHA; override for synthetic fixtures only")
    parser.add_argument("--target", default="HEAD")
    parser.add_argument("--infrastructure-baseline", default=INFRASTRUCTURE_BASELINE_SHA, help="immutable stage A SHA; fixture override only")
    args = parser.parse_args(argv)
    try:
        report = check(args.repo, args.baseline, args.target, infrastructure_baseline=args.infrastructure_baseline)
    except (GuardError, OSError) as error:
        print(f"ERROR: release guard: {error}", file=sys.stderr)
        return 1
    print("OK: release guard " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
