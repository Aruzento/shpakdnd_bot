"""Duel rounds are simultaneous. No Boss/Tower-specific rules enter this resolver."""
from app.mini.editorial import text as editorial_text

def resolve(challenger,defender):
    left,right=int(challenger['attack']),int(defender['attack'])
    if left<=0 or right<=0: raise ValueError(editorial_text('errors.duels.combat.resolve.01'))
    hp_left,hp_right=left*3,right*3
    rounds=[]
    while hp_left>0 and hp_right>0:
        hp_left-=right;hp_right-=left
        rounds.append({'challenger_hp':max(0,hp_left),'defender_hp':max(0,hp_right),
            'challenger_damage':left,'defender_damage':right})
    winner=None if hp_left<=0 and hp_right<=0 else ('challenger' if hp_left>0 else 'defender')
    return {'winner':winner,'draw':winner is None,'rounds':rounds}
