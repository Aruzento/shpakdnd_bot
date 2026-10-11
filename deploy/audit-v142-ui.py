"""Offline screen audit: real keyboard builders, disposable test SQLite only.

Run from repository root: python deploy/audit-v142-ui.py --output /path/to/audit
Design documents are data; actions and visible_if are never executed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
NOTES={
    'registration':'§2.4: private deep link → имя → home; /minicreate сохранён.',
    'gacha_coupon':'§2.5: три реальных типа + количество → подтверждение → обычная карточка.',
    'gacha_result':'§3.2: upgrade перенесён в профиль/коллекцию; active условный; Back → gacha.',
    'gacha_batch_result':'§2.6/3.2: сохранённые 10 исходов, один выбранный результат, границы пагинации.',
    'hero_card':'§2.8/9: продажа осколков сохраняется; Back включает исходные фильтр/страницу.',
    'mythic':'§2.3: Back → more; создание/пагинация зависят от фрагментов и актуального каталога.',
    'tower':'§3.4: dispatcher показывает только текущее состояние, не все действия сразу.',
    'tower_hero_card':'§3.4: selected/unselected взаимоисключающие; соседние герои и trial условные.',
    'tower_won':'§2: сохранён полезный переход на следующий этаж и просмотр экипировки.',
    'village_houses':'§2.1: добавлен отдельный ряд Жильцы; купить/поселить/выселить условные.',
    'village_residents':'§2.1: старый макет переопределён — 4/страница, 2×2, Home/Back → houses.',
    'village_resident':'§2.1: ровно 3 действия; старые прямые назначения безопасно отклоняются.',
}


def note(sid):
    if sid in NOTES:return NOTES[sid]
    if sid.startswith('tower_selector_'):return '§2.7: только совместимость старых callback; новый вход — редкость → список → карточка.'
    if sid.startswith('boss_selector_'):return '§2.7/3.5: общий Boss selector сохранён, 6 героев/страница; динамические фильтры.'
    if sid.startswith('collection_filter_option_'):return '§3.3: Любое + динамические валидные значения + К фильтрам + Home/Back.'
    if sid in ('collection_all','collection_results'):return '§3.3: 4 героя, 2×2; 3 кнопки пагинации; Back сохраняет контекст.'
    if sid.startswith('village_') and sid.endswith(('_add','_remove')):return '§2.2/3.6: список свободных/работающих на выбранном предприятии; остаёмся в списке.'
    if sid in ('events_rps','events_rps_choice','events_rps_result','events_lab_active'):return '§2.9: добавлен безопасный выход; сессия и экономическая механика сохраняются.'
    if sid.startswith(('boss','duel','equipment','use','shop','village','tower')):return 'Динамические сущности, границы и права формируются сервером; пустой список сохраняет возврат.'
    return 'Геометрия и действия сохранены либо обновлены по §3; callbacks проверены, runtime JSON не вводится.'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw=Path(tmp)/'runtime.json';env=dict(os.environ,BOT_TOKEN='123456:offline-ui-audit',PYTHONIOENCODING='utf-8',V142_UI_AUDIT_OUTPUT=str(raw))
        subprocess.run([sys.executable,'-m','unittest','tests.v1_4_2.test_screen_contract','-q'],cwd=ROOT,env=env,check=True)
        runtime=json.loads(raw.read_text(encoding='utf-8'))
    before=json.loads((ROOT/'docs/ui/V1.4.2-audit-before.json').read_text(encoding='utf-8'))
    design=json.loads((ROOT/'docs/ui/v1.4.2/DND_Mini_UI_Map_UPDATED_2026-10-09(1).json').read_text(encoding='utf-8'))
    proposed={s['id']:s for s in design['msg']};records=[]
    for s in before['screens']:
        sid=s['screen'];record=dict(s,runtime=runtime[sid],design=proposed[sid],decision=note(sid),
            test=f'tests.v1_4_2.test_screen_contract.ScreenCoverageTests.test_screen_{sid}',
            automatic='passed: Python builder, rows, callback handler binding, <=64 bytes',telegram='pending manual staging')
        records.append(record)
    assert len(records)==109
    result={'start_sha':before['start_sha'],'screen_count':109,'screens':records,
        'scope':'Offline renderer/handler audit. Does not certify Telegram rendering or execute every transition on a live bot.'}
    (out/'V1.4.2-screen-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# V1.4.2: матрица всех 109 экранов','',
        'Начальный SHA: `'+before['start_sha']+'`. Итоговый SHA фиксируется в отчёте и логах после коммита.',
        '', 'Каждый экран построен действующей Python-функцией на изолированной SQLite. Проверены строки, непустые подписи, длина callback и существование обработчиков. Строгая геометрия основных экранов проверена отдельными assertions; дополнительные сквозные тесты проверяют мутации/возвраты. Это не подтверждение ручного Telegram-стенда.',
        '', 'Число строк динамического списка отличается от шаблонного placeholder. Условные кнопки зависят от прав и состояния. Переопределения исторических макетов перечислены ниже согласно §2 полного ТЗ. Дизайн JSON не исполняется.',
        '', '| ID | План до кода | Runtime renderer | Реальные ряды в фикстуре | Решение / переходы | Проверка |', '|---|---|---|---|---|---|']
    for r in records:
        rows=' / '.join(' + '.join(row) for row in r['runtime']['rows']).replace('|','\\|')
        lines.append(f"| `{r['screen']}` | {r['plan']} | `{r['runtime']['renderer']}` | {rows} | {r['decision']} | `test_screen_{r['screen']}` PASS; Telegram ожидается |")
    lines+=['','Для каждого callback полный JSON содержит подпись, фактический payload и Python handler; исходная карта содержит проектные target/actions/image/visible_if. Предварительная матрица сохранена в `V1.4.2-audit-before.json`, решения 9 конфликтов — в `V1.4.2-conflicts.md`.','',
        'Изображения: home/character/gacha_result/hero_card/tower_hero_card используют существующий send_hero_card; при отсутствии/ошибке фото используется персональный текст. Остальные image-поля карт сохраняют прежние renderer. Каталоги изображений проверяет validate_v142 и тест неизменности пользовательского контента.','']
    (out/'V1.4.2-screen-matrix.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f'PASS: {len(records)} screen records → {out}')


if __name__=='__main__':main()
