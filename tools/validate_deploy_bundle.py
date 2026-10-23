"""Verify the protected bundle, allowing only the authorized logger signal fix."""

from pathlib import Path
import subprocess

BASE = "d9856b2038a36490f93a4f5613ba7887698ac7e8"
ROOT = Path(__file__).resolve().parents[1]
OLD = 'as_bot "$PYTHON" "$TOOLS/deploy_helpers.py" --redact-env "$PROJECT/.env" | tee -a "$LOG"'
NEW = 'as_bot "$PYTHON" -c \'import runpy,signal,sys; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); runpy.run_path(sys.argv.pop(1),run_name="__main__")\' "$TOOLS/deploy_helpers.py" --redact-env "$PROJECT/.env" | tee -i -a "$LOG"'
PATHS = (
    "deploy/deploy-shpakdnd.sh",
    "deploy/deploy_helpers.py",
    "deploy/install-deploy-shpakdnd.sh",
    "deploy/sqlite-deploy.py",
    "deploy/telegram-deploy-notice.py",
    "deploy/pvp_ops.py",
    "tests/v1_3_3/test_deploy_shell.py",
    "tests/v1_4_3_1/test_deploy_confirm.py",
)


def main():
    for path in PATHS:
        before = subprocess.check_output(
            ["git", "show", BASE + ":" + path], cwd=ROOT
        ).decode("utf-8")
        after = (ROOT / path).read_text(encoding="utf-8-sig")
        if path == "deploy/deploy-shpakdnd.sh":
            if after.count(NEW) != 1:
                raise AssertionError("Exact authorized logger call is required")
            after = after.replace(NEW, OLD)
        if before != after:
            raise AssertionError(
                "Protected file changed outside the authorized logger fix: " + path
            )
    print(
        "PASS: protected bundle; sole authorized change is redactor SIGINT/SIGTERM handling"
    )


if __name__ == "__main__":
    main()
