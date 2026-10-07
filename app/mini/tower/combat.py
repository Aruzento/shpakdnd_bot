"""Pure Tower adapter: shared HP hooks, three shields, no Boss persistence imports.

Boss reward-only decay/corruption have no meaning here. Passive shard procs are
banked until victory so losing/retrying cannot farm combat currency.
"""
import json
from app.mini.combat import creatures, classes
from app.mini.combat.hero_abilities import engine as abilities
from app.mini.combat.matchups import faction_multiplier_percent, modify_damage


def resolve_turn(attempt, *, roller=None):
    hero = json.loads(attempt["hero_json"])
    enemy = json.loads(attempt["enemy_json"])
    runtime = json.loads(attempt["runtime_json"])
    hero_state = runtime.get("hero", {})
    enemy.update(current_hp=attempt["current_hp"], trait_rules_version=1,
                 class_rules_version=runtime.get("class_rules_version",0),
                 features_json=json.dumps(enemy["features"]),
                 feature_state_json=json.dumps(runtime.get("creature", {})),
                 reward_shields=attempt["shields"], reward_shields_max=3)
    events = []
    damage_total = 0
    turn = attempt["turn"] + 1
    percent = faction_multiplier_percent(hero["faction"], enemy["faction"])
    passive = hero.get("passive_key") or "none"
    participants = [{"hero_snapshot_json":json.dumps(hero)}]

    def apply(plan):
        enemy.update(plan.get("boss_changes", {}))
        events.extend(plan.get("events", []))

    def damage(value, kind="attack"):
        nonlocal damage_total
        actual = min(enemy["current_hp"], max(0,value))
        enemy["current_hp"] -= actual
        damage_total += actual
        events.append({"type":kind,"damage":actual,"message":f"⚔️ {actual} урона."})
        return actual

    def death():
        if enemy["current_hp"] > 0:
            return False
        revived = creatures.boss_death(enemy)
        apply(revived)
        return not revived["revived"]

    echo = abilities.resolve_turn_start(state=hero_state)
    hero_state = echo["state"]
    if echo["damage"]:
        damage(echo["damage"],"echo")
    won = death() if enemy["current_hp"] == 0 else False
    primary_killed = False
    if not won:
        reachable = creatures.can_reach(enemy,hero)
        hit_number = runtime.get("hit_count",attempt["turn"]) + 1
        runtime["hit_count"] = hit_number
        attack = abilities.resolve_attack(passive if reachable else "none",
            base_damage=classes.initial_attack(hero["attack"]+attempt["equipment_bonus"],participants,active=classes.enabled(enemy)),hit_number=hit_number,
            boss_hp_before=enemy["current_hp"],boss_max_hp=enemy["max_hp"],roller=roller)
        events.extend(attack["events"])
        ordinary=creatures.feature_damage(enemy,hero,modify_damage(attack["damage"],percent))
        hit = damage(classes.beast_damage(ordinary,hero,hero_state,active=classes.enabled(enemy)))
        if not reachable:
            events.append({"type":"unreachable","message":"🪽 Нужен дальний бой или летающий герой."})
        apply(creatures.on_hit(enemy,hero,successful=hit>0,roller=roller))
        after = abilities.resolve_after_attack(passive if reachable else "none",hit_number=hit_number,actual_hp_damage=hit,state=hero_state)
        class_plan=classes.on_hit(enemy,hero,after['state'],successful=hit>0,roller=roller)
        apply(class_plan)
        hero_state = class_plan["state"]; events.extend(after["events"])
        runtime["skips"] = runtime.get("skips",0)+attack["boss_skip_turns"]
        primary_killed = enemy["current_hp"] == 0
        won = death() if primary_killed else False
        for _ in range(attack["extra_attacks"] if reachable and not primary_killed else 0):
            # Extra hits use the snapshotted account attack without retriggering passives.
            extra_hit = damage(creatures.feature_damage(enemy,hero,modify_damage(hero["attack"]+attempt["equipment_bonus"],percent)),"extra_attack")
            apply(creatures.on_hit(enemy,hero,successful=extra_hit>0,roller=roller))
            won = death() if enemy["current_hp"] == 0 else False
            if won: break
        if not won:
            apply(creatures.after_hero_turn(enemy,hero,roller=roller))
    if not won:
        if runtime.get("skips",0):
            runtime["skips"] -= 1
            events.append({"type":"enemy_skip","message":"⏭ Противник пропускает ответный ход."})
        elif enemy.get("response","attack") == "prepare" and not runtime.get("enemy_actions",0):
            runtime["enemy_actions"] = 1
            events.append({"type":"enemy_prepare","message":"⏳ Противник готовит атаку; щиты целы."})
        else:
            runtime["enemy_actions"] = runtime.get("enemy_actions",0)+1
            holy = creatures.holy_attack_attempt(enemy,roller=roller); apply(holy)
            if not holy["miss"]:
                proc = abilities.resolve_boss_attack(passive,roller=roller)
                runtime["bonus_shards"] = runtime.get("bonus_shards",0)+proc["bonus_shards"]
                events.extend(proc["events"])
                guard = hero_state.get("reward_guard_charges",0)
                if guard:
                    hero_state["reward_guard_charges"] = guard-1
                    events.append({"type":"guard","message":"🛡 Защитный заряд поглотил ответную атаку."})
                else:
                    enemy["reward_shields"] = max(0,enemy["reward_shields"]-1)
                    events.append({"type":"shield","message":"💥 Противник разбил щит."})
            # Shield exhaustion ends the attempt before poison can kill the enemy.
            if enemy["reward_shields"] > 0:
                apply(creatures.poison_tick(enemy))
                won = death() if enemy["current_hp"] == 0 else False
                if not won:
                    apply(creatures.holy_regeneration(enemy,participants))
        if not won and enemy["reward_shields"] > 0:
            apply(creatures.end_round(enemy,participants))
    if won:
        kill = abilities.resolve_kill(passive,roller=roller) if primary_killed else {"bonus_shards":0,"events":[]}
        victory = abilities.resolve_victory(passive,roller=roller) if runtime.get("hit_count",0) else {"bonus_shards":0,"events":[]}
        runtime["bonus_shards"] = runtime.get("bonus_shards",0)+kill["bonus_shards"]+victory["bonus_shards"]
        events.extend(kill["events"]+victory["events"])
    status = "won" if won else ("lost" if enemy["reward_shields"] == 0 else "active")
    runtime.update(hero=hero_state,creature=json.loads(enemy["feature_state_json"]))
    # Shared hooks retain their established event types; presentation uses Tower terminology.
    for event in events:
        event["message"] = event.get("message","").replace("босса","противника").replace("Босс","Противник").replace("босс","противник").replace("награды","щитов")
    return {"current_hp":enemy["current_hp"],"shields":enemy["reward_shields"],"turn":turn,
            "status":status,"runtime_json":json.dumps(runtime),"events_json":json.dumps(events,ensure_ascii=False),
            "damage":damage_total,"faction_percent":percent}
