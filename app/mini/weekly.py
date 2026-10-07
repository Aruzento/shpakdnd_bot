"""Seven equally weighted weekly reward categories reuse canonical item codes."""
import secrets
from app.mini.items import grant_item_in_transaction
from app.mini.catalog import load_shop_catalog
from app.mini.boss.catalog import load_boss_item_catalog, list_boss_templates

NEW_ITEMS=(
 ('tower_equipment_chest','Сундук экипировки Испытаний','tower_equipment_chest'),
 ('village_gold_boost','Усиление рынка +25% на 8 часов','village_gold_boost'),
 ('village_shards_boost','Усиление шахты +25% на 8 часов','village_shards_boost'),
)


def sync_items_in_transaction(conn):
    items=[dict(code=c,name=n,category='consumable',effect_key=e) for c,n,e in NEW_ITEMS]
    items.extend(load_boss_item_catalog()['items'])
    items.extend(p['item'] for p in load_shop_catalog()['products'] if p['delivery']=='inventory')
    for boss in list_boss_templates():
        items.append(dict(code='boss_egg_'+boss['code'],name='Яйцо: '+boss['name'],category='boss_egg',effect_key=''))
    for item in items:
        conn.execute('''INSERT INTO mini_items(code,name,category,description,effect_key,stackable,active)
            VALUES(?,?,?,?,?,1,1) ON CONFLICT(code) DO NOTHING''',
            (item['code'],item['name'],item.get('category','consumable'),item.get('description',''),item.get('effect_key','')))


def grant_chest(conn,player_id,day,*,chooser=None):
    choose=chooser or secrets.choice
    sync_items_in_transaction(conn)
    categories=('tower_equipment_chest','boss_coin_pouch','boss_shard_casket','egg','summon_ticket','village_gold_boost','village_shards_boost')
    code=choose(categories)
    if code=='egg': code='boss_egg_'+choose(list_boss_templates())['code']
    conn.execute('INSERT INTO mini_daily_chests VALUES(?,?,?)',(player_id,day,code))
    grant_item_in_transaction(conn,player_id,code,1)
    return code
