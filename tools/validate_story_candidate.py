"""Validate the unpublished author package in disposable external storage only."""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.stories.catalog import directory, discover
from app.stories.routing import available_choices


def main():
    sandbox = os.getenv("DND_CHECK_SANDBOX") or os.getenv("RELEASE_SANDBOX")
    if not sandbox:
        raise SystemExit("Use the isolated check harness or the Linux CI sandbox")
    package = ROOT / "content/stories/unpublished/tayna_propavshey_ovtsy"
    for name, digest in json.loads((package / "MANIFEST.json").read_text(encoding="utf-8")).items():
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Author package hash mismatch: " + name)
    target = directory(tempfile.mkdtemp(prefix="dnd-story-candidate-"))
    shutil.copyfile(package / "tayna_propavshey_ovtsy.json", target / "author.json")
    stories, errors = discover(target)
    assert not errors and len(stories) == 1, errors
    story = stories["tayna_propavshey_ovtsy"]
    rng = random.Random(20261026)
    endings = Counter()
    longest = 0
    for _ in range(10000):
        node, visited = story["start_node"], set()
        while True:
            assert node not in visited
            visited.add(node)
            if story["nodes"][node].get("end"):
                endings[node] += 1
                break
            choices = available_choices(story, node, visited)
            assert choices, (node, sorted(visited))
            node = rng.choice(choices)[1]["next"]
        longest = max(longest, len(visited))
    assert set(endings) == {"ending_k" + str(n) for n in range(1, 8)}
    env = dict(os.environ, STORIES_DIR=str(target))
    checks = []
    for command in (["tools/validate_story_content.py", "--directory", str(target)],
                    ["-m", "app.mini.mythic.content", "--strict-content"]):
        result = subprocess.run([sys.executable, *command], env=env, cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8")
        checks.append(dict(command=command, exit_code=result.returncode, output=result.stdout))
        assert result.returncode == 0, result.stdout
    report = dict(status="PASS", production_publication=False, schema_version=2,
                  nodes=len(story["nodes"]), choices=sum(len(n.get("choices", [])) for n in story["nodes"].values()),
                  seed=20261026, runs=10000, endings=dict(sorted(endings.items())),
                  max_path_nodes=longest, directory=str(target), checks=checks)
    output = Path(sandbox) / "story-candidate.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False))
    print("Evidence:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
