# Исторические SQLite contracts — PR #9

Актуальный guard-only этап: [Complete SQLite contracts](COMPLETE_SCHEMA_GUARD.md) — все known tables/indexes и required triggers. Исторические результаты ниже сохранены; новый exact-SHA отчёт и CI не подменяются прежними.

Исходный tooling commit: dfc1e2d031b59f8b9107fe698c779c3ad0e98369.
Игровой app tree остаётся cb678fe9f0978bb115a12e27c56a7160d0c94e4c.
Финальный SHA и результаты полного CI приводятся в отдельном итоговом отчёте PR.
Никакие серверные операции данным документом не разрешаются.

## Источник доказательств

Владелец передал только DDL девяти таблиц и индексов из согласованного backup
/opt/shpakdnd-backups/pre-v15-20261028-193144-661948.sqlite3.
SHA-256 acb2620a631f790cce82e4d4f1339a2a601c2e30dd5756cd1d2d31cfc8977b51
проверен владельцем до/после. Codex не подключался к production/TEST,
не получал backup или строки игроков и лично production-copy не проверял.

Schema-only фикстура: tests/v1_5/fixtures/historical_v14_nine_tables.json.
На ней воспроизводятся настоящие init_db/init_mini_db/init_boss_db V1.4,
предыдущей V1.5 и текущей V1.5. Три последовательных применения текущих миграций
проверяются на заполненной синтетической базе, а не production data.

## Все девять причин

| Таблица | Подтверждённое отличие от чистой V1.4 |
|---|---|
| mini_boss_participants | hero_id/attack/боевые поля добавлялись ALTER и находятся после старых полей |
| mini_bosses | template/image/publication/reward/combat поля добавлялись после старых полей |
| mini_event_sessions | порядок тот же; только отступы SQL partial UNIQUE index |
| mini_heroes | image_path после created_at, затем combat traits, а не внутри исходного CREATE |
| mini_item_uses | порядок тот же; только отступы SQL partial UNIQUE index |
| mini_player_heroes | stars после obtained_at; отсутствует inline CHECK(stars>=0) |
| mini_players | shards после last_seen_at; отсутствует inline CHECK(shards>=0) |
| mini_wallet_transactions | operation_key после created_at; также отступы SQL partial UNIQUE index |
| mini_worlds | launcher_message_id после created_at |

PRAGMA table_xinfo definitions по именам и FOREIGN KEY совпадают для всех девяти.
Семь физических порядков различаются. PK/UNIQUE key names/order/collation/direction
совпадают; SQL CHECK отдельно проверен и не объявляется одинаковым там, где его нет.

Точное происхождение обоих index SQL воспроизведено по Git:
55aea187a7c6f23dd010a39f0ca2eb8a6b87716d:app/mini/schema.py (Events),
c61c4e81e55ed424b0cc410a2d2886ac3c65fbcd:app/mini/schema.py (Item uses).
Их sqlite_master.sql побайтово совпадает с переданным владельцем.
Предикаты остаются WHERE status = 'active' и WHERE operation_key <> ''.
Refactor90c5598 перенёс SQL в migration modules и изменил отступы. Это доказательство
конкретных известных вариантов, а не разрешение любых похожих WHERE.

Проверены writers/readers трёх аудированных источников: INSERT в эти девять таблиц
указывает колонки; SELECT * / h.* / bp.* читаются через sqlite3.Row/dict и именованные
поля. Числовые row[0]/row[1] используются с явными SELECT columns/COUNT, не порядком
этих таблиц. Синтетические история, кошелёк, ownership, завершённый Boss/Event/item
сохраняются после миграций и повторного открытия. Отдельный CLI test сохраняет S5
ownership и feedback/Stories blockers.

## Выбранный механизм

Никакой семантической нормализации в runtime guard нет.
Сохранена исходная order-sensitive _table_signature; добавлены только конкретные
подписи переданных DDL и их результатов настоящих миграций трёх reviewed источников.

Для ровно этих девяти таблиц требуется дополнительный парный exact contract:
исходная signature + буквальный CREATE TABLE SQL + все UNIQUE index names/origin,
partial flags, полный index_xinfo и буквальный SQL индекса. В него входят CHECK,
DEFAULT, AUTOINCREMENT, table options, PK/FK, типы/порядок колонок,
UNIQUE collation/direction и WHERE. Ни новые перестановки, ни произвольный SQL
формат, ни иной CHECK/WHERE не добавляются автоматически.
Неполный exact inventory также fail-closed. Остальные таблицы не получают новых
исторических вариантов и продолжают проверяться прежним frozen inventory.

Владелец отдельно утвердил отсутствие двух исторических CHECK с компенсацией:
guard во всех поддержанных профилях read-only проверяет mini_players.shards,
mini_player_heroes.shards и mini_player_heroes.stars. NULL, typeof!='integer' и
отрицательные значения дают BLOCKED_INVALID_SHARDS_OR_STARS (или более ранний
BLOCKED_INTEGRITY для нарушения NOT NULL). Автоматического исправления нет.
Это диагностика согласованного legacy состояния, а не DDL/runtime migration.

mode=ro, query_only=ON, одна read transaction; WAL виден. DB/WAL bytes до/после
повторных проверок сравниваются. Не вызываются migration/recovery/wallet/Telegram
операции guard-ом. Реальные sending/uncertain публикации, неизвестные статусы,
неподдержанные ownership/ledger/feedback/PvP сохраняют прежние blockers.

## Обязательное ограничение runtime: BLOCKED, не скрыто

Абсолютная гарантия «runtime не может записать нецелое shards/stars» НЕ доказана.
Подтверждён минимальный counterexample на синтетической исторической БД:
app.mini.boss.repository.add_kill_shards(conn, pid, 1) при shards=SQLite INTEGER MAX
сохраняет typeof(shards)='real'. Аналогичный класс риска у SQL stars=stars+1.
Это существующий риск SQL arithmetic overflow, не изменение экономики этой задачей.
CHECK(x>=0) чистой схемы также не запрещает REAL.

Новый guard обнаруживает получившееся состояние и блокирует все targets без ремонта,
но не предотвращает будущую запись работающего приложения. Нормальные пути трат
проверяют баланс/стоимость, Python-bound int overflow завершает транзакцию ошибкой;
не все SQL increments имеют явную upper-bound/type проверку.
Runtime исправлять в этих границах запрещено. Требуется отдельное решение владельца
по этому обнаруженному блокеру; успешные schema/CI tests не означают выполнения
абсолютной runtime гарантии или production readiness.

## Read-only проверка владельцем: две реальные offline копии

Codex не выполняет команды на сервере. Сначала оператор готовит чистый доверенный
checkout финального tooling SHA с полной Git историей; это отдельная локальная
подготовка без deployment. TOOLS ниже — этот checkout. В существующем
/opt/shpakdnd-backups/v15-guard-nl82vvyc/project/shpakdnd.db должна лежать именно
отдельная успешно мигрированная offline копия, не symlink/рабочая production DB.
Команда не мигрирует её и явно вернёт NOT_RUN, если V1.5 shape отсутствует.

Установить FINAL_SHA из финального отчёта PR #9 и TOOLS, затем выполнить:

```bash
export FINAL_SHA=FINAL_SHA_FROM_PR9_REPORT
export TOOLS=/ABS/TRUSTED/REVIEWED/FINAL_CHECKOUT
python3 - <<'PY'
import os,hashlib,importlib.util,json,sqlite3,subprocess
from pathlib import Path
from contextlib import closing
tools=Path(os.environ['TOOLS']).resolve(strict=True)
final=os.environ['FINAL_SHA']
def git(*args):return subprocess.check_output(['git','-C',str(tools),*args],text=True).strip()
assert git('rev-parse','HEAD')==final, 'WRONG_TOOLING_SHA'
assert not git('status','--porcelain'), 'DIRTY_TOOLING'
assert git('rev-parse','HEAD:app')=='cb678fe9f0978bb115a12e27c56a7160d0c94e4c', 'WRONG_APP_TREE'
spec=importlib.util.spec_from_file_location('reviewed_guard',tools/'deploy/release_guard.py')
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
backup=Path('/opt/shpakdnd-backups/pre-v15-20261028-193144-661948.sqlite3')
migrated=Path('/opt/shpakdnd-backups/v15-guard-nl82vvyc/project/shpakdnd.db')
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert digest(backup)=='acb2620a631f790cce82e4d4f1339a2a601c2e30dd5756cd1d2d31cfc8977b51','BACKUP_HASH_MISMATCH'
targets=['d077dd2a2c29ad420b138bf07af23c77f4674d0b','cef77cc87f6a2286284f5cf8fdb851c775b04dd1',final]
for label,path in [('original_v14',backup),('migrated_v15',migrated)]:
    assert path.is_file() and not path.is_symlink(),'UNSAFE_OFFLINE_PATH'
    assert path.resolve().is_relative_to(Path('/opt/shpakdnd-backups')),'OUTSIDE_BACKUPS'
    files=[path]+[p for p in [Path(str(path)+'-wal')] if p.exists()]
    before={str(p):digest(p) for p in files}
    for target in targets:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if label=='migrated_v15':
                tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                inv=json.loads((tools/'deploy/release_schema.json').read_text())
                if not (set(inv)-{'_baseline'}|set(guard.FEEDBACK_SCHEMA))<=tables:
                    print(json.dumps({'copy':label,'target':target,'status':'NOT_RUN','reason':'MIGRATED_COPY_NOT_READY'}));continue
            try:
                result=guard.inspect_connection(c,guard.target_capability(tools,target))
                out={'copy':label,'target':target,'status':'PASS' if result['rollback_ready'] else 'BLOCKED','blockers':result['blockers']}
            except (guard.GuardError,sqlite3.Error) as e:
                out={'copy':label,'target':target,'status':'BLOCKED','blockers':[str(e) if isinstance(e,guard.GuardError) else 'BLOCKED_DATABASE_OR_GUARD_UNAVAILABLE']}
            print(json.dumps(out,sort_keys=True))
    assert before=={str(p):digest(p) for p in files},'OFFLINE_COPY_CHANGED'
print('READ_ONLY_DB_WAL_HASHES_UNCHANGED')
PY
```

Команда использует только frozen guard core с проверенными target identities и
двумя явно указанными offline путями, не operational deploy CLI и не разрешение
rollback. Вывод — коды и агрегированный verdict, без IDs/балансов/журналов.
NOT_RUN не означает PASS. При BLOCKED_UNKNOWN_TABLE_CONTRACT нужны только DDL
проблемных таблиц и повтор уровней1–2; полный CI до исправления не повторять.
Перед фактическим релизом штатный preflight/guard CLI с project boundary остаётся
обязательным. При любых неизвестных данных/DDL или неопределённой доставке STOP.

## Матрица и восстановление

| Проверенное состояние | V1.4 target | прежняя V1.5 | feedback V1.5 |
|---|---|---|---|
| Историческая V1.4, валидные shards/stars, все прочие guards готовы | условно ALLOW | условно ALLOW | условно ALLOW |
| Та же копия мигрирована, нет V1.5 persistent progress | условно ALLOW | условно ALLOW | условно ALLOW |
| S5/Mythic ownership, V1.5 ledger/Raid/Story/Pet progress | BLOCKED | по поддержанным состояниям | по поддержанным состояниям |
| Feedback rows, включая terminal achievements | BLOCKED | BLOCKED | по поддержанным состояниям |
| Недопустимые shards/stars, неизвестные schema/status/tree | BLOCKED | BLOCKED | BLOCKED |
| Sending/uncertain delivery, unresolved PvP/economy | BLOCKED | BLOCKED | BLOCKED |

ALLOW означает совместимость известного снимка, не разрешение deployment/изменения
старых игровых правил. Startup старого кода сам по себе ничего не разрешает.

Устанавливать только весь новый frozen bundle одного reviewed SHA штатным installer
после отдельного разрешения. Прежний immutable bundle сохраняется. Возврат именно
bundle выполняется доверенным installer с --activate-existing PREVIOUS_BUNDLE_SHA
и общим lock; не app checkout/SQLite restore/systemd restart. Старый bundle не знает
новых contracts и может снова блокировать историческую БД. Использовать его для
обхода проверки нельзя. Подробные команды и предыдущие доказательства сохранены
в ROLLBACK_COMPATIBILITY_FEEDBACK.md и ATOMIC_BUNDLE_AND_CI.md.

При BLOCKED сохранять текущие БД/WAL/логи, не удалять данные, не запускать старый код,
не возвращать старый backup поверх нового прогресса. Нужен совместимый target или
отдельный согласованный план. main, TEST и production данной задачей не меняются.
