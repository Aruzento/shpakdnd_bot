"""Read-only, stdlib-only external Story boundary and immutable content receipt."""

import hashlib
import json
from pathlib import Path

APPROVED_STORY_SHA256 = "f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2"
REQUIRED_FILE = "tayna_propavshey_ovtsy.json"


class ContentError(ValueError):
    def __init__(self, reason, exit_code=1):
        super().__init__(reason)
        self.exit_code = exit_code


def external_directory(value, project):
    raw = Path(value)
    if not raw.is_absolute():
        raise ContentError("INVALID_STORIES_PATH_NOT_ABSOLUTE")
    for part in (raw, *raw.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ContentError("INVALID_STORIES_SYMLINK")
    path = raw.resolve()
    checkout = Path(project).resolve()
    if path == checkout or checkout in path.parents or any((p / ".git").exists() for p in (path, *path.parents)):
        raise ContentError("INVALID_STORIES_INSIDE_GIT")
    if not path.exists():
        raise ContentError("BLOCKED_BY_INPUT_STORIES_DIRECTORY", 2)
    if not path.is_dir() or not path.stat().st_mode & 0o444:
        raise ContentError("INVALID_STORIES_NOT_READABLE")
    return path


def fingerprint(value, project):
    directory = external_directory(value, project)
    result = {}
    for file in sorted(directory.glob("*.json")):
        if file.is_symlink() or not file.is_file() or file.resolve().parent != directory:
            raise ContentError("INVALID_STORY_FILE_BOUNDARY")
        if file.stat().st_size > 2000000 or not file.stat().st_mode & 0o444:
            raise ContentError("INVALID_STORY_FILE_SIZE_OR_PERMISSIONS")
        result[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def approved_story(value, project, discover):
    directory = external_directory(value, project)
    before = fingerprint(directory, project)
    stories, errors = discover(directory)
    if errors:
        raise ContentError("INVALID_STORY_CATALOG: " + ",".join(sorted(errors)))
    if REQUIRED_FILE not in before:
        raise ContentError("BLOCKED_BY_INPUT_REQUIRED_STORY", 2)
    if before[REQUIRED_FILE] != APPROVED_STORY_SHA256:
        raise ContentError("INVALID_APPROVED_STORY_HASH")
    story = stories.get("tayna_propavshey_ovtsy")
    if not story or story.get("schema_version") != 2 or story.get("visit_policy") != "once":
        raise ContentError("INVALID_APPROVED_STORY_CONTRACT")
    nodes = story["nodes"]
    if (len(nodes) != 53 or sum(len(n.get("choices", [])) for n in nodes.values()) != 113
            or sum(n.get("end") is True for n in nodes.values()) != 7):
        raise ContentError("INVALID_APPROVED_STORY_GRAPH")
    if before != fingerprint(directory, project):
        raise ContentError("INVALID_STORIES_CHANGED_DURING_CHECK")
    return dict(directory=str(directory), files=before, codes=sorted(stories))
