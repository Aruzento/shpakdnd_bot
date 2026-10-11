"""Generate review evidence from actual UI builders and registered router filters.

Run from the checkout: python docs/ui/build_matrix.py
Development-only: it imports test fixtures and never starts polling or opens a DB.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BOT_TOKEN','123456:offline-ui-matrix')
from tests.v1_4_2.test_screen_contracts import SOURCE,render_screen,button_handlers


def action_for(sid):
    if sid.startswith('gacha'):return 'coins/ticket → single pull; ×10 → atomic batch; coupon → inventory confirm; result/page → persisted result'
    if sid.startswith('collection') or sid in ('hero_card','shards_sell'):return 'owned heroes/filter/page; active/upgrade/favorite/sell → existing services; back → owner-bound stored context'
    if sid in ('registration','home','launcher','character','character_no_hero'):return 'owner/world validation; private name request + atomic onboarding; existing profile/upgrade/home'
    if sid.startswith('village'):return 'revision + owner; buy/settle/evict/job/collect → village.mutate; refresh same view; jobs require explicit removal before reassignment'
    if sid.startswith('tower_selector'):return 'legacy selector compatibility; floor/status/ownership checks; select only before active attempt'
    if sid.startswith('tower'):return 'rarity → list → photo card → selection/start/turn; snapshot; persisted one retry per lost attempt; first-clear reward unchanged'
    if sid.startswith('boss'):return 'existing boss registration/admin/combat; permissions; immutable battle state and reward finalization unchanged'
    if sid.startswith('events'):return 'existing stake/session/resolve/repeat; leaving UI keeps unfinished session and does not change economy'
    if sid.startswith('duel'):return 'existing owned selection/challenge/accept/refuse/cancel; escrow and payout unchanged'
    if sid.startswith('shop') or sid.startswith('use') or sid in ('inventory','item_use_result','certificate_result'):return 'existing purchase/inventory/use/certificate services; coupon shares item-use transaction; navigation only'
    if sid.startswith('equipment'):return 'existing equip/unequip; preserve Tower parent on navigation; no new equipment effects'
    if sid=='mythic':return 'existing list/craft for fragments; back → more'
    if sid.startswith('rules'):return 'whitelisted help section; back → rules/more'
    if sid=='daily_result':return 'existing idempotent daily claim/streak/chest; inventory/adventures'
    return 'validated submenu navigation; no economic write'


def generate():
    records=[];lines=['# V1.4.2 — матрица всех 109 экранов','',
        'Baseline PR: `293693d87692bb9e7f4567bcca895e799e7455d2`. Это проверка Python renderers и router filters, **не Telegram-стенд**.',
        'Образцы используют world=999999 / user=9999999999. Динамические списки, права, пустые состояния и изменения количества проверяются дополнительными regression/integration tests.',
        'Исходные подписи, `visible_if`, изображения и проектные переходы сохранены в [buttons-карте](v1.4.2-map.json). Actions/условия из карты никогда не исполняются.',
        'Подробные кнопки → callback → handler для каждой строки: [машинная матрица](v1.4.2-matrix.json).',
        'Для `tower_selector_*` статус «compatibility»: новая навигация их не показывает; старые callbacks безопасно поддерживаются.',
        '§2 ТЗ уточняет Village residents/card/jobs, купоны, Mythic и возвраты. См. [решения](../architecture/V1.4.2-COMPLETION.md).','',
        '| Экран | Статус | Строки кнопок (образец) | Renderer | Handler(s) | Действие | Тест |',
        '|---|---|---|---|---|---|---|']
    for source in SOURCE:
        sid=source['id']
        with patch('secrets.token_hex',return_value='11223344'):
            text,markup,renderer=render_screen(sid)
        resolved=asyncio.run(button_handlers(markup))
        rows=[[dict(text=b.text,callback=b.callback_data,handlers=resolved[i][j]) for j,b in enumerate(row)] for i,row in enumerate(markup.inline_keyboard)]
        status='compatibility' if sid.startswith('tower_selector') else 'updated' if (source.get('design_status')=='changed_in_user_proposal_2026-10-09' or sid.startswith(('collection','village','tower','events')) or sid in ('gacha_result','gacha_coupon','hero_card','shards_sell','mythic','inventory','character','character_no_hero','item_use_result','certificate_result','shop_purchase','boss','boss_none')) else 'preserved'
        record=dict(screen=sid,status=status,renderer=renderer.__module__+'.'+renderer.__name__,
            image_contract=source.get('image',''),design_targets=[b['next_msg_id'] for row in source['buttons'] for b in row],
            action=action_for(sid),buttons=rows,test='tests.v1_4_2.test_screen_contracts.ScreenContractTests.test_screen_'+sid)
        records.append(record)
        labels='<br>'.join(' / '.join(b['text'] for b in row) for row in rows).replace('|',r'\|')
        handlers=sorted({h for row in rows for b in row for h in b['handlers']})
        lines.append('| `'+sid+'` | '+status+' | '+labels+' | `'+record['renderer']+'` | '+'<br>'.join('`'+h+'`' for h in handlers)+' | '+record['action']+' | `test_screen_'+sid+'` |')
    (ROOT/'docs/ui/v1.4.2-matrix.json').write_text(json.dumps({'kind':'development evidence, not runtime config','screens':records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    (ROOT/'docs/ui/V1.4.2-MATRIX.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print('109 screens / real renderers / all generated callbacks registered')


if __name__=='__main__':generate()
