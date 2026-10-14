"""Small persistent navigation records; never evaluate UI-map actions/conditions."""
import json
import secrets
import hashlib
import re
from app.config import DB_PATH
from app.mini.db import connect_mini_db


def remember(player_id,screen,context,db_path=DB_PATH):
    if screen!='collection':raise ValueError('Неизвестный контекст меню.')
    from app.mini.ui.hero_selector import decode
    decode(context['filter'],'c')
    if context['view'] not in ('home','all','results','collection'):raise ValueError('Неизвестный список.')
    if type(context['page']) is not int or context['page']<0:raise ValueError('Некорректная страница.')
    token=secrets.token_hex(4)
    with connect_mini_db(db_path) as conn:
        conn.execute('INSERT INTO mini_ui_navigation VALUES(?,?,?,?)',
                     (token,int(player_id),screen,json.dumps(context)))
    return token


def restore(player_id,token,db_path=DB_PATH):
    legacy=re.fullmatch(r'([ar])(\d+)~([0-9a-z]{7})',token)
    if legacy:
        from app.mini.ui.hero_selector import decode
        decode(legacy[3],'c')
        return dict(view='all' if legacy[1]=='a' else 'results',page=int(legacy[2]),filter=legacy[3])
    with connect_mini_db(db_path) as conn:
        row=conn.execute('SELECT context_json FROM mini_ui_navigation WHERE token=? AND player_id=? AND screen=?',
                         (token,int(player_id),'collection')).fetchone()
    if not row:raise ValueError('Контекст карточки устарел. Открой коллекцию заново.')
    return json.loads(row[0])


def split_context(payload):
    parts=payload.split('~')
    if len(parts)==3 and re.fullmatch(r'[ar]\d+',parts[1]):return parts[0],'~'.join(parts[1:])
    if len(parts)>2:raise ValueError('Некорректный контекст.')
    return parts[0],parts[1] if len(parts)==2 else ''


def legacy_operation(callback,prefix):
    message=getattr(callback,'message',None)
    identity=(getattr(message,'ephemeral_message_id',None) or getattr(message,'message_id',None)
              or getattr(callback,'inline_message_id',None) or callback.id)
    chat=getattr(message,'chat',None)
    scope=(getattr(chat,'id',None),getattr(message,'message_thread_id',None),identity)
    return prefix+':'+hashlib.sha256(str(scope).encode()).hexdigest()[:20]


def claim_publication(player_id,key,db_path=DB_PATH):
    # Reserve before Telegram. An uncertain send is never retried automatically.
    with connect_mini_db(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        claimed=bool(conn.execute('INSERT OR IGNORE INTO mini_ui_publications(player_id,operation_key) VALUES(?,?)',
                                  (int(player_id),key)).rowcount)
        # Preserve the earlier draft's public-share replay if code is rolled back.
        legacy=key
        if key.startswith('share:') and '.' in key:legacy=key.replace('.',':',1)
        conn.execute('INSERT OR IGNORE INTO mini_public_shares(player_id,operation_key) VALUES(?,?)',(int(player_id),legacy))
        return claimed


def validate_navigation(player_id,token,db_path=DB_PATH):
    saved=restore(player_id,token,db_path)
    return remember(player_id,'collection',saved,db_path) if '~' in token else token
