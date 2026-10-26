"""Verify authorized hardening hashes and retained immutable deployment contracts."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess

BASE = "d9856b2038a36490f93a4f5613ba7887698ac7e8"
ROOT = Path(__file__).resolve().parents[1]
HARDENED = {
    "deploy/install_bundle.py", "tests/v1_5/test_atomic_bundle.py",
    "deploy/deploy-shpakdnd.sh", "deploy/install-deploy-shpakdnd.sh",
    "deploy/release_preflight.py", "deploy/release_content.py", "deploy/release_guard.py",
    "deploy/release_schema.json", "scripts/run_isolated_checks.py",
    "tools/release_preflight_checks.py", "tools/validate_release_content.py",
    "tools/validate_deploy_bundle.py", "tools/validate_release_hardening.py",
    "tests/v1_3_3/test_deploy_shell.py", "tests/v1_4_3_1/test_deploy_confirm.py",
    "tests/v1_5/test_release_hardening.py", "tests/v1_4_3/test_pvp_rollback_shell.py",
}
IMMUTABLE = ("deploy/deploy_helpers.py", "deploy/sqlite-deploy.py",
             "deploy/telegram-deploy-notice.py", "deploy/pvp_ops.py")
FUNCTIONS = ("summary", "interrupted", "select_input", "read_input", "confirm", "require_clean",
             "unit_loaded", "stop_stack", "protected_target", "checkout_head", "start_stack",
             "failed_start", "failure_choice")


def original(path):
    return subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT).decode("utf-8")


def function(text, name):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + "() {"))
    if lines[start].endswith("}"):
        return lines[start]
    end = lines.index("}", start)
    return "\n".join(lines[start:end+1])


def test_names(text):
    return {(c.name, n.name) for c in ast.walk(ast.parse(text)) if isinstance(c, ast.ClassDef)
            for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")}


def validate(root=ROOT):
    manifest = json.loads((root / "docs/v1.5/RELEASE_HARDENING_BUNDLE_SHA256.json").read_text(encoding="utf-8"))
    if set(manifest) != HARDENED:
        raise AssertionError("Exact authorized hardening scope required")
    for path, digest in manifest.items():
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise AssertionError("Hardening bundle hash mismatch: " + path)
    for path in IMMUTABLE:
        if original(path) != (root / path).read_text(encoding="utf-8-sig"):
            raise AssertionError("Immutable protected helper changed: " + path)
    current = (root / "deploy/deploy-shpakdnd.sh").read_text(encoding="utf-8")
    baseline = original("deploy/deploy-shpakdnd.sh")
    for name in FUNCTIONS:
        if function(current, name) != function(baseline, name):
            raise AssertionError("Protected IO/service contract changed: " + name)
    for path in ("tests/v1_3_3/test_deploy_shell.py", "tests/v1_4_3_1/test_deploy_confirm.py"):
        if not test_names(original(path)) <= test_names((root / path).read_text(encoding="utf-8")):
            raise AssertionError("Protected regression removed: " + path)
    path = "tests/v1_4_3/test_pvp_rollback_shell.py"
    audited = subprocess.check_output(["git", "show", "cef77cc87f6a2286284f5cf8fdb851c775b04dd1:" + path], cwd=ROOT).decode("utf-8")
    if not test_names(audited) <= test_names((root / path).read_text(encoding="utf-8")):
        raise AssertionError("Protected PvP rollback regression removed")
    if "-m unittest" in function(current, "checks"):
        raise AssertionError("Long tests cannot run after stop")
    return True


def main():
    validate()
    print("PASS: exact authorized release-hardening bundle, immutable helpers/TTY/service contracts and retained regressions")


if __name__ == "__main__":
    main()
