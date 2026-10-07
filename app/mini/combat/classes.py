"""Versioned class role effects. Pure plans, no DB/Telegram or feature imports."""
import json
import secrets


def enabled(battle): return int(battle.get('class_rules_version',0))>=1


def roll_success(chance): return secrets.randbelow(100)<chance


def active_classes(participants):
    return [json.loads(p.get('hero_snapshot_json') or '{}').get('class_tag','none')
            for p in participants if not p.get('banished',0)]


def initial_attack(attack,participants,*,active=True):
    count=active_classes(participants).count('warrior') if active else 0
    return max(1,int(attack)*(100+5*count)//100)


def beast_damage(damage,hero,state,*,active=True):
    count=int(state.get('class_successful_hits',0))
    return int(damage)*(100+2*count)//100 if active and hero.get('class_tag')=='beast' else int(damage)


def permanent_damage(battle):
    base=int(battle.get('reward_coins',0))
    # Retain existing ability/admin setters of the legacy reward percent.
    return min(max(0,base-int(battle.get('sneaky_stolen',0))),
               max(int(battle.get('boss_damage',0)),base-base*int(battle.get('reward_percent',100))//100))


def real_reward(battle):
    if not enabled(battle): return int(battle.get('reward_coins',0))*int(battle.get('reward_percent',100))//100
    return max(0,int(battle.get('reward_coins',0))-permanent_damage(battle)-int(battle.get('sneaky_stolen',0)))


def effective_reward(battle):
    base=int(battle.get('reward_coins',0))
    return max(0,real_reward(battle)+base*int(battle.get('reward_temp_hp',0))//100-base*int(battle.get('reward_corruption',0))//100)


def on_hit(battle,hero,state,*,successful,roller=None):
    values=dict(state);changes={};events=[];stolen=0
    if not enabled(battle) or not successful:
        return dict(state=values,boss_changes=changes,events=events,stolen=stolen)
    kind=hero.get('class_tag','none');roll=roller or roll_success
    if kind=='beast': values['class_successful_hits']=int(values.get('class_successful_hits',0))+1
    if kind=='guardian' and int(battle['reward_shields'])<int(battle['reward_shields_max']) and roll(5):
        changes['reward_shields']=int(battle['reward_shields'])+1
        events.append(dict(type='class_guardian',message='🛡 Страж восстановил один щит.'))
    # Reward procs apply only to modes with a real coin reward pool.
    if kind=='sneaky' and real_reward(battle)>0 and roll(1):
        stolen=min(real_reward(battle),(real_reward(battle)*5+99)//100)
        changes['sneaky_stolen']=int(battle.get('sneaky_stolen',0))+stolen
        values['class_stolen_coins']=int(values.get('class_stolen_coins',0))+stolen
        events.append(dict(type='class_sneaky',stolen=stolen,message=f'🪙 Плут украл {stolen} монет награды.'))
    if kind=='healer' and permanent_damage(battle)>0:
        healed=min(permanent_damage(battle),(int(battle['reward_coins'])*5+99)//100)
        changes['boss_damage']=permanent_damage(battle)-healed
        base=int(battle['reward_coins'])
        changes['reward_percent']=min(100,((base-changes['boss_damage'])*100+base-1)//base) if base else 100
        events.append(dict(type='class_healer',healed=healed,message=f'💚 Целитель восстановил {healed} монет награды.'))
    return dict(state=values,boss_changes=changes,events=events,stolen=stolen)


def blocks_construct_regeneration(participants):
    return 'technical' in active_classes(participants)


def can_remove_magic_shield(hero):
    from app.mini.combat.tags import MAGIC_CLASSES
    return hero.get('class_tag') in MAGIC_CLASSES
