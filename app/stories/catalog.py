"""Non-executable DAG story catalog, discovered at list/start without restart."""

import json
import os
import re
from pathlib import Path


def validate(data):
    if not isinstance(data, dict) or (
        type(data.get("schema_version")) is not int or data["schema_version"] != 1
    ):
        raise ValueError("schema_version must be 1")
    if set(data) - {
        "schema_version",
        "code",
        "title",
        "description",
        "start_node",
        "nodes",
    }:
        raise ValueError("Unknown story fields")
    if not isinstance(data.get("code"), str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,48}", data["code"]
    ):
        raise ValueError("Invalid story code")
    for key, limit in (("title", 150), ("description", 1000)):
        if (
            not isinstance(data.get(key), str)
            or not data[key].strip()
            or len(data[key].encode("utf-16-le")) // 2 > limit
        ):
            raise ValueError("Invalid " + key)
    nodes = data.get("nodes")
    if (
        not isinstance(nodes, dict)
        or not 1 <= len(nodes) <= 1000
        or data.get("start_node") not in nodes
    ):
        raise ValueError("Invalid nodes/start_node")
    incoming = dict.fromkeys(nodes, 0)
    edges = {}
    for nid, node in nodes.items():
        if (
            not isinstance(nid, str)
            or not nid
            or len(nid) > 64
            or not isinstance(node, dict)
        ):
            raise ValueError("Invalid node")
        if set(node) - {"text", "choices", "end"}:
            raise ValueError("Unknown node fields")
        if (
            not isinstance(node.get("text"), str)
            or not node["text"].strip()
            or len(node["text"].encode("utf-16-le")) // 2 > 3500
        ):
            raise ValueError("Invalid node text")
        if node.get("end") is True:
            if node.get("choices"):
                raise ValueError("Ending cannot have choices")
            edges[nid] = []
            continue
        if "end" in node and node["end"] is not False:
            raise ValueError("end must be boolean")
        choices = node.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= 20:
            raise ValueError("Nonfinal node needs choices")
        identifiers = set()
        edges[nid] = []
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"id", "text", "next"}:
                raise ValueError("Invalid choice fields")
            cid = choice["id"]
            if (
                not isinstance(cid, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,20}", cid)
                or cid in identifiers
            ):
                raise ValueError("Invalid/duplicate choice ID")
            if (
                not isinstance(choice["text"], str)
                or not choice["text"].strip()
                or len(choice["text"]) > 120
            ):
                raise ValueError("Invalid choice text")
            target = choice["next"]
            if not isinstance(target, str) or target not in nodes:
                raise ValueError("Missing next node")
            identifiers.add(cid)
            edges[nid].append(target)
            incoming[target] += 1
    reachable = set()
    stack = [data["start_node"]]
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(edges[node])
    if reachable != set(nodes):
        raise ValueError("Unreachable nodes")
    queue = [n for n, degree in incoming.items() if degree == 0]
    visited = 0
    while queue:
        n = queue.pop()
        visited += 1
        for target in edges[n]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise ValueError("Story cycles are forbidden")
    return data


def directory():
    path = Path(os.getenv("STORIES_DIR", "/var/lib/shpakdnd/stories")).resolve()
    checkout = Path(__file__).resolve().parents[2]
    if (
        path == checkout
        or checkout in path.parents
        or any((parent / ".git").exists() for parent in (path, *path.parents))
    ):
        raise ValueError("STORIES_DIR must be outside Git checkout/watcher")
    return path


def discover(path=None):
    path = Path(path) if path is not None else directory()
    stories = {}
    errors = {}
    duplicates = set()
    for file in sorted(path.glob("*.json")):
        try:
            if file.is_symlink() or file.stat().st_size > 2000000:
                raise ValueError("Story file must be local and <=2MB")
            story = validate(json.loads(file.read_text(encoding="utf-8")))
            code = story["code"]
            if code in stories or code in duplicates:
                stories.pop(code, None)
                duplicates.add(code)
                raise ValueError("Duplicate story code across files")
            stories[code] = story
        except (ValueError, OSError) as error:
            errors[file.name] = str(error)
    return stories, errors
