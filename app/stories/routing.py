"""Pure route resolution against the frozen story, retaining source choice indices."""


def ending_reachable_nodes(story, blocked=()):
    """Reverse BFS: exactly the nodes with a path to an unblocked real ending.

    Any reachable walk contains a simple path. Removing blocked vertices therefore
    proves there is a continuation without revisiting the group's committed nodes.
    """
    nodes = story["nodes"]
    blocked = set(blocked)
    reverse = {nid: [] for nid in nodes if nid not in blocked}
    stack = []
    for nid in reverse:
        node = nodes[nid]
        if node.get("end") is True:
            stack.append(nid)
        for choice in node.get("choices", []):
            if choice["next"] in reverse:
                reverse[choice["next"]].append(nid)
    reachable = set(stack)
    while stack:
        for parent in reverse[stack.pop()]:
            if parent not in reachable:
                reachable.add(parent)
                stack.append(parent)
    return reachable


def available_choices(story, node_id, visited=()):
    """Return (original zero-based index, choice), never renumber after filtering."""
    choices = list(enumerate(story["nodes"][node_id].get("choices", [])))
    if story["schema_version"] == 1:
        return choices
    # The current node must also be blocked, including when called outside SQLite.
    allowed = ending_reachable_nodes(story, set(visited) | {node_id})
    return [(index, choice) for index, choice in choices if choice["next"] in allowed]
