# Complete SQLite release contracts — guard-only hardening

Исходный SHA:8e89473563e42fdd30c2aa15c04bd816a1f97ced.
App tree сохраняется cb678fe9f0978bb115a12e27c56a7160d0c94e4c.
Финальный SHA и exact-SHA CI приводятся в отдельном отчёте PR9.
Runtime/экономика/DDL migrations/author content не изменяются.

## Подтверждённые входы

Reviewed schema-only fixture:
tests/v1_5/fixtures/reviewed_schema_metadata.json,
SHA256 e95fb119b9cd6b5593b00a2e38ceed43c6559405b71102092a76b1193cc90b0f.
Original DB hash acb2620a631f790cce82e4d4f1339a2a601c2e30dd5756cd1d2d31cfc8977b51.
Migrated DB hash db5eccf7b8c1d7dd88b4aa63be0c493ff54acf081da09cc9bf496bde9ef953e4.
Owner collector сообщил DB/WAL hashes unchanged, WAL отсутствуют у обеих copy.
Codex получил только metadata, не DB/игровые строки и не подключался к серверам.

Исходный профиль:78 user tables,95 indexes,5 D&D triggers.
Мигрированный:105 user tables,126 indexes,7 triggers.
sqlite_sequence — внутренний объект, не включён в user counts.
Прежний JSON f0b6e2de... содержал две одинаковые V1.4 copy, поэтому не использован
как доказательство migrated profile. Исторический audit9 таблиц сохранён.

Оба новых профиля replayed только в пустых локальных SQLite. Все прежние
_table_signature и9 exact contracts совпали; owner index_list flags/index_xinfo
воспроизведены, SQL hashes7 triggers совпали. Сравнение column/constraint SQL tokens
с actual clean V1.4/текущей V1.5 выявило только ранее утверждённые варианты
mini_players и mini_player_heroes (ALTER order/отсутствующие legacy CHECK).
Все index names/unique/origin/partial,key order/collation/direction и SQL tokens
сопоставлены clean-source профилям; CID меняется только вместе с reviewed table order.
Это разовый аудит входа. Runtime SQL/token normalization не используется.

Clean schemas заново созданы реальными init_db/init_mini_db/init_boss_db из Git
d077dd2a...,cef77cc...,8e894735... на временных файлах. Получены105 known tables и
129 конкретных paired variants из reviewed source/owner metadata, не self-declaration.

## Устранённые false-ALLOW

1. _table_signature сохранена без изменения реализации. Дополнительный exact gate
   теперь обязателен для КАЖДОЙ known table, не только исторических9.
2. _exact_table_contract включает буквальный CREATE TABLE SQL и весь index set:
   name,unique,origin,partial,index_info,index_xinfo,буквальный index SQL (null для
   автоматически созданных индексов). Extra/missing/changed обычный,UNIQUE,partial,
   expression index меняет contract. CHECK/DEFAULT/PK/FK/table options сохраняются.
3. index_list.seq не хешируется: это enumeration/creation order, не порядок ключей.
   Term order в index_info/xinfo, CID,name,DESC,collation,key flags включены полностью.
   Имена и все constraints/SQL не нормализуются.
4. Количество ключей exact inventory должно совпадать со всем reviewed table set;
   неполный registry не отключает проверку «неуказанной» таблицы.
5. Присутствующие trigger/view по-прежнему должны иметь reviewed hash. Дополнительно
   обязательные objects проверяются на НАЛИЧИЕ по module/table profile:
   dnd_items+dnd_currencies →currency_category_insert/update/keep_category;
   inventory+dnd_items →inventory_item_scope_insert/update;
   V1.5 Boss timeline/actions/events →соответствующие два timeline triggers.
   В legacy без timeline они не требуются; в Mini-only без D&D не требуются D&D5.
   Неизвестные objects продолжают блокироваться, auto-heal нет.

Все105 known tables имеют exact DDL gate, включая таблицы без CHECK. Поэтому
добавленный CHECK также неизвестный contract; пустые таблицы не маскируют изменение.
Unknown SQL/format/permutation не добавляются автоматически в whitelist.

## Данные и read-only

Сохранены прежние integrity/FK checks,capability pin,Git SHA/tree binding,protection
от Git replacements,current HEAD binding,all V1.5 state/ownership/ledger/PvP/
sending-uncertain blockers. Source DB открывается mode=ro,query_only=ON,одна read
transaction,WAL-aware;guard не запускает init/migrate/repair/финансовые операции.

Утверждённые shards/stars checks сохранены. Добавлены readonly invariants whole Mini
item quantities и copies:NULL/noninteger/negative →BLOCKED_INVALID_MINI_INVENTORY
(либо ранний INTEGRITY). copies=0 не объявляется новым игровым правилом и не чинится.
Обычный D&D retained quantity representation не получает этого Mini-only ограничения.
Все currencies/rewards/prices unchanged. Никаких int-cast repairs/clamp/удаления строк.

Владелец ранее подтвердил58→83 hero SQL rows:79 catalog codes +4 historical unused
records;их не удалять и не требовать catalog-row exact equality. Ownership по
target registry проверяется отдельно,как прежде.

## Целевые tests

test_complete_release_schema содержит addition/removal/change всех index classes,
каждый missing trigger,changed/extra trigger,CHECK weaken/remove/add,
Mini-only/legacy profiles,owner metadata replay,invalid inventory/copies,
integrity/FK,read-only DB/WAL повторные открытия и actual guard CLI с тремя targets.
Каждое declared index удаляется отдельно;каждая CHECK-table получает mutation
первого CHECK при валидных/пустых данных. Дополнительные cases проверяют negative
inventory и NULL corruption без изменения metadata после corruption.
Существующие historical/ownership/functional/atomic/PTY regressions не удалены.

Поэтапно:только affected tests,затем один полный связанный integration прогон.
После фиксации final SHA —full unittest/validators/check_bot/compileall/diff/Linux
Python3.11/3.13 CI на нём. Старый CI не переименовывается в новый PASS.
Полный локальный Linux suite допускается подтвердить exact-SHA CI,без дублирования.

## Независимый owner preflight

На финальном SHA повторить read-only guard на тех же согласованных offline копиях:
V1.4 backup /opt/shpakdnd-backups/pre-v15-20261028-193144-661948.sqlite3,
V1.5 /opt/shpakdnd-backups/v15-migration-check-vcp2aos0/v15-migration-b5ru_pac/migration.db.
Новый final helper/schema берутся одним reviewed комплектом. Сохранить DB/WAL hashes,
выдавать только aggregate blockers для d077dd2a...,cef77cc...,final target.

Подробный шаблон owner command находится в HISTORICAL_SQLITE_CONTRACTS.md; его
migrated путь в этой итерации уточнён выше. Финальный отчёт содержит подготовленную
команду с exact final SHA и правильным путём. Не читать live production вместо copy.
Ранее raw6 snapshots verdict были BLOCKED_PVP_UNRESOLVED при pause_new_required;
только in-memory pause simulation давала6 PASS. Новая schema защита не обходит
эту maintenance gate. Не ставить paused SQL UPDATE в backup ради реального PASS.

При новом BLOCKED не удалять indexes/triggers/rows ради успеха и не запускать restore.
Сохранить metadata/текущую БД/WAL и разобрать конкретное отличие. Guard не является
разрешением merge/deployment;main/TEST/production не меняются.

## Frozen bundle / rollback / оставшиеся gates

Установка только полного нового frozen deploy bundle одного final SHA через штатный
installer,с общим lock и сохранением retained bundle. Не копировать один guard/schema
поверх активной версии. Installer rollback (--activate-existing reviewed retained SHA)
меняет только entrypoint,а не app/DB/systemd. Старый bundle не использовать как обход.

Штатный dry-run/preflight связан с origin/main. Пока main=d077...,он не проверяет
кандидата ветки;ref не подменять. Будущий изменившийся release SHA требует своего CI.
Ни установку,ни deployment/rollback/restart данным документом не разрешаем.

Остаточные gates:independent ChatGPT review,owner exact-SHA offline data check,
реальная pause-new и актуальный economics/delivery preflight,отдельное решение
владельца по релизу. INTEGER overflow работающего app НЕ исправлен: guard обнаруживает
испорченное состояние,но не предотвращает будущий SQL increment. Самостоятельное ТЗ
INTEGER_OVERFLOW_FOLLOWUP_TZ.md подготовлено для отдельного согласования.
