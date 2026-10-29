"""Real release-content gates: PASS0, missing2, invalid1 in disposable external dirs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    sandbox = os.getenv("DND_CHECK_SANDBOX") or os.getenv("RELEASE_SANDBOX")
    if not sandbox:
        raise SystemExit("Use isolated harness/CI; never point this tool at live content")
    package = ROOT / "content/stories/unpublished/tayna_propavshey_ovtsy"
    report = []
    with tempfile.TemporaryDirectory(prefix="dnd-release-content-fixture-") as folder:
        directory = Path(folder)
        file = directory / "tayna_propavshey_ovtsy.json"
        raw = (package / file.name).read_bytes()
        before_hash = hashlib.sha256(raw).hexdigest()
        for case, expected in (("full", 0), ("missing", 2), ("invalid", 1)):
            if case == "full":
                file.write_bytes(raw)
            elif case == "missing":
                file.unlink()
            else:
                file.write_text("{", encoding="utf-8")
            before = {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.iterdir()}
            env = dict(os.environ)
            command = [sys.executable, "tools/validate_release_content.py", "--directory", str(directory)]
            proc = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            assert proc.returncode == expected, proc.stdout
            assert before == {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.iterdir()}
            report.append(dict(case=case, expected_exit=expected, exit_code=proc.returncode,
                               command=command, output=proc.stdout))
        assert hashlib.sha256((package/file.name).read_bytes()).hexdigest() == before_hash
    output = Path(sandbox)/"release-content-cases.json"
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(status="PASS", cases={r["case"]:r["exit_code"] for r in report},
                          live_stories="NOT_CHECKED_OR_PUBLISHED", evidence=str(output))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
