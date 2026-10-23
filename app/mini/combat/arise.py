"""Independent, persisted Arise rolls; at most one shadow per player and combat."""

import json
from app.mini.combat.hero_abilities.engine import _roll_success, arise_chance


def sources(hero, *, mode="normal", team=None):
    prop = hero.get("passives", {}).get(mode, {})
    result = []
    if mode == "normal" and hero.get("spirit_lantern"):
        result.append(("spirit_lantern", 100))
    definition = prop.get("definition", {})
    effects = definition.get("effects", [])
    for index, effect in enumerate(effects):
        if effect.get("type") == "shadow_extraction":
            result.append(
                (
                    f'ability:{prop.get("key",hero.get("passive_key"))}:{index}',
                    int(hero.get("arise_chance_percent", effect["chance_percent"])),
                )
            )
    if not prop and mode == "normal":
        chance = arise_chance(hero)
        if chance is not None:
            result.append(("legacy_ability", chance))
    key = prop.get("key", hero.get("passive_key") if mode == "normal" else "none")
    if key == "mythic_dark":
        result.append(
            ("mythic_dark", 40 + 3 * max(0, min(5, int(hero.get("stars", 1)) - 1)))
        )
    team = team or {}
    lunar = team.get("lunar", {})
    if mode == "normal" and lunar.get("arcana") == 11:
        result.append(("lunar_shadows", 25 + 2 * lunar["level"]))
    return result


def award(
    conn, mode, battle_id, pid, target, eligible, sources, *, now=None, roller=None
):
    if not eligible or not target.get("shadow_extractable", False):
        return []
    shadow = conn.execute(
        "SELECT id,code FROM mini_heroes WHERE rarity='shadow' AND code=?",
        (target.get("shadow_hero_code"),),
    ).fetchone()
    if shadow is None:
        return []
    prior = conn.execute(
        "SELECT result_json FROM mini_combat_arise WHERE mode=? AND battle_id=? AND player_id=?",
        (mode, battle_id, pid),
    ).fetchone()
    if prior:
        return json.loads(prior[0])
    chances = {}
    for source, chance in sources:
        chances[source] = max(int(chance), chances.get(source, 0))
    results = []
    for source, chance in chances.items():
        if not 0 <= chance <= 100:
            raise ValueError("Arise chance must be 0..100")
        success = bool((roller or _roll_success)(chance))
        results.append(
            {
                "source": source,
                "chance": chance,
                "success": success,
                "shadow_code": shadow["code"],
            }
        )
        if success:
            if conn.execute(
                "SELECT 1 FROM mini_village_residents WHERE player_id=? AND hero_id=?",
                (pid, shadow["id"]),
            ).fetchone():
                from app.mini.village.service import settle, timestamp

                settle(conn, pid, timestamp(now))
            conn.execute(
                "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?) ON CONFLICT(player_id,hero_id) DO UPDATE SET copies=copies+1,stars=stars+1",
                (pid, shadow["id"]),
            )
            break
    conn.execute(
        "INSERT INTO mini_combat_arise VALUES(?,?,?,?)",
        (mode, battle_id, pid, json.dumps(results)),
    )
    return results
