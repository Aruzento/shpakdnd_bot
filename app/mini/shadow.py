"""Boss victory extraction uses canonical Arise config, never a summoner label."""
from app.mini.combat.hero_abilities.engine import arise_chance, _roll_success
from app.mini.boss.loadouts import battle_loadout


def eligible_shadow_target(conn, boss):
    boss = dict(boss)
    if not boss.get('shadow_extractable', True) or not boss.get('shadow_hero_code'):
        return None
    return conn.execute("SELECT id,code FROM mini_heroes WHERE code=? AND rarity='shadow'", (boss['shadow_hero_code'],)).fetchone()


def extract_in_transaction(conn,boss,participant,*,roller=None,now=None):
    if not conn.in_transaction: raise RuntimeError('Shadow extraction needs a transaction.')
    boss,participant=dict(boss),dict(participant)
    if not boss.get('shadow_extractable',True) or not boss.get('shadow_hero_code') or not participant.get('hit_count'):
        return None
    snapshot = battle_loadout(participant['hero_snapshot_json'])
    if int(boss.get('rules_version',0))>=15:
        import json
        from app.mini.combat.arise import sources,award
        target={**boss,'shadow_extractable':boss.get('shadow_extractable',True)}
        results=award(conn,'boss',boss['id'],participant['player_id'],target,True,sources(snapshot,team=json.loads(boss.get('team_state_json') or '{}')),now=now,roller=roller or _roll_success)
        if not results:return None
        success=any(r['success'] for r in results)
        # Preserve the legacy audit summary while recording all independent sources.
        conn.execute('INSERT OR IGNORE INTO mini_shadow_rolls VALUES(?,?,?,?,?,?)',(boss['id'],participant['player_id'],participant['hero_id'],results[-1]['chance'],results[-1]['shadow_code'],int(success)))
        return {'type':'arise','success':success,'sources':results,'shadow_code':results[-1]['shadow_code'],'message':f"🌑 Arise: {'получена тень' if success else 'тень ускользнула'} {results[-1]['shadow_code']}."}
    chance = 100 if snapshot.get('spirit_lantern') else arise_chance(snapshot)
    if chance is None: return None
    hero=conn.execute("SELECT id,code FROM mini_heroes WHERE code=? AND rarity='shadow'",(boss['shadow_hero_code'],)).fetchone()
    if not hero: return None
    key=(boss['id'],participant['player_id'],participant['hero_id'])
    if conn.execute('SELECT 1 FROM mini_shadow_rolls WHERE boss_id=? AND player_id=? AND hero_id=?',key).fetchone(): return None
    success = True if snapshot.get("spirit_lantern") else (roller or _roll_success)(chance)
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
