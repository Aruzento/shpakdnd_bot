"""Approved, deterministic raid mechanics. No content records or I/O here.

Counters measure committed Raid attempts, not individual HP events. The caller
owns the transaction; prepare/finish never mutate the frozen boss specification.
"""

from copy import deepcopy

MAX_INT = 2**63 - 1
SPECS = {
    "armor_cycle": {"type": "armor_cycle", "armored_attacks": 10, "exposed_attacks": 5},
    "flight_cycle": {
        "type": "flight_cycle",
        "flying_attacks": 8,
        "grounded_attacks": 2,
    },
    "periodic_heal": {
        "type": "periodic_heal",
        "every_attacks": 15,
        "heal_bps_of_original_hp": 25,
        "sun_suppresses": True,
    },
    "hp_defense_phases": {
        "type": "hp_defense_phases",
        "threshold_percent": [75, 50, 25],
        "phase_features": [["armored", "flying"], ["armored"], ["flying"], []],
        "threshold_base": "original_max_hp",
        "monotonic": True,
    },
}
PROFILES = {
    "armor_cycle": (100000, "fire", "ice", ["armored"]),
    "flight_cycle": (115000, "lightning", "star", ["flying"]),
    "periodic_heal": (130000, "dark", "sun", []),
    "hp_defense_phases": (150000, "star", "moon", ["armored", "flying"]),
}
TAGS = {"armored", "flying"}


def _integer(value, label, minimum=0, maximum=MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid raid mechanic " + label)
    return value


def validate(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("type"), str):
        raise ValueError("Raid mechanic must be a typed object")
    kind = spec["type"]
    if kind not in SPECS or set(spec) != set(SPECS[kind]):
        raise ValueError("Unknown or incomplete raid mechanic fields/type")
    if kind in ("armor_cycle", "flight_cycle", "periodic_heal"):
        for field in set(spec) - {"type", "sun_suppresses"}:
            _integer(spec[field], field, 1)
        if kind == "periodic_heal" and type(spec["sun_suppresses"]) is not bool:
            raise ValueError("sun_suppresses must be boolean")
    else:
        thresholds = spec["threshold_percent"]
        if not isinstance(thresholds, list) or len(thresholds) != 3:
            raise ValueError("Three decreasing HP thresholds required")
        for value in thresholds:
            _integer(value, "threshold", 1, 99)
        if thresholds != sorted(set(thresholds), reverse=True):
            raise ValueError("HP thresholds must strictly decrease")
        phases = spec["phase_features"]
        if not isinstance(phases, list) or len(phases) != 4:
            raise ValueError("Four defense phases required")
        for features in phases:
            if (
                not isinstance(features, list)
                or any(not isinstance(v, str) or v not in TAGS for v in features)
                or len(features) != len(set(features))
            ):
                raise ValueError("Invalid phase features")
        if type(spec["monotonic"]) is not bool:
            raise ValueError("monotonic must be boolean")
        if not isinstance(spec["threshold_base"], str):
            raise ValueError("threshold_base must be original_max_hp")
    # This release implements the approved four parameter sets, not an
    # unrestricted author scripting/tuning interface.
    if spec != SPECS[kind]:
        raise ValueError(
            "Raid mechanic parameters differ from the approved specification"
        )
    return deepcopy(spec)


def validate_profile(boss):
    spec = validate(boss["mechanic"])
    hp, element, counter, features = PROFILES[spec["type"]]
    if (
        boss["max_hp"] != hp
        or boss["element"] != element
        or boss["counter_elements"] != [counter]
        or sorted(boss.get("features", [])) != sorted(features)
        or boss.get("regeneration_percent", 0) != 0
    ):
        raise ValueError("Raid boss does not match its approved mechanic profile")
    return spec


def _hp_phase(hp, original, spec):
    # Integer comparison preserves exact inclusive boundaries for any original M.
    return 1 + sum(hp * 100 <= original * t for t in spec["threshold_percent"])


def prepare(raid, state):
    spec = raid["snapshot"].get("mechanic")
    if spec is None:
        return None  # Exact legacy state/result shape; no implicit opt-in.
    spec = validate(spec)
    kind = spec["type"]
    original = _integer(raid["original_max_hp"], "original_max_hp", 1)
    hp = _integer(raid["current_hp"], "current_hp")
    saved = state.get("mechanic", {})
    if not isinstance(saved, dict):
        raise ValueError("Invalid persisted raid mechanic state")
    if saved and (
        type(saved.get("version")) is not int
        or saved.get("version") != 1
        or saved.get("type") != kind
    ):
        raise ValueError("Persisted mechanic does not match its frozen specification")
    count = _integer(saved.get("confirmed_attacks", 0), "confirmed_attacks")
    features = list(raid["snapshot"].get("features", []))
    phase = None
    remaining = None
    if kind in ("armor_cycle", "flight_cycle"):
        protected = (
            spec["armored_attacks"] if kind == "armor_cycle" else spec["flying_attacks"]
        )
        exposed = (
            spec["exposed_attacks"]
            if kind == "armor_cycle"
            else spec["grounded_attacks"]
        )
        tag = "armored" if kind == "armor_cycle" else "flying"
        offset = count % (protected + exposed)
        phase = 1 if offset < protected else 2
        features = [v for v in features if v != tag]
        if phase == 1:
            features.append(tag)
        remaining = protected - offset if phase == 1 else protected + exposed - offset
    elif kind == "hp_defense_phases":
        old_phase = _integer(saved.get("phase", 1), "phase", 1, 4)
        phase = max(old_phase, _hp_phase(hp, original, spec))
        features = spec["phase_features"][phase - 1]
    else:
        remaining = spec["every_attacks"] - count % spec["every_attacks"]
    return {
        "type": kind,
        "confirmed_attacks": count,
        "phase": phase,
        "effective_features": sorted(features),
        "remaining_attacks": remaining,
        "hp_at_start": hp,
        "original_max_hp": original,
    }


def initialize(snapshot, hp):
    if "mechanic" not in snapshot:
        return {}
    raid = {"snapshot": snapshot, "current_hp": hp, "original_max_hp": hp}
    context = prepare(raid, {})
    return {
        "mechanic": {
            "version": 1,
            "type": context["type"],
            "confirmed_attacks": 0,
            "phase": context["phase"],
            "effective_features": context["effective_features"],
        }
    }


def finish(raid, state, context, hp, maximum, events, *, sun_suppressed=False):
    if context is None:
        return hp, [], None
    spec = raid["snapshot"]["mechanic"]
    count = _integer(context["confirmed_attacks"] + 1, "confirmed_attacks", 1)
    saved = deepcopy(state.get("mechanic", {}))
    saved.update(
        version=1, type=context["type"], confirmed_attacks=count, phase=context["phase"]
    )
    audit = []
    heal = None
    if spec["type"] == "periodic_heal" and count % spec["every_attacks"] == 0:
        requested = (
            context["original_max_hp"] * spec["heal_bps_of_original_hp"] // 10000
        )
        suppressed = bool(spec["sun_suppresses"] and sun_suppressed)
        actual = 0 if suppressed or hp == 0 else min(requested, max(0, maximum - hp))
        heal = {
            "type": "raid_periodic_heal",
            "mechanic_type": spec["type"],
            "attack_number": count,
            "requested_hp": requested,
            "healed_hp": actual,
            "sun_suppressed": suppressed,
            "fatal_hit": hp == 0,
            "hp_before": hp,
            "hp_after": hp + actual,
            "original_max_hp": context["original_max_hp"],
        }
        hp += actual
        saved["last_heal"] = deepcopy(heal)
        audit.append(heal)
    # Retain every threshold reached during this attempt, even if post-hit
    # healing (or Eclipse on a later attempt) raises current HP again.
    lowest = min([context["hp_at_start"], hp] + [e["hp_after"] for e in events])
    if spec["type"] == "hp_defense_phases":
        saved["phase"] = max(
            context["phase"], _hp_phase(lowest, context["original_max_hp"], spec)
        )
    state["mechanic"] = saved
    next_raid = {**raid, "current_hp": hp}
    next_context = prepare(next_raid, state)
    saved["phase"] = next_context["phase"]
    saved["effective_features"] = next_context["effective_features"]
    if context["phase"] != next_context["phase"]:
        transitions = {
            "armor_cycle": ("armor_destroyed", "armor_restored"),
            "flight_cycle": ("landed", "took_off"),
        }
        transition = (
            transitions[spec["type"]][int(next_context["phase"] == 1)]
            if spec["type"] in transitions
            else "hp_phase_changed"
        )
        event = {
            "type": "raid_mechanic_transition",
            "mechanic_type": spec["type"],
            "transition": transition,
            "attack_number": count,
            "phase_before": context["phase"],
            "phase_after": next_context["phase"],
            "features_before": context["effective_features"],
            "features_after": next_context["effective_features"],
            "hp_at_start": context["hp_at_start"],
            "hp_after": hp,
            "lowest_hp": lowest,
            "original_max_hp": context["original_max_hp"],
        }
        if spec["type"] == "hp_defense_phases":
            event["crossed_thresholds"] = spec["threshold_percent"][
                context["phase"] - 1 : next_context["phase"] - 1
            ]
        saved["last_transition"] = deepcopy(event)
        audit.append(event)
    receipt = {
        "type": spec["type"],
        "attack_number": count,
        "phase_used": context["phase"],
        "features_used": context["effective_features"],
        "phase_next": next_context["phase"],
        "features_next": next_context["effective_features"],
        "remaining_attacks": next_context["remaining_attacks"],
    }
    if heal is not None:
        receipt["heal"] = deepcopy(heal)
    return hp, audit, receipt


def lines(raid):
    context = prepare(raid, raid.get("state", {}))
    if context is None:
        return []
    kind = context["type"]
    count = context["confirmed_attacks"]
    remaining = context["remaining_attacks"]
    tags = {"armored": "броня", "flying": "полёт"}
    protection = ", ".join(tags[tag] for tag in context["effective_features"]) or "нет"
    if kind == "armor_cycle":
        title = "Циклическая броня"
        detail = (
            "Броня действует" if context["phase"] == 1 else "Броня разрушена"
        ) + f"; до переключения: {remaining} атак."
    elif kind == "flight_cycle":
        title = "Воздушная фаза"
        detail = (
            "В воздухе" if context["phase"] == 1 else "На земле"
        ) + f"; до переключения: {remaining} атак."
    elif kind == "periodic_heal":
        title = "Периодическое восстановление"
        amount = (
            context["original_max_hp"]
            * raid["snapshot"]["mechanic"]["heal_bps_of_original_hp"]
            // 10000
        )
        detail = f"До восстановления {amount} HP: {remaining} атак."
    else:
        title = "Защитные фазы HP"
        phase = context["phase"]
        threshold = (
            context["original_max_hp"]
            * raid["snapshot"]["mechanic"]["threshold_percent"][phase - 1]
            // 100
            if phase < 4
            else None
        )
        detail = f"Фаза {phase}/4; следующий порог: " + (
            f"≤{threshold} HP." if threshold is not None else "нет."
        )
    result = [
        f"Механика: {title}; подтверждено атак: {count}.",
        detail,
        "Активная защита: " + protection + ".",
    ]
    last_heal = raid.get("state", {}).get("mechanic", {}).get("last_heal")
    if last_heal:
        result.append(
            "Последнее восстановление: "
            + (
                "подавлено Солнечным."
                if last_heal["sun_suppressed"]
                else f"+{last_heal['healed_hp']} HP."
            )
        )
    return result


def event_lines(audit):
    names = {
        "armor_destroyed": "Броня разрушена",
        "armor_restored": "Броня восстановлена",
        "landed": "Босс приземлился",
        "took_off": "Босс снова в воздухе",
    }
    result = []
    for event in audit:
        if event["type"] == "raid_mechanic_transition":
            result.append(
                names.get(
                    event["transition"], f"Защитная фаза: {event['phase_after']}/4"
                )
                + "."
            )
        elif event["type"] == "raid_periodic_heal":
            result.append(
                "Восстановление подавлено Солнечным."
                if event["sun_suppressed"]
                else f"Периодическое восстановление: +{event['healed_hp']} HP."
            )
    return result
