"""Release gate for the explicit external target, never publishes or opens a live DB."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.release_content import ContentError, approved_story
from app.stories.catalog import discover


def check(directory):
    result = dict(CODE_CONTENT_PASS="NOT_VERIFIED", TARGET_STORIES_DIR_READY="NOT_VERIFIED",
                  STRICT_CONTENT_TARGET_PASS="NOT_VERIFIED", production_publication=False)
    try:
        receipt = approved_story(directory, ROOT, discover)
        result.update(TARGET_STORIES_DIR_READY="PASS", **receipt)
        env = dict(os.environ, STORIES_DIR=str(directory))
        checks = []
        for args in (["tools/validate_story_content.py", "--directory", str(directory)],
                     ["-m", "app.mini.mythic.content", "--strict-content"]):
            proc = subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8")
            checks.append(dict(command=args, exit_code=proc.returncode, output=proc.stdout))
            if proc.returncode:
                result.update(checks=checks, reason="TARGET_CONTENT_GATE_FAILED")
                return result, proc.returncode if proc.returncode in (1, 2) else 1
        from deploy.release_content import fingerprint
        if receipt["files"] != fingerprint(directory, ROOT):
            raise ContentError("INVALID_STORIES_CHANGED_DURING_CHECK")
        result.update(CODE_CONTENT_PASS="PASS", STRICT_CONTENT_TARGET_PASS="PASS", checks=checks)
        return result, 0
    except (ContentError, OSError, ValueError) as error:
        result.update(reason=str(error), TARGET_STORIES_DIR_READY="BLOCKED", STRICT_CONTENT_TARGET_PASS="BLOCKED")
        return result, getattr(error, "exit_code", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not (os.getenv("DND_CHECK_SANDBOX") or os.getenv("RELEASE_SANDBOX")):
        parser.error("Use scripts/run_isolated_checks.py; explicit target read only")
    result, code = check(args.directory)
    raw = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(raw, encoding="utf-8")
    print(raw)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
