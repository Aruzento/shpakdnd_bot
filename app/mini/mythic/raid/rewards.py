"""R09 exact conserved allocation; ties are chosen once by finalizer."""

import secrets


def allocate(original_max_hp, contributions, *, chooser=None):
    positive = {
        int(pid): int(damage) for pid, damage in contributions.items() if damage > 0
    }
    if not positive:
        return []
    chooser = chooser or secrets.choice
    ranks = []
    for damage in sorted(set(positive.values()), reverse=True):
        tied = sorted(pid for pid in positive if positive[pid] == damage)
        while tied:
            chosen = chooser(tied)
            if chosen not in tied:
                raise ValueError("Invalid tie choice")
            ranks.append(chosen)
            tied.remove(chosen)
    fund = (int(original_max_hp) + 4999) // 5000
    total = sum(positive.values())
    shares = {pid: fund * damage // total for pid, damage in positive.items()}
    rank_order = {pid: i for i, pid in enumerate(ranks)}
    leftovers = fund - sum(shares.values())
    order = sorted(
        positive, key=lambda p: (-(fund * positive[p] % total), rank_order[p])
    )
    for pid in order[:leftovers]:
        shares[pid] += 1
    return [
        {
            "player_id": pid,
            "rank": i + 1,
            "damage": positive[pid],
            "base": 1,
            "share": shares[pid],
            "top_bonus": (15, 10, 5)[i] if i < 3 else 0,
            "amount": 1 + shares[pid] + ((15, 10, 5)[i] if i < 3 else 0),
        }
        for i, pid in enumerate(ranks)
    ]
