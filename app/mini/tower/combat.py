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
    v15=int(runtime.get("rules_version",0))>=15
    from app.mini.combat import team
    from app.mini.combat.modes import ordinary_damage
    values=runtime.get("team",{})
    enemy.update(current_hp=attempt["current_hp"], trait_rules_version=1,
                 class_rules_version=runtime.get("class_rules_version",0),
                 features_json=json.dumps(enemy["features"]),
                 feature_state_json=json.dumps(runtime.get("creature", {})),
                 reward_shields=attempt["shields"], reward_shields_max=3)
    events = []
    damage_total = 0
    turn = attempt["turn"] + 1
    form = abilities.resolve_round_start(hero, state=hero_state, round_number=turn, roller=roller)
    hero, hero_state = form['hero'], form['state']
    events.extend(form['events'])
    if v15 and values.get('neutralized'):enemy['faction']='neutral'
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
    if v15 and values.get('initial_outcome')=='victory':won=True;enemy['current_hp']=0
    if v15 and values.get('initial_outcome')=='failure':enemy['reward_shields']=0
    if v15 and values.get('neutralized'):enemy['faction']='neutral'
    definition=hero.get('passives',{}).get('normal',{}).get('definition') if v15 else None
    primary_killed = False
    if not won and enemy["reward_shields"]>0:
        reachable = creatures.can_reach(enemy,hero)
        hit_number = runtime.get("hit_count",attempt["turn"]) + 1
        runtime["hit_count"] = hit_number
        attack = abilities.resolve_attack(passive if reachable else "none",
            base_damage=classes.initial_attack(hero["attack"]+attempt["equipment_bonus"],participants,active=classes.enabled(enemy) and not v15),hit_number=hit_number,
            boss_hp_before=enemy["current_hp"],boss_max_hp=enemy["max_hp"],roller=roller,additive_bonuses=v15,ability_definition=hero.get("passives",{}).get("normal",{}).get("definition") if v15 else None)
        events.extend(attack["events"])
        resolved_damage=attack['damage']
        if v15:
            modifiers=team.modifiers(values,hero,attempt['player_id'],turn)
            modifiers['bonus_bps'].append(attack.get('ordinary_bonus_bps',0))
            if classes.enabled(enemy):
                if hero.get('class_tag')=='warrior':modifiers['bonus_bps'].append(500)
                if hero.get('class_tag')=='beast':modifiers['bonus_bps'].append(200*int(hero_state.get('class_successful_hits',0)))
            resolved_damage=ordinary_damage(resolved_damage,**{k:modifiers[k] for k in ('bonus_bps','additive','multiplier','reduction_percent')},protected_zero=not reachable,element_percents=[v for e,v in enemy.get('element_multipliers',{}).items() if e in set(modifiers['elements'])|set(hero.get('elements',[]))])
        ordinary=creatures.feature_damage(enemy,hero,modify_damage(resolved_damage,percent))
        hit = damage(classes.beast_damage(ordinary,hero,hero_state,active=classes.enabled(enemy) and not v15) + (attack['pure_bonus'] if reachable else 0))
        if not reachable:
            events.append({"type":"unreachable","message":"🪽 Нужен дальний бой или летающий герой."})
        apply(creatures.on_hit(enemy,hero,successful=hit>0,roller=roller))
        after = abilities.resolve_after_attack(passive if reachable else "none",hit_number=hit_number,actual_hp_damage=hit,state=hero_state,ability_definition=definition)
        if after.get('restore_shield'):
            enemy['reward_shields'] = min(enemy['reward_shields_max'], enemy['reward_shields'] + 1)
        class_plan=classes.on_hit(enemy,hero,after['state'],successful=hit>0,roller=roller)
        apply(class_plan)
        hero_state = class_plan["state"]; events.extend(after["events"])
        runtime["skips"] = runtime.get("skips",0)+attack["boss_skip_turns"]
        if v15:
            plan=team.after_hit(values,hero,attempt['player_id'],enemy['current_hp'],enemy['max_hp'],turn,hit,roller=roller,participants=())
            values=plan['state'];runtime['skips']=runtime.get('skips',0)+plan['boss_skip']
            for event in plan['events']:damage(event['actual_hp_damage'],event['kind'])
            if values.get('suppressed_round')==turn or values.get('blind_round')==turn:
                enemy['features_json']='[]'
                if values.get('blind_round')==turn:enemy['faction']='neutral'
        primary_killed = enemy["current_hp"] == 0
        won = death() if primary_killed else False
        for _ in range(attack["extra_attacks"] if reachable and not primary_killed else 0):
            if v15:
                extra=abilities.resolve_attack(passive,base_damage=(hero['attack']+attempt['equipment_bonus'])*attack.get('extra_attack_percent',100)//100,hit_number=hit_number,boss_hp_before=enemy['current_hp'],boss_max_hp=enemy['max_hp'],roller=roller,additive_bonuses=True,ability_definition=definition,state=hero_state)
                events.extend(extra['events'])
                modifiers=team.modifiers(values,hero,attempt['player_id'],turn)
                modifiers['bonus_bps'].append(extra.get('ordinary_bonus_bps',0))
                if classes.enabled(enemy):
                    if hero.get('class_tag')=='warrior':modifiers['bonus_bps'].append(500)
                    if hero.get('class_tag')=='beast':modifiers['bonus_bps'].append(200*int(hero_state.get('class_successful_hits',0)))
                raw=ordinary_damage(extra['damage'],**{k:modifiers[k] for k in ('bonus_bps','additive','multiplier','reduction_percent')},protected_zero=not reachable,element_percents=[v for e,v in enemy.get('element_multipliers',{}).items() if e in set(modifiers['elements'])|set(hero.get('elements',[]))])
                extra_hit=damage(creatures.feature_damage(enemy,hero,modify_damage(raw,percent))+extra.get('pure_bonus',0),'extra_attack')
                post=abilities.resolve_after_attack(passive,hit_number=hit_number,actual_hp_damage=extra_hit,state=hero_state,ability_definition=definition)
                class_plan=classes.on_hit(enemy,hero,post['state'],successful=extra_hit>0,roller=roller)
                apply(class_plan)
                hero_state=class_plan['state'];events.extend(post['events'])
                if post.get('restore_shield'):enemy['reward_shields']=min(3,enemy['reward_shields']+1)
                shared=team.after_hit(values,hero,attempt['player_id'],enemy['current_hp'],enemy['max_hp'],turn,extra_hit,is_extra=True,roller=roller)
                values=shared['state']
                for event in shared['events']:damage(event['actual_hp_damage'],event['kind'])
                runtime['skips']=runtime.get('skips',0)+extra.get('boss_skip_turns',0)
            else:
                extra_hit = damage(creatures.feature_damage(enemy,hero,modify_damage(max(1,(hero["attack"]+attempt["equipment_bonus"])*attack.get("extra_attack_percent",100)//100),percent)),"extra_attack")
            apply(creatures.on_hit(enemy,hero,successful=extra_hit>0,roller=roller))
            if enemy['current_hp']==0:primary_killed=True
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
            v15_guard=team.reward_attack(values,enemy['current_hp'],turn,roller=roller) if v15 and not holy['miss'] else None
            if v15_guard:
                values=v15_guard['state']
                if v15_guard['blocked']:
                    events.append({'type':v15_guard['reason'],'message':'✨ Защитное свойство предотвратило ответную атаку.'})
                    if v15_guard['hp_damage']:
                        damage(v15_guard['hp_damage'],'ice_shield')
                        won=death() if enemy['current_hp']==0 else False
            if not holy["miss"] and not (v15_guard and v15_guard['blocked']):
                proc = abilities.resolve_boss_attack(passive,roller=roller,ability_definition=definition)
                runtime["bonus_shards"] = runtime.get("bonus_shards",0)+proc["bonus_shards"]
                events.extend(proc["events"])
                defense = abilities.resolve_reward_defense(passive, state=hero_state, shields=enemy["reward_shields"], roller=roller,ability_definition=definition)
                hero_state = defense["state"]
                guard = hero_state.get("reward_guard_charges",0)
                if defense["blocked"]:
                    events.append({"type":defense["type"],"message":"🛡 Атака поглощена."})
                elif guard:
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
    if won and v15 and hero.get('passive_key')=='mythic_star' and (roller or abilities._roll_success)(20+3*team.level(hero)):
        runtime['bonus_shards']=runtime.get('bonus_shards',0)+300
    if won:
        kill = abilities.resolve_kill(passive,roller=roller,ability_definition=definition) if primary_killed else {"bonus_shards":0,"events":[]}
        victory = abilities.resolve_victory(passive,roller=roller,ability_definition=definition) if runtime.get("hit_count",0) else {"bonus_shards":0,"events":[]}
        runtime["bonus_shards"] = runtime.get("bonus_shards",0)+kill["bonus_shards"]+victory["bonus_shards"]
        events.extend(kill["events"]+victory["events"])
    status = "won" if won else ("lost" if enemy["reward_shields"] == 0 else "active")
    if v15:runtime["team"]=values
    runtime.update(hero=hero_state,creature=json.loads(enemy["feature_state_json"]))
    # Shared hooks retain their established event types; presentation uses Tower terminology.
    for event in events:
        event["message"] = event.get("message","").replace("босса","противника").replace("Босс","Противник").replace("босс","противник").replace("награды","щитов")
    return {"current_hp":enemy["current_hp"],"shields":enemy["reward_shields"],"turn":turn,
            "status":status,"runtime_json":json.dumps(runtime),"events_json":json.dumps(events,ensure_ascii=False),
            "damage":damage_total,"faction_percent":percent}
