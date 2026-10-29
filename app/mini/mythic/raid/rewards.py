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


def compensation(original_max_hp, contributions):
    """O01 partial pool; arbitrary-precision integers, stable player-id tie break."""
    if type(original_max_hp) is not int or not 0 < original_max_hp <= 2**63 - 1:
        raise ValueError("Invalid original HP")
    positive = {}
    for pid, damage in contributions.items():
        if (
            type(pid) is not int
            or type(damage) is not int
            or not 0 <= damage <= 2**63 - 1
        ):
            raise ValueError("Invalid actual contribution")
        if damage > 0:
            positive[pid] = damage
    total = sum(positive.values())
    fund = (original_max_hp + 4999) // 5000
    pool = fund * min(total, original_max_hp) // original_max_hp
    shares = (
        {pid: pool * damage // total for pid, damage in positive.items()}
        if total
        else {}
    )
    order = (
        sorted(positive, key=lambda pid: (-(pool * positive[pid] % total), pid))
        if total
        else []
    )
    for pid in order[: pool - sum(shares.values())]:
        shares[pid] += 1
    ranking = sorted(positive, key=lambda pid: (-positive[pid], pid))
    rewards = [
        {
            "player_id": pid,
            "rank": index + 1,
            "damage": positive[pid],
            "amount": shares[pid],
            "arise": [],
        }
        for index, pid in enumerate(ranking)
    ]
    assert sum(shares.values()) == pool
    return {
        "cancel_mode": "with_reward",
        "M": original_max_hp,
        "D": total,
        "F": fund,
        "P": pool,
        "tie_break": "remainder_desc_player_id_asc",
        "rewards": rewards,
    }
