from app.mini.presentation import format_player_mention
"""Mini admin use cases. Each mutation and its audit commit in one transaction."""
import sqlite3
from app import config
from app.topic_guard import require_mini_topic
from app.mini.db import connect_mini_db
from app.mini.hero_upgrades import calculate_attack
from app.mini.wallet import change_balance_in_transaction
from app.mini.shards import change_shards_in_transaction
from app.mini.equipment.service import aggregate_in_transaction,grant_in_transaction,remove_in_transaction
from app.mini.superadmin.access import require_superadmin


def _world(conn,command):
    require_mini_topic(command.chat_id,command.thread_id)
    row=conn.execute("SELECT * FROM mini_worlds WHERE chat_id=? AND thread_id=? AND enabled=1",
                     (command.chat_id,command.thread_id)).fetchone()
    if row is None:
        raise ValueError("Mini-мир не найден или отключён.")
    return dict(row)


def _player(conn,world_id,target):
    if target.isdigit():
        rows=conn.execute("SELECT * FROM mini_players WHERE world_id=? AND telegram_user_id=?",(world_id,int(target))).fetchall()
    else:
        rows=conn.execute("SELECT * FROM mini_players WHERE world_id=? AND lower(username)=?",(world_id,target)).fetchall()
    if len(rows)!=1:
        raise ValueError("Mini-игрок не найден или username неоднозначен. Используй numeric Telegram ID.")
    return dict(rows[0])


def _look(conn,player,flags):
    lines=[f"{player['character_name']} — {player['telegram_user_id']} {format_player_mention(player,conn=conn)}"]
    if '-c' in flags: lines.append(f"Монеты: {player['coins']}")
    if '-s' in flags: lines.append(f"Осколки: {player['shards']}")
    if '-p' in flags:
        lines.append("Герои:")
        for h in conn.execute("""SELECT h.*,ph.stars FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
            WHERE ph.player_id=? ORDER BY h.name""",(player['id'],)):
            active=' · активный' if player['active_hero_id']==h['id'] else ''
            lines.append(f"{h['name']} [{h['code']}] {h['rarity']} ★{h['stars']} ATK {calculate_attack(h['attack'],h['stars'])}{active}")
    if '-i' in flags:
        lines.append("Расходуемые Mini items:")
        for r in conn.execute("""SELECT i.name,i.code,v.quantity FROM mini_inventory v JOIN mini_items i ON i.id=v.item_id
            WHERE v.player_id=? AND v.quantity>0 ORDER BY i.name""",(player['id'],)):
            lines.append(f"{r['name']} [{r['code']}] ×{r['quantity']}")
        equipped={r['slot']:r for r in conn.execute("""SELECT e.* FROM mini_equipment_slots s
            JOIN mini_equipment e ON e.code=s.code WHERE s.player_id=?""",(player['id'],))}
        lines.append("Экипировано:")
        for slot in ('helmet','ring','cloak'):
            e=equipped.get(slot)
            lines.append(f"{slot}: {e['name']} +{e['attack_bonus']} [{e['code']}]" if e else f"{slot}: пусто")
        lines.append(f"Equipment ATK: +{aggregate_in_transaction(conn,player['id'])}")
        lines.append("Во владении Equipment:")
        for r in conn.execute("""SELECT e.*,o.quantity FROM mini_equipment_owned o JOIN mini_equipment e ON e.code=o.code
            WHERE o.player_id=? ORDER BY e.slot,e.attack_bonus""",(player['id'],)):
            lines.append(f"{r['name']} [{r['code']}] +{r['attack_bonus']} ×{r['quantity']}")
    return '\n'.join(lines)


def _hero(conn,player,value,deleting):
    hero=conn.execute("SELECT * FROM mini_heroes WHERE lower(code)=lower(?)",(value,)).fetchone()
    if hero is None: raise ValueError("Герой не найден.")
    owned=conn.execute("SELECT 1 FROM mini_player_heroes WHERE player_id=? AND hero_id=?",(player['id'],hero['id'])).fetchone()
    if not deleting:
        if owned: return False,"Герой уже есть."
        conn.execute("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",(player['id'],hero['id']))
        if player['active_hero_id'] is None:
            conn.execute("UPDATE mini_players SET active_hero_id=? WHERE id=?",(hero['id'],player['id']))
        return True,f"Выдан герой {hero['name']}."
    if not owned: raise ValueError("У игрока нет этого героя.")
    if conn.execute("SELECT 1 FROM mini_tower_attempts WHERE player_id=? AND hero_id=? AND status='active'",(player['id'],hero['id'])).fetchone():
        raise ValueError("Герой используется активной попыткой Tower.")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_boss_participants'").fetchone():
        busy=conn.execute("""SELECT 1 FROM mini_boss_participants bp JOIN mini_bosses b ON b.id=bp.boss_id
            WHERE bp.player_id=? AND b.status IN ('announced','ready','fighting')
            AND CASE WHEN b.status IN ('announced','ready') THEN ? ELSE bp.hero_id END=?""",
            (player['id'],player['active_hero_id'],hero['id'])).fetchone()
        if busy: raise ValueError("Герой используется активным Boss battle.")
    conn.execute("DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?",(player['id'],hero['id']))
    if player['active_hero_id']==hero['id']:
        conn.execute("""UPDATE mini_players SET active_hero_id=(SELECT MIN(hero_id) FROM mini_player_heroes WHERE player_id=?)
            WHERE id=?""",(player['id'],player['id']))
    return True,f"Удалён герой {hero['name']}."


def _item(conn,player_id,code,deleting):
    eq=conn.execute("SELECT code FROM mini_equipment WHERE lower(code)=lower(?)",(code,)).fetchone()
    if eq:
        if deleting: remove_in_transaction(conn,player_id,eq[0])
        else: grant_in_transaction(conn,player_id,eq[0])
        return
    item=conn.execute("SELECT * FROM mini_items WHERE lower(code)=lower(?)",(code,)).fetchone()
    if item is None: raise ValueError("Mini item/equipment code не найден.")
    owned=conn.execute("SELECT quantity FROM mini_inventory WHERE player_id=? AND item_id=?",(player_id,item['id'])).fetchone()
    quantity=int(owned[0]) if owned else 0
    if deleting:
        if quantity<=0: raise ValueError("У игрока нет этого предмета.")
        conn.execute("UPDATE mini_inventory SET quantity=quantity-1 WHERE player_id=? AND item_id=?",(player_id,item['id']))
    else:
        if not item['stackable'] and quantity>0: raise ValueError("Нестакируемый предмет уже есть.")
        conn.execute("""INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(?,?,1)
            ON CONFLICT(player_id,item_id) DO UPDATE SET quantity=quantity+1,updated_at=CURRENT_TIMESTAMP""",(player_id,item['id']))


def execute(admin_user_id,command,*,operation_key='',db_path=None):
    db_path=config.DB_PATH if db_path is None else db_path
    require_superadmin(admin_user_id)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if command.action in {'superadd','superdel','superluck'}:
            if not operation_key: raise ValueError("Для мутации нужен стабильный operation key.")
            conn.execute("BEGIN IMMEDIATE")
        world=_world(conn,command)
        if command.action=='superchars':
            return '\n'.join([f"Mini players {command.chat_id}:{command.thread_id}"]+[
                f"{r['telegram_user_id']} {format_player_mention(r,conn=conn)} — {r['character_name']}"
                for r in conn.execute("SELECT * FROM mini_players WHERE world_id=? ORDER BY telegram_user_id",(world['id'],))])
        player=_player(conn,world['id'],command.target)
        if command.action=='superlook': return _look(conn,player,command.flags)
        if conn.execute("SELECT 1 FROM mini_superadmin_audit WHERE operation_key=?",(operation_key,)).fetchone():
            return "Эта административная операция уже выполнена."
        amount=None;resource='gacha_guarantee';entity='legendary';message='';applied=True
        if command.action=='superluck':
            row=conn.execute("SELECT forced_legendary FROM mini_gacha_guarantees WHERE player_id=?",(player['id'],)).fetchone()
            if row and row[0]:
                applied=False;message='Гарантия Legendary уже установлена.'
            else:
                conn.execute("""INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)
                    ON CONFLICT(player_id) DO UPDATE SET forced_legendary=1""",(player['id'],))
                message='Следующий успешный призыв: Legendary 100%.'
        else:
            flag=command.flags[0];deleting=command.action=='superdel'
            resource={'-c':'coins','-s':'shards','-p':'hero','-i':'item'}[flag];entity=command.value
            if flag in {'-c','-s'}:
                amount=int(command.value)
                if amount<=0: raise ValueError("Количество должно быть положительным.")
                delta=-amount if deleting else amount
                if flag=='-c':
                    balance=change_balance_in_transaction(conn,player['id'],delta,'Mini superadmin',
                        'superadmin',None,operation_key)['balance']
                else: balance=change_shards_in_transaction(conn,player['id'],delta)
                message=f"{resource}: изменение {delta}, баланс {balance}."
            elif flag=='-p': applied,message=_hero(conn,player,command.value,deleting)
            else:
                _item(conn,player['id'],command.value,deleting)
                message=f"Предмет {command.value}: {'удалён' if deleting else 'выдан'}."
        conn.execute("""INSERT INTO mini_superadmin_audit
            (admin_user_id,action,world_id,target_user_id,player_id,resource,entity_code,amount,operation_key)
            VALUES(?,?,?,?,?,?,?,?,?)""",(admin_user_id,command.action,world['id'],player['telegram_user_id'],
                player['id'],resource,entity,amount,operation_key))
        return f"{'✅' if applied else 'ℹ️'} {player['character_name']}: {message}"
