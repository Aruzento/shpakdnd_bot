"""Canonical definitions across catalogs; equipment retains its slot ownership."""
from app.mini.catalog import load_shop_catalog
from app.mini.effects.registry import validate_effect_key

WEEKLY_ITEMS=(
 ('tower_equipment_chest','Сундук экипировки Испытаний','tower_equipment_chest'),
 ('village_gold_boost','Усиление рынка +25% на 8 часов','village_gold_boost'),
 ('village_shards_boost','Усиление шахты +25% на 8 часов','village_shards_boost'),
)


def load_definitions():
    from app.mini.boss.catalog import load_boss_item_catalog
    from app.mini.equipment.catalog import load_catalog as equipment_catalog
    definitions={}
    def add(raw, kind, source):
        code=raw.get('code')
        if not isinstance(code,str) or not code.strip() or code!=code.strip(): raise ValueError(f'{source}: invalid item code.')
        item=dict(raw,kind=kind)
        if not isinstance(item.get('name'),str) or not item['name'].strip(): raise ValueError(f'{code}: invalid name.')
        if kind=='consumable':
            item.setdefault('description','');item.setdefault('effect_key','')
            item.setdefault('stackable',True);item.setdefault('active',True)
            item.setdefault('category','boss_reward')
            if not isinstance(item['description'],str) or not isinstance(item['category'],str) or not item['category'].strip():
                raise ValueError(f'{code}: invalid item fields.')
            if type(item['active']) is not bool or type(item['stackable']) is not bool: raise ValueError(f'{code}: invalid flags.')
            validate_effect_key(item['effect_key'])
        if code in definitions and definitions[code]!=item:
            raise ValueError(f'Conflicting item definition: {code} ({source}).')
        definitions[code]=item
    for product in load_shop_catalog()['products']:
        if product['delivery']=='inventory':
            add(dict(product['item'],category=product['category'],active=product.get('active',True)), 'consumable','shop')
    for item in load_boss_item_catalog()['items']: add(item,'consumable','boss')
    for code,name,effect in WEEKLY_ITEMS:
        add(dict(code=code,name=name,category='consumable',effect_key=effect),'consumable','weekly')
    # Read template names without recursively invoking Boss validation, which
    # itself validates reward codes through this registry.
    import json
    from app.mini.boss.catalog import BOSSES_JSON
    for boss in json.loads(BOSSES_JSON.read_text(encoding='utf-8'))['bosses']:
        add(dict(code='boss_egg_'+boss['code'],name='Яйцо: '+boss['name'],category='boss_egg'),'consumable','boss eggs')
    for item in equipment_catalog()['items']: add(item,'equipment','equipment')
    return definitions


def get_definition(code):
    return load_definitions().get(code)


def sync_in_transaction(conn):
    definitions=load_definitions()
    for item in definitions.values():
        if item['kind']!='consumable': continue
        conn.execute('''INSERT INTO mini_items(code,name,category,description,effect_key,stackable,active)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,
            category=excluded.category,description=excluded.description,effect_key=excluded.effect_key,
            stackable=excluded.stackable,active=excluded.active''',
            (item['code'],item['name'],item['category'],item['description'],item['effect_key'],int(item['stackable']),int(item['active'])))
    return definitions
