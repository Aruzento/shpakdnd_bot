"""Frozen two-phase deployment receipt; tests run in an external exact-SHA clone."""

import argparse
import hashlib
import json
import os
import io
import re
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

try:
    from .release_content import external_directory, fingerprint
except ImportError:
    from release_content import external_directory, fingerprint


def git(project, *args):
    return subprocess.check_output(["git", "-C", str(project), *args], text=True, stderr=subprocess.PIPE).strip()


def exact_sha(value):
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("BLOCKED_INVALID_SHA")
    return value


def validate_workspace(workspace, project):
    raw = Path(workspace)
    if raw.is_symlink() or not raw.is_absolute():
        raise ValueError("BLOCKED_WORKSPACE_BOUNDARY")
    workspace = raw.resolve(strict=True)
    project = Path(project).resolve(strict=True)
    if not workspace.name.startswith("shpakdnd-preflight.") or workspace == project or project in workspace.parents:
        raise ValueError("BLOCKED_WORKSPACE_BOUNDARY")
    if any((p / ".git").exists() for p in workspace.parents):
        raise ValueError("BLOCKED_WORKSPACE_INSIDE_GIT")
    return workspace


def safe_environment(project, stories):
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(BOT_TOKEN="123456:offline-release-preflight", PYTHONIOENCODING="utf-8",
               PYTHONUTF8="1", PYTHONUNBUFFERED="1", DND_CHECK_DENY_PROJECT=str(project),
               DND_CHECK_OFFLINE="1", STORIES_DIR=str(stories))
    return env


def validate_frozen_bundle(candidate):
    names = ("deploy-shpakdnd.sh", "deploy_helpers.py", "pvp_ops.py", "sqlite-deploy.py",
             "telegram-deploy-notice.py", "release_guard.py", "release_content.py",
             "release_preflight.py", "release_schema.json")
    for name in names:
        frozen = Path(__file__).with_name(name)
        target = candidate / "deploy" / name
        if not target.is_file() or hashlib.sha256(frozen.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise ValueError("BLOCKED_FROZEN_BUNDLE_TARGET_MISMATCH:" + name)


def configuration(project, requested="", service=None):
    from dotenv import dotenv_values
    project = Path(project).resolve(strict=True)
    path = project / ".env"
    if path.is_symlink():
        raise ValueError("BLOCKED_CONFIG_SYMLINK")
    raw = path.read_bytes()
    try:
        values = dotenv_values(stream=io.StringIO(raw.decode("utf-8")), interpolate=False)
    except UnicodeError:
        raise ValueError("BLOCKED_CONFIG_ENCODING") from None
    configured = values.get("STORIES_DIR")
    if not configured:
        raise ValueError("BLOCKED_EXPLICIT_TARGET_STORIES_DIR_REQUIRED")
    directory = external_directory(configured, project)
    if requested and external_directory(requested, project) != directory:
        raise ValueError("BLOCKED_TARGET_CONFIGURATION_MISMATCH")
    signature = None
    if service:
        properties = {}
        for name in ("Environment", "EnvironmentFiles", "WorkingDirectory", "ExecStart", "User"):
            properties[name] = subprocess.check_output(
                ["systemctl", "show", "-p", name, "--value", service], text=True,
                stderr=subprocess.DEVNULL).strip()
        # ExecStart's D-Bus rendering includes live pid/start/stop/code/status.
        # Those change on an ordinary stop and must not invalidate static identity.
        properties["ExecStart"] = re.sub(
            r";\s*(?:start_time|stop_time|pid|code|status)=[^;{}]*", "", properties["ExecStart"])
        properties["UnitDefinition"] = subprocess.check_output(
            ["systemctl", "cat", "--no-pager", service], text=True,
            stderr=subprocess.DEVNULL)
        try:
            for item in shlex.split(properties["Environment"]):
                if item.startswith("STORIES_DIR=") and external_directory(item.split("=", 1)[1], project) != directory:
                    raise ValueError("BLOCKED_SERVICE_STORIES_OVERRIDE")
        except ValueError:
            raise ValueError("BLOCKED_SERVICE_STORIES_OVERRIDE_OR_FORMAT") from None
        files = properties["EnvironmentFiles"]
        matches = re.findall(r"(.+?) \(ignore_errors=(?:yes|no)\)(?: |$)", files)
        if files and (not matches or any(Path(name.strip().strip('"')).resolve() != path for name in matches)):
            raise ValueError("BLOCKED_UNREVIEWED_SERVICE_ENVIRONMENT_FILE")
        # Never log environment values/tokens, only the digest of static unit settings.
        signature = hashlib.sha256(json.dumps(properties, sort_keys=True).encode()).hexdigest()
    return dict(directory=str(directory), env_sha256=hashlib.sha256(raw).hexdigest(), service=service, service_signature=signature)


def prepare(args):
    project = Path(args.project).resolve(strict=True)
    workspace = validate_workspace(args.workspace, project)
    target = exact_sha(args.sha)
    old = exact_sha(args.old_sha)
    config = configuration(project, args.stories_directory, getattr(args, "service", None))
    stories = Path(config["directory"])
    if git(project, "rev-parse", "HEAD") != old or git(project, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("BLOCKED_LIVE_HEAD_OR_DIRTY")
    if git(project, "rev-parse", "origin/main^{commit}") != target:
        raise ValueError("BLOCKED_TARGET_NOT_ORIGIN_MAIN")
    if shutil.disk_usage(workspace).free < 8 * 1024**3:
        raise ValueError("BLOCKED_PREFLIGHT_DISK_SPACE_UNDER_8G")
    candidate = workspace / "candidate"
    if candidate.exists():
        raise ValueError("BLOCKED_WORKSPACE_NOT_EMPTY")
    env = safe_environment(project, stories)
    scratch = workspace / "scratch"
    scratch.mkdir()
    env.update(TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch))
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(project), str(candidate)], check=True, env=env)
    subprocess.run(["git", "-C", str(candidate), "checkout", "--detach", target], check=True, env=env)
    if git(candidate, "rev-parse", "HEAD") != target or git(candidate, "status", "--porcelain"):
        raise ValueError("BLOCKED_CANDIDATE_SHA_OR_DIRTY")
    if (candidate / ".env").exists() or (candidate / "shpakdnd.db").exists():
        raise ValueError("BLOCKED_CANDIDATE_TRACKED_ENV_OR_DATABASE")
    story_files = fingerprint(stories, project)
    validate_frozen_bundle(candidate)
    run_id = uuid.uuid4().hex
    log = workspace / "preflight.log"
    command = [sys.executable, "scripts/run_isolated_checks.py", "--label", "release-" + run_id,
               "--", "tools/release_preflight_checks.py", "--target-stories-dir", str(stories), "--sha", target]
    began = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        proc = subprocess.run(command, cwd=candidate, env=env, stdout=stream, stderr=subprocess.STDOUT)
    receipt = dict(run_id=run_id, target_sha=target, old_sha=old, candidate=str(candidate),
                   stories_directory=str(stories), python=sys.version, command=command,
                   exit_code=proc.returncode, seconds=round(time.monotonic()-began, 3), log=str(log))
    receipt["configuration"] = config
    if proc.returncode == 0:
        if config != configuration(project, args.stories_directory, getattr(args, "service", None)):
            raise ValueError("BLOCKED_CONFIGURATION_CHANGED_DURING_PREFLIGHT")
        if story_files != fingerprint(stories, project):
            raise ValueError("BLOCKED_TARGET_STORIES_CHANGED_DURING_PREFLIGHT")
        receipt["story_files"] = story_files
        if git(candidate, "rev-parse", "HEAD") != target or git(candidate, "status", "--porcelain"):
            raise ValueError("BLOCKED_CANDIDATE_CHANGED_AFTER_CHECKS")
    (workspace / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))
    if proc.returncode:
        print(log.read_text(encoding="utf-8")[-8000:])
        raise ValueError("BLOCKED_OFFLINE_PREFLIGHT_GATE")


def verify(args):
    project = Path(args.project).resolve(strict=True)
    workspace = validate_workspace(args.workspace, project)
    receipt = json.loads((workspace / "receipt.json").read_text(encoding="utf-8"))
    target = exact_sha(args.sha)
    expected = target if args.installed else exact_sha(args.old_sha)
    if receipt.get("exit_code") != 0 or receipt.get("target_sha") != target or receipt.get("old_sha") != args.old_sha:
        raise ValueError("BLOCKED_RECEIPT_SHA_OR_RESULT")
    if git(project, "rev-parse", "HEAD") != expected or git(project, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("BLOCKED_LIVE_HEAD_OR_DIRTY")
    if git(project, "rev-parse", "origin/main^{commit}") != target:
        raise ValueError("BLOCKED_TARGET_MOVED")
    candidate = workspace / "candidate"
    if str(candidate) != receipt["candidate"] or git(candidate, "rev-parse", "HEAD") != target or git(candidate, "status", "--porcelain"):
        raise ValueError("BLOCKED_CANDIDATE_RECEIPT")
    current_config = configuration(project, receipt["stories_directory"], receipt["configuration"]["service"])
    if current_config != receipt["configuration"]:
        raise ValueError("BLOCKED_CONFIGURATION_CHANGED")
    if receipt["story_files"] != fingerprint(receipt["stories_directory"], project):
        raise ValueError("BLOCKED_TARGET_STORIES_CHANGED")
    print("Preflight receipt and target fingerprint: PASS " + target)


def export_reports(args):
    workspace = validate_workspace(args.workspace, args.project)
    scratch = workspace / "scratch"
    names = {"release-gates.json", "validators.json", "target-content.json", "result.json",
             "v15-migration.json", "shop-benchmark.json", "story-candidate.json"}
    with zipfile.ZipFile(workspace / "evidence.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in scratch.rglob("*") if scratch.exists() else ():
            if file.is_file() and not file.is_symlink() and (file.suffix == ".log" or file.name in names):
                archive.write(file, file.relative_to(workspace))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "configuration", "export"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--sha")
    parser.add_argument("--old-sha")
    parser.add_argument("--stories-directory", default="")
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--service")
    args = parser.parse_args()
    try:
        if args.action == "configuration":
            if args.workspace:
                external_directory(args.workspace, args.project)
            print(configuration(args.project, args.stories_directory, args.service)["directory"])
        elif args.action == "export":
            export_reports(args)
        elif args.action == "prepare":
            prepare(args)
        else:
            verify(args)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Never print CalledProcessError commands/env or raw config exceptions.
        print(str(error) if isinstance(error, ValueError) else "BLOCKED_PREFLIGHT_IO_OR_GIT", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
