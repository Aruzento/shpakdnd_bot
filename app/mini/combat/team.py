"""Pure normal-mode team effects shared by Boss and Tower adapters."""

from copy import deepcopy
import secrets
from app.mini.combat.modes import mode_property, hp_event

NORMAL_ARCANA_WEIGHTS = (15, 15, 8, 12, 12, 9, 10, 1, 1, 5, 2, 10)
APPROVED_RULES = {
    "star_reward": "normal_victory_only",
    "tower_lunar": "first_player_floor_choice",
    "lunar_rewards": "O07_approved",
}


def key(hero):
    return mode_property(hero, "normal")["key"]


def level(hero):
    value = int(hero.get("stars", 1))
    if hero.get("rarity") == "mythic" and not 1 <= value <= 6:
        raise ValueError("Mythic stars must be 1..6")
    return max(0, min(5, value - 1))


def begin(heroes, original_hp, base_coins, *, chooser=None, lunar=None):
    keys = [key(h) for h in heroes]
    state = {
        "version": 15,
        "original_hp": int(original_hp),
        "original_coins": int(base_coins),
        "mana_aura": "mana_allegory" in keys,
        "seal_penalty": int(base_coins) * 25 // 100 if "soul_seal" in keys else 0,
        "soul_seal": "soul_seal" in keys,
        "draghar_cap": int(base_coins) * 60 // 100,
        "last_stand_active": False,
        "last_stand_used": False,
        "sum_stars": sum(int(h.get("stars", 0)) for h in heroes),
        "round_buffs": {},
        "sun_marks": {},
        "elements": [
            e for e in ("dark", "star", "moon", "sun") if "mythic_" + e in keys
        ],
        "draghar_present": "bloodthirsty_fight" in keys,
    }
    if "mythic_ice" in keys:
        ice = heroes[keys.index("mythic_ice")]
        state.update(ice_shields=2, ice_level=level(ice))
    if "mythic_moon" in keys:
        moon = heroes[keys.index("mythic_moon")]
        if lunar is None:
            pool = [
                index
                for index, weight in enumerate(NORMAL_ARCANA_WEIGHTS)
                for _ in range(weight)
            ]
            lunar = {"arcana": (chooser or secrets.choice)(pool), "level": level(moon)}
        if lunar["arcana"] not in range(12):
            raise ValueError("Invalid normal lunar choice")
        state["lunar"] = deepcopy(lunar)
    return state


def start_changes(state):
    lunar = state.get("lunar", {})
    arc = lunar.get("arcana")
    changes = {}
    if arc == 3:
        gain = state["original_hp"] * 30 // 100
        changes.update(
            max_hp=state["original_hp"] + gain, current_hp=state["original_hp"] + gain
        )
    if arc == 4:
        changes["extra_shields"] = 2
    if arc == 7:
        changes["instant"] = "victory"
    if arc == 8:
        changes["instant"] = "failure"
    return changes


def modifiers(state, hero, player_id, round_number):
    bonus = []
    additive = 0
    multiplier = 1
    kind = key(hero)
    lv = level(hero)
    if state.get("mana_aura") and hero.get("class_tag") == "mage":
        bonus.append(2000)
    if kind == "mythic_earth":
        bonus.append((500 + lv * 100) * int(round_number))
    buffs = state.get("round_buffs", {})
    if buffs.get("round") == round_number:
        bonus.extend(int(buffs.get(field, 0)) for field in ("dark", "star"))
    lunar = state.get("lunar", {})
    arc = lunar.get("arcana")
    if arc == 1:
        bonus.append(2500)
    if arc == 6:
        bonus.append(-9000)
    if arc == 5 and kind == "mythic_moon":
        additive += 1000 + 200 * lunar["level"]
    if kind == "mythic_sun":
        multiplier = 2 ** int(state.get("sun_marks", {}).get(str(player_id), 0))
    return {
        "bonus_bps": bonus,
        "additive": additive,
        "multiplier": multiplier,
        "reduction_percent": 25 if arc == 0 else 0,
        "elements": state.get("elements", []),
    }


def after_hit(
    state,
    hero,
    player_id,
    current_hp,
    max_hp,
    round_number,
    actual,
    *,
    is_extra=False,
    roller=None,
    participants=()
):
    state = deepcopy(state)
    events = []
    roller = roller or (lambda chance: secrets.randbelow(10000) < int(chance * 100))
    hp = max(0, int(current_hp))
    kind = key(hero)
    lv = level(hero)
    extras = []
    skip = 0

    def hit(value, source):
        nonlocal hp
        event = hp_event(hp, value, player_id, source)
        hp = event["hp_after"]
        events.append(event)

    if actual <= 0:
        return {
            "state": state,
            "events": events,
            "current_hp": hp,
            "extra_players": extras,
            "boss_skip": skip,
        }
    burn = state.get("burn")
    if burn:
        burn["damage"] += int(actual)
        burn["stacks"] += 1
        if burn["stacks"] >= 10:
            hit(burn["damage"] * (20 + 4 * burn["level"]) // 100, "fire_explosion")
            state.pop("burn", None)
    if kind == "holy_smite" and not state.get("smite_used") and hp:
        state["smite_used"] = True
        hit(max(1, state["original_hp"] * 20 // 100), "holy_smite")
    if kind == "mythic_fire" and "burn" not in state:
        state["burn"] = {"stacks": 0, "damage": 0, "level": lv}
    if kind == "mythic_lightning" and not is_extra:
        if state.get("lightning_round") != round_number:
            state["lightning_round"] = round_number
            extras = [
                int(p["player_id"])
                for p in participants
                if int(p["player_id"]) != player_id and not p.get("banished")
            ]
        if roller(10 + 2 * lv):
            skip = 1
        state["lightning_reward_percent"] = max(
            state.get("lightning_reward_percent", 0), 2 + lv
        )
    if kind == "mythic_earth" and roller(10 + 3 * lv):
        state["suppressed_round"] = round_number
    if kind in ("mythic_dark", "mythic_star"):
        buffs = state.setdefault("round_buffs", {})
        if buffs.get("round") != round_number:
            buffs.clear()
            buffs["round"] = round_number
        if kind == "mythic_dark":
            buffs["dark"] = (max_hp - hp) * 10000 // max(1, max_hp)
        else:
            buffs["star"] = state["sum_stars"] * (100 + 20 * lv)
        overlay = kind.removeprefix("mythic_")
        if overlay not in state["elements"]:
            state["elements"].append(overlay)
    if kind == "mythic_sun":
        marks = state.setdefault("sun_marks", {})
        marks[str(player_id)] = int(marks.get(str(player_id), 0)) + 1
        state.update(
            blind_round=round_number, blind_chance=30 + 5 * lv, neutralized=True
        )
        if "sun" not in state["elements"]:
            state["elements"].append("sun")
    return {
        "state": state,
        "events": events,
        "current_hp": hp,
        "extra_players": extras,
        "boss_skip": skip,
    }


def reward_attack(state, current_hp, round_number, *, roller=None):
    state = deepcopy(state)
    roller = roller or (lambda chance: secrets.randbelow(10000) < int(chance * 100))
    if state.get("soul_seal"):
        return {"state": state, "blocked": True, "reason": "soul_seal", "hp_damage": 0}
    if state.get("blind_round") == round_number and roller(
        state.get("blind_chance", 0)
    ):
        return {"state": state, "blocked": True, "reason": "sun_miss", "hp_damage": 0}
    if state.get("last_stand_active") and roller(50):
        return {
            "state": state,
            "blocked": True,
            "reason": "last_stand_miss",
            "hp_damage": 0,
        }
    if state.get("ice_shields", 0) > 0:
        state["ice_shields"] -= 1
        damage = int(current_hp) * (10 + 2 * state["ice_level"]) // 100
        return {
            "state": state,
            "blocked": True,
            "reason": "ice_shield",
            "hp_damage": damage,
        }
    if state.get("lunar_shields", 0) > 0:
        state["lunar_shields"] -= 1
        return {
            "state": state,
            "blocked": True,
            "reason": "lunar_shield",
            "hp_damage": 0,
        }
    return {"state": state, "blocked": False, "reason": None, "hp_damage": 0}


def coins(state, current_base, *, stolen=0, boss_damage=0, shields_intact=False):
    base = (
        state["draghar_cap"]
        if state.get("draghar_present", state.get("last_stand_active"))
        else int(current_base)
    )
    value = max(0, base - state.get("seal_penalty", 0) - int(stolen) - int(boss_damage))
    if shields_intact:
        value = value * (100 + state.get("lightning_reward_percent", 0)) // 100
    lunar = state.get("lunar", {})
    arc = lunar.get("arcana")
    from app.mini.mythic.policies import normal
    policy=normal(state)
    if arc == 2:
        value = value * policy['gold_percent'][lunar['level']] // 100
    if arc == 9:
        value = value * policy['thief_remaining_percent'] // 100
    return value
