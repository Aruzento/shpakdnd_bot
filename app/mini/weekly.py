"""Seven equally weighted weekly reward categories reuse canonical item codes."""
import secrets
from app.mini.items import grant_item_in_transaction
from app.mini.catalog import load_shop_catalog
from app.mini.boss.catalog import load_boss_item_catalog, list_boss_templates

from app.mini.item_registry import WEEKLY_ITEMS as NEW_ITEMS


def sync_items_in_transaction(conn):
    from app.mini.item_registry import sync_in_transaction
    sync_in_transaction(conn)


def grant_chest(conn,player_id,day,*,chooser=None):
    choose=chooser or secrets.choice
    sync_items_in_transaction(conn)
    categories=('tower_equipment_chest','boss_coin_pouch','boss_shard_casket','egg','summon_ticket','village_gold_boost','village_shards_boost')
    code=choose(categories)
    if code=='egg': code='boss_egg_'+choose(list_boss_templates())['code']
    conn.execute('INSERT INTO mini_daily_chests VALUES(?,?,?)',(player_id,day,code))
    grant_item_in_transaction(conn,player_id,code,1)
    return code
