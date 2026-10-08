"""Exact-SHA isolated preflight and journal-guarded short live migrations."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from deploy_helpers import DeployError, backup_database, check_database, redact_output
from preflight_data import inventory_database, compare_copy, schema_digest, isolated_database

BASELINE_SHA = "f06c0d129fdad0fef4fde0889915faac26b94f4a"
STAGE_A_SHA = "49cd326dc7283f0d29b26a5f9307f4dfa6dec3c1"
BUNDLE = ("deploy-shpakdnd.sh", "deploy_helpers.py", "sqlite-deploy.py",
          "telegram-deploy-notice.py", "release_preflight.py", "preflight_data.py",
          "release_state.py", "systemd_state.py", "release_lkg.py", "legacy_lkg.py")
SHARED_SCRIPTS=("release_checks.py","release_guard.py","test_inventory.py")


def shared_scripts(directory):
    path=Path(directory)/"shared"
    if not path.is_dir() and Path(directory).name == "deploy" and all((Path(directory).parent/"scripts"/name).is_file() for name in SHARED_SCRIPTS):
        path=Path(directory).parent/"scripts"
    return path


REQUIRED = ("bot.py", "app/config.py", "check_bot.py", "requirements.txt",
            "scripts/release_checks.py", "scripts/release_guard.py", "scripts/test_inventory.py",
            ".github/workflows/release-checks.yml", "deploy/deploy-shpakdnd.sh",
            "deploy/release_preflight.py", "deploy/preflight_data.py", "deploy/release_state.py",
            "deploy/systemd_state.py", "deploy/release_lkg.py", "deploy/legacy_lkg.py",
            "deploy/install-systemd-units.sh")
PHASES = ("source", "services", "tooling", "target_checkout", "dependencies", "snapshot",
          "migration", "preservation", "repeat_migration", "lkg", "release_checks", "source_recheck")


PRODUCTION_DEPENDENCIES = """import importlib.metadata, sys
from pip._vendor.packaging.requirements import Requirement
for raw in open(sys.argv[1], encoding='utf-8'):
    raw = raw.strip()
    if not raw or raw.startswith('#'):
        continue
    requirement = Requirement(raw)
    if requirement.url or requirement.extras:
        raise RuntimeError('Direct URL/extras require separate dependency review')
    if requirement.marker and not requirement.marker.evaluate():
        continue
    version = importlib.metadata.version(requirement.name)
    if version not in requirement.specifier:
        raise RuntimeError('Production dependency incompatible: '+requirement.name)
print('Production installed packages satisfy TARGET requirements')
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def full_sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DeployError("A full pinned Git commit SHA is required")
    return value


def tooling_hash(directory):
    digest = hashlib.sha256()
    files=[(name,Path(directory)/name) for name in BUNDLE]
    files.extend(("shared/"+name,shared_scripts(directory)/name) for name in SHARED_SCRIPTS)
    for name,file in files:
        if not file.is_file() or file.is_symlink():
            raise DeployError(f"Incomplete installed tooling: {name}")
        info=file.stat()
        if os.name=="posix" and (info.st_uid!=os.geteuid() or info.st_mode & 0o022):
            raise DeployError("Installed tooling ownership/permissions are unsafe")
        digest.update(name.encode() + b"\0" + file.read_bytes())
    return digest.hexdigest()


def secure_path(path, *, directory=False, owner=None):
    path = Path(path)
    info = path.lstat()
    owner = getattr(os, "geteuid", lambda: None)() if owner is None else owner
    if path.is_symlink() or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode)):
        raise DeployError("Evidence/log path must be a regular file/private directory")
    if os.name == "posix" and (info.st_uid != owner or info.st_mode & 0o077):
        raise DeployError("Evidence ownership/permissions are unsafe")
    return path


def private_directory(path):
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)
    secure_path(path, directory=True)
    return path.resolve()


def safe_environment(home, *, network=None, project=None):
    # No real .env, BOT_TOKEN, proxy credentials, PYTHONPATH or pip configuration.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ"}}
    env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home), TEMP=str(home), TMP=str(home),
               BOT_TOKEN="ci-test-token", BOT_TIMEZONE="Europe/Moscow", PYTHONUTF8="1",
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
               PIP_NO_CACHE_DIR="1", PIP_DISABLE_PIP_VERSION_CHECK="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    if network:
        env["PYTHONPATH"] = str(network) + os.pathsep + str(project)
    return env


def network_source(target):
    tree = ast.parse((Path(target) / "scripts/release_checks.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NETWORK_GUARD" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise DeployError("TARGET has no shared release checks network guard")


def write_private(path, content):
    path = Path(path)
    # Atomic replacement; only the controller owns the containing directory.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_evidence(path, report):
    data = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    write_private(path, data)
    write_private(str(path) + ".sha256", hashlib.sha256(data.encode()).hexdigest() + "\n")


def validate_evidence(path, *, old, target, tools, max_age=3600, now=None, rollback_db=None, live_db=None, expected_schema=None):
    full_sha(old); full_sha(target)
    if live_db and rollback_db:
        raise DeployError("Select exactly one live or rollback database")
    path = Path(path)
    secure_path(path.parent, directory=True)
    secure_path(path)
    checksum = secure_path(str(path) + ".sha256")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum.read_text().strip():
        raise DeployError("Preflight evidence checksum mismatch")
    try:
        report = json.loads(data)
        required = {"version", "old_sha", "target_sha", "baseline_sha", "stage_a_sha", "tooling_hash",
                    "started_at", "finished_at", "checks", "tests", "migration", "rollback_compatible",
                    "log_path", "log_sha256", "status", "source_schema", "target_schema", "head_discovered_tests", "baseline_discovered_tests"}
        if not required <= report.keys() or report["version"] != 1 or report["status"] != "PASS":
            raise DeployError("Preflight evidence is missing, incomplete or not PASS")
        if (report["old_sha"] != old or report["target_sha"] != target or report["baseline_sha"] != BASELINE_SHA
                or report["stage_a_sha"] != STAGE_A_SHA or report["tooling_hash"] != tooling_hash(tools)):
            raise DeployError("Preflight evidence SHA/tooling mismatch")
        finished = datetime.fromisoformat(report["finished_at"]).timestamp()
        started = datetime.fromisoformat(report["started_at"]).timestamp()
        moment = time.time() if now is None else now
        if not started <= finished <= moment + 5 or moment - finished > max_age:
            raise DeployError("Preflight evidence is stale or has invalid timestamps")
        if set(report["checks"]) != set(PHASES) or any(value != "OK" for value in report["checks"].values()):
            raise DeployError("Preflight checks are incomplete")
        tests = report["tests"]
        if (not isinstance(tests, dict) or tests["count"] <= 0 or tests["count"] != report["head_discovered_tests"]
                or any(tests[key] for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes"))):
            raise DeployError("Preflight test evidence is incomplete or failed")
        if report["migration"]["status"] != "OK" or not report["migration"]["repeat_preserved"]:
            raise DeployError("Migration evidence is incomplete")
        log = Path(report["log_path"])
        if log.parent.resolve() != path.parent.resolve():
            raise DeployError("Evidence log is outside the protected report directory")
        secure_path(log)
        if hashlib.sha256(log.read_bytes()).hexdigest()!=report["log_sha256"]:
            raise DeployError("Preflight log integrity mismatch")
        if report["rollback_compatible"] is not True and report["rollback_compatible"] is not False and report["rollback_compatible"]!="unknown":
            raise DeployError("Rollback compatibility result is invalid")
        if report["migration"].get("integrity")!="OK" or report["migration"].get("foreign_keys")!="OK":
            raise DeployError("SQLite migration checks are incomplete")
        if live_db or rollback_db:
            if expected_schema not in {"source", "target"}:
                raise DeployError("Explicit expected schema is required for live/rollback validation")
            database = live_db or rollback_db
            check_database(database)
            if schema_digest(database) != report[expected_schema + "_schema"]:
                raise DeployError("Live schema differs from expected " + expected_schema.upper())
        if report.get("installed_systemd"):
            from systemd_state import verify_binding
            verify_binding(report["installed_systemd"])
            from release_lkg import verify_binding as verify_lkg
            verify_lkg(report["lkg_binding"],report["installed_systemd"]["project"])
        if rollback_db and report.get("lkg_binding"):
            compatibility=report.get("lkg_compatibility",{})
            if not report["lkg_binding"]["sha"] or compatibility.get("sha")!=report["lkg_binding"]["sha"] or compatibility.get(expected_schema) is not True:
                raise DeployError("LKG compatibility for actual schema is false or unknown")
        elif rollback_db:
            if report["rollback_compatible"] == "unknown" or (expected_schema == "target" and report["rollback_compatible"] is not True):
                raise DeployError("Rollback is blocked: compatibility is false or unknown")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise DeployError("Preflight evidence has invalid fields") from error
    return report


def require_deployment_lock(db):
    """The manager owns the same inherited lock before any journal transition."""
    if os.name != "posix" or not stat.S_ISREG(os.fstat(9).st_mode):
        raise DeployError("Preflight requires the inherited deployment lock")
    lock_info=os.fstat(9)
    db_info=Path(db).stat()
    if (lock_info.st_uid!=os.geteuid() or lock_info.st_mode & 0o022
            or (lock_info.st_dev,lock_info.st_ino)==(db_info.st_dev,db_info.st_ino)):
        raise DeployError("Deployment lock ownership/permissions or DB alias is unsafe")
    import fcntl
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)


def logical_digest(db):
    """Hash complete logical contents, including metadata; never store user rows."""
    return hashlib.sha256(json.dumps(inventory_database(db, strict=True), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def deployment_path(evidence):
    return Path(evidence).with_suffix(".deployment.json")


def save_deployment(evidence, state):
    # File and containing directory are durable before any live writer starts.
    save_evidence(deployment_path(evidence), state)
    if os.name == "posix":
        fd = os.open(Path(evidence).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_deployment(evidence, *, old, target, tools):
    path = deployment_path(evidence)
    secure_path(path.parent, directory=True); secure_path(path)
    checksum = secure_path(str(path) + ".sha256")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum.read_text().strip():
        raise DeployError("Deployment state checksum mismatch")
    state = json.loads(raw)
    if (state.get("version") != 1 or state.get("old_sha") != old or state.get("target_sha") != target
            or state.get("tooling_hash") != tooling_hash(tools)
            or state.get("evidence_sha256") != hashlib.sha256(Path(evidence).read_bytes()).hexdigest()):
        raise DeployError("Deployment state evidence/SHA/tooling mismatch")
    phase = state.get("phase")
    histories = {"SOURCE": ["SOURCE"], "MIGRATION_STARTED": ["SOURCE", "MIGRATION_STARTED"],
                 "TARGET": ["SOURCE", "MIGRATION_STARTED", "TARGET"]}
    if phase not in histories or state.get("history") != histories[phase]:
        raise DeployError("Deployment DB is UNKNOWN/FAILED or has invalid operation history")
    if phase == "TARGET" and (state.get("runtime_success") is not True or not state.get("preserved")):
        raise DeployError("TARGET is not confirmed by successful runtime migration/preservation")
    if phase == "SOURCE" and state.get("runtime_success"):
        raise DeployError("SOURCE has invalid migration history")
    return state


def initialize_deployment(evidence, *, old, target, tools, db, backup):
    validate_evidence(evidence, old=old, target=target, tools=tools, live_db=db, expected_schema="source")
    if deployment_path(evidence).exists() or Path(str(deployment_path(evidence)) + ".sha256").exists():
        raise DeployError("Deployment state already exists; cannot reset migration history")
    backup, db = Path(backup), Path(db)
    if backup.is_symlink() or not backup.is_file() or backup.samefile(db):
        raise DeployError("Final backup must be an independent regular database")
    check_database(backup)
    compare_copy(inventory_database(backup, strict=True), db, strict=True)
    if schema_digest(backup) != schema_digest(db) or logical_digest(backup) != logical_digest(db):
        raise DeployError("Fresh final backup does not match SOURCE database")
    state = {"version": 1, "phase": "SOURCE", "history": ["SOURCE"], "old_sha": old,
             "target_sha": target, "tooling_hash": tooling_hash(tools), "db": str(db.resolve()),
             "backup": str(backup.resolve()), "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
             "source_data": logical_digest(backup), "runtime_success": False, "preserved": False,
             "startup_attempted": False, "evidence_sha256": hashlib.sha256(Path(evidence).read_bytes()).hexdigest()}
    save_deployment(evidence, state)
    return state


def verify_deployment(evidence, *, old, target, tools, db, expected_schema=None, rollback=False):
    state = read_deployment(evidence, old=old, target=target, tools=tools)
    phase = state["phase"]
    if phase not in {"SOURCE", "TARGET"} or (expected_schema and phase.lower() != expected_schema):
        raise DeployError("Deployment DB stage does not match expected confirmed state")
    db, backup = Path(db), Path(state["backup"])
    if db.is_symlink() or str(db.resolve()) != state["db"] or backup.is_symlink() or not backup.is_file() or backup.samefile(db):
        raise DeployError("Deployment DB/backup path mismatch")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != state["backup_sha256"]:
        raise DeployError("Final backup integrity mismatch")
    validate_evidence(evidence, old=old, target=target, tools=tools,
                      **({"rollback_db": db} if rollback else {"live_db": db}), expected_schema=phase.lower())
    if phase == "SOURCE":
        if state["startup_attempted"]:
            raise DeployError("SOURCE rollback blocked: startup may have written to live DB")
        if logical_digest(db) != state["source_data"]:
            raise DeployError("SOURCE data changed; migration/write history is not confirmed")
    else:
        # Revalidate both critical old data and the sealed post-migration state.
        compare_copy(inventory_database(backup, strict=True), db, strict=True)
        if logical_digest(db) != state["target_data"]:
            raise DeployError("TARGET data changed after verified runtime migration")
    return state


def execute_live_runtime(project, db, target, tools, python, bot_user):
    command = [str(python), str(Path(tools) / "release_preflight.py"), "runtime", "--short",
               "--project", str(project), "--db", str(db), "--sha", target]
    if bot_user:
        command = ["runuser", "-u", bot_user, "--", *command]
    process = subprocess.Popen(command, start_new_session=os.name == "posix")
    try:
        if process.wait(timeout=50):
            raise DeployError("Runtime migration process failed")
    except BaseException:
        if process.poll() is None:
            if os.name == "posix": os.killpg(process.pid, signal.SIGTERM)
            else: process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name == "posix": os.killpg(process.pid, signal.SIGKILL)
                else: process.kill()
                process.wait()
        raise


def migrate_live(evidence, *, old, target, tools, db, project, python=sys.executable, bot_user=None, runner=None):
    state = verify_deployment(evidence, old=old, target=target, tools=tools, db=db, expected_schema="source")
    state.update(phase="MIGRATION_STARTED", history=["SOURCE", "MIGRATION_STARTED"])
    save_deployment(evidence, state)  # Must finish durably before starting child.
    try:
        (runner or execute_live_runtime)(project, db, target, tools, python, bot_user)
        validate_evidence(evidence, old=old, target=target, tools=tools, live_db=db, expected_schema="target")
        compare_copy(inventory_database(state["backup"], strict=True), db, strict=True)
        state.update(phase="TARGET", history=["SOURCE", "MIGRATION_STARTED", "TARGET"],
                     runtime_success=True, preserved=True, target_data=logical_digest(db))
        save_deployment(evidence, state)
    except BaseException:
        state.update(phase="UNKNOWN", runtime_success=False, preserved=False)
        save_deployment(evidence, state)
        raise
    return state


def abort_deployment(evidence):
    # Only unsafe downgrade is possible here; no CLI can mark runtime success.
    path = deployment_path(evidence)
    if not path.exists(): return
    secure_path(path.parent, directory=True); secure_path(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["phase"] == "MIGRATION_STARTED" or state.get("startup_attempted"):
        state.update(phase="UNKNOWN", runtime_success=False, preserved=False)
        save_deployment(evidence, state)


class Interrupted(DeployError):
    pass


class Preflight:
    def __init__(self, *, project, db, old, target, evidence, workspace, tools, python=sys.executable,
                 bot_user=None, services=None, timeout=1200, lkg_path=None):
        self.project, self.db = Path(project).resolve(), Path(db).resolve()
        self.old, self.target = full_sha(old), full_sha(target)
        self.evidence, self.workspace = Path(evidence), Path(workspace)
        self.tools, self.python, self.bot_user = Path(tools).resolve(), str(python), bot_user
        self.services, self.timeout = services, timeout
        self.lkg_path=lkg_path or (os.environ.get("SHPAKDND_LKG","/var/lib/shpakdnd-release/last-known-good.json") if services else None)
        self.temp = None
        self.report = {"version": 1, "old_sha": old, "target_sha": target, "baseline_sha": BASELINE_SHA,
                       "stage_a_sha": STAGE_A_SHA, "started_at": utc_now(), "finished_at": None,
                       "checks": {name: "NOT RUN" for name in PHASES}, "tests": None,
                       "migration": {"status": "NOT RUN"}, "rollback_compatible": "unknown", "status": "FAIL"}

    def user_command(self, command):
        return ["runuser", "-u", self.bot_user, "--", *map(str, command)] if self.bot_user else list(map(str, command))

    def run_command(self, command, *, cwd=None, env=None, as_user=True):
        command = self.user_command(command) if as_user else list(map(str, command))
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", start_new_session=os.name == "posix")
        try:
            output, _ = process.communicate(timeout=self.timeout)
        except BaseException:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
            raise
        with self.log.open("a", encoding="utf-8") as stream:
            redact_output(self.project / ".env", io.StringIO(output), stream.write)
        if process.returncode:
            raise DeployError(f"Preflight command failed (exit {process.returncode}); see protected log")
        return output

    def git(self, *args, cwd=None):
        return self.run_command(["git", "-C", str(cwd or self.project), *args], env=self.env).strip()

    def step(self, name, operation):
        print(f"Preflight: {name}", flush=True)
        self.report["checks"][name] = "RUNNING"
        save_evidence(self.evidence, self.report)
        result = operation()
        self.report["checks"][name] = "OK"
        save_evidence(self.evidence, self.report)
        return result

    def source_check(self):
        if Path(self.git("rev-parse", "--show-toplevel")).resolve() != self.project:
            raise DeployError("Production project must be the repository root")
        if self.git("status", "--porcelain", "--untracked-files=all"):
            raise DeployError("Production worktree is dirty")
        if self.git("rev-parse", "HEAD") != self.old:
            raise DeployError("OLD SHA changed while preflight was running")
        for sha in (self.old, self.target, BASELINE_SHA, STAGE_A_SHA):
            self.git("cat-file", "-e", sha + "^{commit}")
        self.git("fsck", "--no-reflogs", "--full", self.target)
        self.git("merge-base", "--is-ancestor", BASELINE_SHA, self.target)
        paths = self.git("ls-tree", "-r", "--name-only", self.target).splitlines()
        if any(re.match(r"^(\.env$|\.venv(/|$)|(?:shpakdnd|timers)\.db($|[-.]))", p) for p in paths):
            raise DeployError("TARGET tracks production env/database/venv")
        if not set(REQUIRED) <= set(paths):
            raise DeployError("TARGET is missing required release/deployment sources")
        # Stage B introduces no application/schema/balance edits.
        if self.git("diff", "--name-only", STAGE_A_SHA, self.target, "--", "app", "bot.py", "check_bot.py"):
            raise DeployError("Stage B TARGET changes game code/catalog/schema; separate review required")

    def inspect_services(self):
        if self.services is None:
            # Library fixture use only; installed CLI always supplies units.
            return
        service, watcher, update = self.services
        def show(unit, field):
            return self.run_command(["systemctl", "show", "-p", field, "--value", unit], env=self.env, as_user=False).strip()
        for unit in self.services:
            if show(unit, "LoadState") != "loaded":
                raise DeployError("Required systemd unit is not loaded")
        if (show(service, "User") != self.bot_user or show(service, "WorkingDirectory") != str(self.project)
                or self.python not in show(service, "ExecStart") or show(watcher, "Triggers") != update):
            raise DeployError("Systemd configuration does not match project/python/user")
        states = {unit: self.run_command(["systemctl", "is-active", unit], env=self.env, as_user=False).strip()
                  for unit in (service, watcher)}
        if any(value != "active" for value in states.values()):
            raise DeployError("Bot and watcher must remain active during preflight")
        from systemd_state import verify_installed
        installed=verify_installed(os.environ.get("SHPAKDND_SYSTEMD_MANIFEST","/var/lib/shpakdnd-release/systemd-installation.json"),
                                   project=self.project,units=self.services,active=True)
        if installed["tooling_hash"]!=tooling_hash(self.tools):
            raise DeployError("Installed units/tooling versions differ")
        if "installed_systemd" in self.report and self.report["installed_systemd"]!=installed:
            raise DeployError("Installed systemd/helper changed during preflight")
        self.report["installed_systemd"]=installed
        # An approved read-only observer may run while preflight holds the lock.
        current = {**states, update: "safe observer", "pid": show(service, "MainPID"), "restarts": show(service, "NRestarts")}
        if not current["pid"].isdigit() or int(current["pid"]) <= 0:
            raise DeployError("Bot has no running PID during preflight")
        if "service_states" in self.report and self.report["service_states"] != current:
            raise DeployError("Production service changed/restarted during preflight")
        self.report["service_states"] = current

    def chown(self, path):
        if self.bot_user:
            import pwd
            account = pwd.getpwnam(self.bot_user)
            os.chown(path, account.pw_uid, account.pw_gid)

    def checkout(self, destination, sha):
        self.run_command(["git", "clone", "--no-hardlinks", "--no-checkout", str(self.project), str(destination)], env=self.env)
        self.git("checkout", "--detach", sha, cwd=destination)
        if self.git("rev-parse", "HEAD", cwd=destination) != sha:
            raise DeployError("Temporary checkout SHA does not equal pinned SHA")
        if (destination / ".env").exists():
            raise DeployError("Isolated checkout contains .env")
        return destination

    def dependencies(self, checkout):
        venv = self.temp / "target-venv"
        self.run_command([self.python, "-m", "venv", str(venv)], env=self.env)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.run_command([python, "-m", "pip", "install", "-r", checkout / "requirements.txt"], cwd=checkout, env=self.env)
        self.run_command([python, "-m", "pip", "check"], cwd=checkout, env=self.env)
        versions = self.run_command([python, "-m", "pip", "list", "--format=json"], cwd=checkout, env=self.env)
        self.report["dependency_versions_hash"] = hashlib.sha256(versions.encode()).hexdigest()
        # This stage never upgrades production venv. Reject a release that would
        # require missing/incompatible packages there, before maintenance.
        self.run_command([self.python, "-c", PRODUCTION_DEPENDENCIES, checkout / "requirements.txt"], cwd=checkout, env=self.env)
        self.report["production_dependencies"] = "compatible; no venv modification"
        return str(python)

    def probe(self, checkout, db, python, *, short=False):
        isolated_database(checkout, db, self.db)
        env = safe_environment(self.temp, network=self.network, project=checkout)
        env["BOT_TIMEZONE"]=self.env["BOT_TIMEZONE"]
        self.run_command([python, str(self.tools / "release_preflight.py"), "runtime", "--project", checkout,
                          "--db", db, "--forbid-db", self.db, "--sha", self.git("rev-parse", "HEAD", cwd=checkout),
                          *( ["--short"] if short else [])], cwd=checkout, env=env)

    def check_lkg(self, source_db, target_db):
        if not self.lkg_path:
            # Library-only fixture; CLI always supplies production units/LKG path.
            return
        from release_lkg import binding, verify_binding
        value=binding(self.lkg_path,self.project)
        self.report["lkg_binding"]=value
        result={"sha":value["sha"],"source":"unknown","target":"unknown"}
        self.report["lkg_compatibility"]=result
        if not value["sha"]:
            self.report["lkg_status"]="UNAVAILABLE; rollback blocked"
            return
        for stage,snapshot in (("source",source_db),("target",target_db)):
            checkout=self.checkout(self.temp/("lkg-"+stage),value["sha"])
            copy=checkout/"shpakdnd.db";backup_database(snapshot,copy);self.chown(copy)
            before=inventory_database(copy,strict=True);expected=schema_digest(copy)
            try:
                self.probe(checkout,copy,self.python)
                compare_copy(before,copy,strict=True)
                if schema_digest(copy)!=expected:raise DeployError("LKG initializer changes actual schema")
                result[stage]=True
            except Interrupted:raise
            except DeployError:result[stage]=False
        verify_binding(value,self.project)

    def network_guard(self,checkout):
        return network_source(checkout)

    def release_checks(self, checkout, python):
        report = self.temp / "release-checks.json"
        self.run_command([python, checkout / "scripts/release_checks.py", "--report", report], cwd=checkout, env=self.env)
        data = json.loads(report.read_text(encoding="utf-8"))
        if data["head"] != self.target or not data["checks"] or any(v != "OK" for v in data["checks"].values()):
            raise DeployError("TARGET release check evidence is incomplete or mismatched")
        tests = data["tests"]
        if not tests or tests["count"] != data["head_discovered_tests"] or any(tests[k] for k in ("failures", "errors", "skips", "expected_failures", "unexpected_successes")):
            raise DeployError("Full TARGET tests failed or skipped")
        self.report.update(tests=tests, head_discovered_tests=data["head_discovered_tests"],
                           baseline_discovered_tests=data["baseline_discovered_tests"], release_checks=data["checks"])

    def perform(self):
        # Resolve containment before mkdir/chmod or creating any temporary file.
        for directory in (self.workspace, self.evidence.parent):
            physical=directory.resolve()
            if physical == self.project or self.project in physical.parents or physical in self.project.parents:
                raise DeployError("Preflight workspace/evidence must be outside production/watcher project")
        private_directory(self.evidence.parent)
        if not self.workspace.exists():
            self.workspace.mkdir(parents=True, mode=0o711)
        info = self.workspace.lstat()
        if self.workspace.is_symlink() or not stat.S_ISDIR(info.st_mode) or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
            raise DeployError("Unsafe preflight workspace ownership/permissions")
        self.log = self.evidence.with_suffix(".log")
        write_private(self.log, "Preflight started; secrets are redacted.\n")
        self.report["log_path"] = str(self.log.resolve())
        # A run is FAIL until every phase and cleanup has completed.
        save_evidence(self.evidence, self.report)
        path = Path(tempfile.mkdtemp(prefix="shpakdnd-preflight-", dir=self.workspace))
        self.temp = path.resolve()
        try:
            self.chown(self.temp)
            self.env = safe_environment(self.temp)
            from dotenv import dotenv_values
            timezone_name=dotenv_values(self.project/".env",interpolate=False).get("BOT_TIMEZONE") or "Europe/Moscow"
            self.env["BOT_TIMEZONE"]=timezone_name
            self.report["config_timezone"]=timezone_name
            self.step("source", self.source_check)
            self.step("services", self.inspect_services)
            self.step("tooling", lambda: self.report.update(tooling_hash=tooling_hash(self.tools)))
            target = self.step("target_checkout", lambda: self.checkout(self.temp / "target", self.target))
            old = self.checkout(self.temp / "old", self.old)
            python = self.step("dependencies", lambda: self.dependencies(target))
            self.network = self.temp / "network"
            self.network.mkdir()
            (self.network / "sitecustomize.py").write_text(self.network_guard(target), encoding="utf-8")
            db = target / "shpakdnd.db"
            self.step("snapshot", lambda: backup_database(self.db, db))
            self.chown(db)
            source_copy=self.temp/"source-snapshot.db"
            backup_database(db,source_copy)
            before = inventory_database(db)
            if not {"mini_players", "mini_worlds", "mini_wallet_transactions", "mini_event_sessions"} <= before.keys():
                raise DeployError("Snapshot is not an initialized production Mini database")
            self.report["source_schema"] = schema_digest(db)
            self.step("migration", lambda: self.probe(target, db, python))
            changes = self.step("preservation", lambda: compare_copy(before, db))
            first = inventory_database(db, strict=True)
            self.step("repeat_migration", lambda: (self.probe(target, db, python), compare_copy(first, db, strict=True)))
            self.report["migration"] = {"status": "OK", "repeat_preserved": True, "integrity": "OK", "foreign_keys": "OK",
                                        "preservation": changes, "metadata_policy": "deploy/preflight_data.py::SERVICE_METADATA"}
            self.report["target_schema"] = schema_digest(db)
            rollback = old / "shpakdnd.db"
            backup_database(db, rollback)
            self.chown(rollback)
            rollback_before = inventory_database(rollback, strict=True)
            try:
                self.probe(old, rollback, self.python)
                compare_copy(rollback_before, rollback, strict=True)
                self.report["rollback_compatible"] = True
                self.report["rollback_status"] = "OK"
            except Interrupted:
                raise
            except DeployError:
                self.report["rollback_compatible"] = False
                self.report["rollback_status"] = "INCOMPATIBLE; code rollback blocked"
            self.step("lkg",lambda:self.check_lkg(source_copy,db))
            self.step("release_checks", lambda: self.release_checks(target, python))
            self.step("source_recheck", lambda: (self.source_check(), self.inspect_services()))
            if self.git("status", "--porcelain", "--untracked-files=all", cwd=target):
                raise DeployError("TARGET source changed during checks")
        except BaseException as error:
            for name, status in self.report["checks"].items():
                if status == "RUNNING":
                    self.report["checks"][name] = "FAIL"
            self.report["error"] = str(error) if isinstance(error, DeployError) else type(error).__name__
            raise
        finally:
            try:
                if (path.is_symlink() or path.resolve() != self.temp or self.temp.parent != self.workspace.resolve()
                        or not path.name.startswith("shpakdnd-preflight-")):
                    raise DeployError("Unsafe temporary cleanup path")
                shutil.rmtree(path)
            finally:
                self.report["finished_at"] = utc_now()
                self.report["log_sha256"] = hashlib.sha256(self.log.read_bytes()).hexdigest()
                save_evidence(self.evidence, self.report)
        self.report["status"] = "PASS"
        save_evidence(self.evidence, self.report)
        return self.report


def runtime_probe(project, db, sha, forbidden=None, *, short=False):
    # Runs as the bot user in a separate process; inspect config before writers.
    project = Path(project).resolve()
    if not short:
        isolated_database(project, db, forbidden)
    elif Path(db).is_symlink() or Path(db).resolve() != project / "shpakdnd.db":
        raise DeployError("Runtime DB path mismatch")
    sys.path.insert(0, str(project))
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != full_sha(sha):
        raise DeployError("Runtime checkout SHA mismatch")
    from app.config import BASE_DIR, DB_PATH, LEGACY_DB_PATH
    if (Path(BASE_DIR).resolve() != project or Path(DB_PATH).is_symlink() or Path(DB_PATH).resolve() != Path(db).resolve()
            or Path(LEGACY_DB_PATH).resolve() != project / "timers.db"):
        raise DeployError("Actual app.config DB_PATH is outside the verified checkout")
    if forbidden:
        isolated_database(project, DB_PATH, forbidden)
    if short:
        check_database(DB_PATH)
        from app.db.schema import init_db
        from app.mini.schema import init_mini_db
        from app.mini.boss.schema import init_boss_db
        init_db(); init_mini_db(); init_boss_db()
        check_database(DB_PATH)
        print("Short runtime migrations/integrity/FK: OK")
    else:
        import check_bot
        if Path(check_bot.DB_PATH).resolve() != Path(db).resolve():
            raise DeployError("check_bot DB_PATH mismatch")
        check_bot.main()
        check_database(DB_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    for name in ("project", "db", "old", "target", "evidence", "workspace", "bot-user", "service", "watcher", "update-service"):
        run.add_argument("--" + name, required=True)
    run.add_argument("--python", default=sys.executable)
    validation = commands.add_parser("validate")
    for name in ("old", "target", "evidence"):
        validation.add_argument("--" + name, required=True)
    database = validation.add_mutually_exclusive_group()
    database.add_argument("--rollback-db")
    database.add_argument("--live-db")
    validation.add_argument("--expected-schema", choices=("source", "target"))
    invalidation = commands.add_parser("invalidate")
    invalidation.add_argument("--evidence", required=True)
    for name in ("deployment-init", "live-runtime", "deployment-check", "deployment-startup"):
        command = commands.add_parser(name)
        for field in ("evidence", "old", "target", "db"):
            command.add_argument("--" + field, required=True)
        if name == "deployment-init": command.add_argument("--backup", required=True)
        if name == "deployment-startup": command.add_argument("--startup-sha",required=True)
        if name == "live-runtime":
            command.add_argument("--project", required=True)
            command.add_argument("--python", default=sys.executable)
            command.add_argument("--bot-user", required=True)
        if name == "deployment-check":
            command.add_argument("--expected-schema", choices=("source", "target"))
            command.add_argument("--rollback", action="store_true")
    abort = commands.add_parser("deployment-abort")
    abort.add_argument("--evidence", required=True)
    runtime = commands.add_parser("runtime")
    for name in ("project", "db", "sha"):
        runtime.add_argument("--" + name, required=True)
    runtime.add_argument("--forbid-db")
    runtime.add_argument("--short", action="store_true")
    args = parser.parse_args(argv)
    tools = Path(__file__).resolve().parent
    try:
        if args.action == "live-runtime":
            def runtime_interrupted(signum, frame):
                raise Interrupted(f"Live migration interrupted by signal {signum}")
            signal.signal(signal.SIGTERM, runtime_interrupted)
            signal.signal(signal.SIGINT, runtime_interrupted)
        if args.action in {"deployment-init", "live-runtime", "deployment-check", "deployment-startup"}:
            require_deployment_lock(args.db)
            gate=validate_evidence(args.evidence,old=args.old,target=args.target,tools=tools)
            if not gate.get("installed_systemd") or not gate.get("lkg_binding"):
                raise DeployError("Installed systemd/LKG verification missing")
            common = dict(old=args.old, target=args.target, tools=tools, db=args.db)
            if args.action == "deployment-init":
                state = initialize_deployment(args.evidence, backup=args.backup, **common)
            elif args.action == "live-runtime":
                state = migrate_live(args.evidence, project=args.project, python=args.python, bot_user=args.bot_user, **common)
            else:
                state = verify_deployment(args.evidence, expected_schema=getattr(args, "expected_schema", None),
                                          rollback=getattr(args, "rollback", False), **common)
                if args.action == "deployment-startup":
                    report=validate_evidence(args.evidence,**{k:v for k,v in common.items() if k!="db"})
                    full_sha(args.startup_sha)
                    if args.startup_sha!=args.target or state["phase"]!="TARGET":
                        from release_lkg import rollback_target
                        if rollback_target(args.evidence,Path(args.db).parent)!=args.startup_sha:
                            raise DeployError("Startup is not the confirmed LKG")
                        verify_deployment(args.evidence,rollback=True,**common)
                    state.update(startup_attempted=True,startup_sha=args.startup_sha,
                                 launch_requested_monotonic=time.monotonic_ns()//1000,launch_requested_at=utc_now())
                    save_deployment(args.evidence, state)
            print("Deployment DB stage: " + state["phase"])
        elif args.action == "deployment-abort":
            abort_deployment(args.evidence)
        elif args.action == "run":
            require_deployment_lock(args.db)
            def interrupted(signum, frame):
                raise Interrupted(f"Preflight interrupted by signal {signum}")
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
            operation = Preflight(project=args.project, db=args.db, old=args.old, target=args.target,
                                  evidence=args.evidence, workspace=args.workspace, tools=tools,
                                  python=args.python, bot_user=args.bot_user,
                                  services=(args.service, args.watcher, args.update_service))
            operation.perform()
            validate_evidence(args.evidence, old=args.old, target=args.target, tools=tools)
            print("Preflight PASS: " + args.evidence)
        elif args.action == "invalidate":
            path = Path(args.evidence)
            secure_path(path.parent, directory=True); secure_path(path)
            report = json.loads(path.read_text(encoding="utf-8"))
            report["status"] = "FAIL"
            report["error"] = "Deployment/preflight command interrupted or failed; evidence invalidated"
            save_evidence(path, report)
        elif args.action == "validate":
            initial=validate_evidence(args.evidence,old=args.old,target=args.target,tools=tools)
            if not initial.get("installed_systemd") or not initial.get("lkg_binding"):
                raise DeployError("Installed systemd/LKG verification missing")
            if args.live_db or args.rollback_db:
                if args.expected_schema not in {"source", "target"}:
                    raise DeployError("Live DB validation requires explicit expected schema")
                verify_deployment(args.evidence, old=args.old, target=args.target, tools=tools,
                                  db=args.live_db or args.rollback_db, expected_schema=args.expected_schema,
                                  rollback=bool(args.rollback_db))
            report = validate_evidence(args.evidence, old=args.old, target=args.target, tools=tools,
                                       rollback_db=args.rollback_db, live_db=args.live_db, expected_schema=args.expected_schema)
            print(f"Preflight evidence: PASS; tests={report['tests']['count']}; rollback_compatible={report['rollback_compatible']}")
        else:
            runtime_probe(args.project, args.db, args.sha, args.forbid_db, short=args.short)
        return 0
    except Exception as error:
        print("ERROR: " + (str(error) if isinstance(error, DeployError) else type(error).__name__), file=sys.stderr)
        return 143 if isinstance(error, Interrupted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
