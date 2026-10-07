"""Boss victory extraction uses canonical Arise config, never a summoner label."""
from app.mini.combat.hero_abilities.engine import arise_chance, _roll_success
from app.mini.boss.loadouts import battle_loadout


def extract_in_transaction(conn,boss,participant,*,roller=None,now=None):
    if not conn.in_transaction: raise RuntimeError('Shadow extraction needs a transaction.')
    boss,participant=dict(boss),dict(participant)
    if not boss.get('shadow_extractable',True) or not boss.get('shadow_hero_code') or not participant.get('hit_count'):
        return None
    chance=arise_chance(battle_loadout(participant['hero_snapshot_json']))
    if chance is None: return None
    hero=conn.execute("SELECT id,code FROM mini_heroes WHERE code=? AND rarity='shadow'",(boss['shadow_hero_code'],)).fetchone()
    if not hero: return None
    key=(boss['id'],participant['player_id'],participant['hero_id'])
    if conn.execute('SELECT 1 FROM mini_shadow_rolls WHERE boss_id=? AND player_id=? AND hero_id=?',key).fetchone(): return None
    success=(roller or _roll_success)(chance)
    conn.execute('INSERT INTO mini_shadow_rolls VALUES(?,?,?,?,?,?)',(*key,chance,hero['code'],int(success)))
    if success:
        if conn.execute('SELECT 1 FROM mini_village_residents WHERE player_id=? AND hero_id=?',
                        (participant['player_id'],hero['id'])).fetchone():
            from app.mini.village.service import settle, timestamp
            settle(conn,participant['player_id'],timestamp(now))
        conn.execute('''INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)
            ON CONFLICT(player_id,hero_id) DO UPDATE SET stars=mini_player_heroes.stars+1,copies=mini_player_heroes.copies+1''',
            (participant['player_id'],hero['id']))
    return {'type':'arise','success':success,'chance_percent':chance,'shadow_code':hero['code'],
        'message':f"🌑 Arise: получена тень {hero['code']}." if success else '🌑 Arise: тень ускользнула.'}
