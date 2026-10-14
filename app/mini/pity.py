
"""Exact-rarity independent counters for standard pulls only."""
from app.mini.editorial.registry import LiveMapping
from app.mini.editorial import text as editorial_text, screen as editorial_screen
LIMITS = {'uncommon': 10, 'rare': 25, 'legendary': 85}


def read(conn, player_id):
    row = conn.execute('SELECT uncommon_count, rare_count, legendary_count FROM mini_gacha_pity WHERE player_id=?', (player_id,)).fetchone()
    return dict(zip(LIMITS, row or (0, 0, 0)))


def required_rarity(counts):
    return next((rarity for rarity in reversed(LIMITS) if counts[rarity] >= LIMITS[rarity] - 1), None)


def advance(conn, player_id, counts, rarity):
    updated = {key: 0 if key == rarity else min(limit, counts[key] + 1) for key, limit in LIMITS.items()}
    conn.execute('''INSERT INTO mini_gacha_pity(player_id, uncommon_count, rare_count, legendary_count)
        VALUES(?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET
        uncommon_count=excluded.uncommon_count, rare_count=excluded.rare_count,
        legendary_count=excluded.legendary_count, updated_at=CURRENT_TIMESTAMP''', (player_id, *updated.values()))
    return updated


@editorial_screen
def format_progress(counts):
    labels = LiveMapping({key: "rarity."+key for key in LIMITS}, terms=True, decorated=True)
    return editorial_text('pity.format_progress.line_04') + '\n'.join(
        f'{labels[key]}: {counts.get(key, 0)}/{limit}' + (editorial_text('pity.format_progress.line_05') if counts.get(key, 0) >= limit else '')
        for key, limit in LIMITS.items())
