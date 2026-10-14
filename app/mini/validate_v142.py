"""Read-only catalog audit for V1.4.2; does not initialize or mutate SQLite."""
import json

from app.mini.catalog import load_hero_catalog, validate_content, hero_image_path, GACHA_RARITIES
from app.mini.boss.catalog import load_boss_catalog, boss_image_path
from app.mini.combat.hero_abilities.catalog import configured_ability_keys
from app.mini.content_safety import validate_combat_content
from app.mini.item_registry import load_definitions


def audit():
    content = validate_content()
    heroes = load_hero_catalog()['heroes']
    bosses = load_boss_catalog()['bosses']
    definitions = load_definitions()
    by_code = {hero['code']: hero for hero in heroes}
    abilities = configured_ability_keys()
    shadows = [hero for hero in heroes if hero['rarity'] == 'shadow']
    for hero in shadows:
        for field in ('code','name','image','passive_key','passive_text','faction','damage_type','class_tag','attack_range','special_trait'):
            if not isinstance(hero.get(field),str) or not hero[field].strip():
                raise ValueError(f"Shadow {hero['code']}: invalid {field}.")
        if type(hero.get('attack')) is not int or hero['attack'] < 0 or type(hero.get('active')) is not bool:
            raise ValueError(f"Shadow {hero['code']}: invalid attack/active.")
        if hero['passive_key'] not in abilities or hero_image_path(hero['image']) is None:
            raise ValueError(f"Shadow {hero['code']}: missing passive/image.")
    links = []
    for boss in bosses:
        code = boss.get('shadow_hero_code')
        if code:
            hero = by_code[code]
            if hero.get('shadow_of') and hero['shadow_of'] != boss['code']:
                raise ValueError(f"Boss {boss['code']}: Shadow counterpart mismatch.")
            links.append({'boss':boss['code'],'shadow':code,'extractable':boss['shadow_extractable']})
    missing_boss_images = [boss['code'] for boss in bosses if boss_image_path(boss.get('image')) is None]
    if content['missing_hero_images'] or missing_boss_images:
        raise ValueError(f"Missing images: heroes={content['missing_hero_images']}, bosses={missing_boss_images}.")
    active_pool = [hero for hero in heroes if hero.get('active',True) and hero['rarity'] in GACHA_RARITIES]
    pools = {rarity:sum(hero['rarity']==rarity for hero in active_pool) for rarity in GACHA_RARITIES}
    if not all(pools.values()):
        raise ValueError('An ordinary gacha rarity pool is empty.')
    safety = validate_combat_content(heroes,bosses)
    return {'heroes':len(heroes),'shadow_heroes':len(shadows),'bosses':len(bosses),
            'shadow_links':links,'ordinary_gacha_pools':pools,
            'consumable_definitions':sum(item['kind']=='consumable' for item in definitions.values()),
            'equipment_definitions':sum(item['kind']=='equipment' for item in definitions.values()),
            'missing_images':0,'warnings':safety['warnings']}


if __name__ == '__main__':
    print(json.dumps(audit(),ensure_ascii=False,indent=2))
