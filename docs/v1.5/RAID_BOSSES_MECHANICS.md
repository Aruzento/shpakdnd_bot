# Четыре механики Mythic Raid — реализация и контракт

Изменения после ручного TEST: [TEST_STAND_FIXES.md](TEST_STAND_FIXES.md). Лимит рейдов теперь 5 за московские сутки; дуэли/PvP временно выключены. План обновления с сохранением прогресса: [TEST_CANDIDATE_SWITCH.md](TEST_CANDIDATE_SWITCH.md).

Исходный HEAD проверен перед изменениями: f0b9c1cd1f789e6b0268b756780108bf3e5663a1.
Ветка codex/v1.5, тот же draft PR #8; base/origin/main d077dd2a2c29ad420b138bf07af23c77f4674d0b.
Утверждённый источник полностью сохранён в RAID_BOSSES_MECHANICS_APPROVED_TZ.md.
Статус «В разработке»: реальные Boss records, восемь Mythic records и внешние
Stories не создаются автоматически. Main, рабочая SQLite и deployment не менялись.

## Профили и границы

| № / mechanic.type | Исходный HP / стихия / контрстихия | Frozen features | Работа |
|---|---|---|---|
| 1 / armor_cycle | 100000 / fire / ice | armored | Атаки 1–10 с бронёй, 11–15 без; затем повтор 10+5. Переключение после 10/15 действует со следующей атаки. |
| 2 / flight_cycle | 115000 / lightning / star | flying | Атаки 1–8 в воздухе, 9–10 на земле; затем повтор 8+2. Нулевые подтверждённые атаки продвигают посадку. |
| 3 / periodic_heal | 130000 / dark / sun | [] | После каждой 15-й попытки floor(originalHP*25/10000)=325 HP, clamp до max; на fatal hit лечения нет. |
| 4 / hp_defense_phases | 150000 / star / moon | armored,flying | HP>112500:оба; (75000,112500]:броня; (37500,75000]:полёт; ≤37500:без тегов. |

Для всех четырёх regeneration_percent=0. Профиль, max_hp, одна counter_elements,
начальные features и mechanic-параметры проверяются вместе. Свободный положительный
параметр вне утверждённого набора не является новым разрешённым балансом.
Старые записи без mechanic по-прежнему допустимы и продолжают статический pipeline.

## JSON

Поле mechanic — строгий объект одного из четырёх типов. Поля и значения — ровно
из раздела II утверждённого ТЗ и константы mechanics.SPECS:

- armor_cycle: armored_attacks=10, exposed_attacks=5;
- flight_cycle: flying_attacks=8, grounded_attacks=2;
- periodic_heal: every_attacks=15, heal_bps_of_original_hp=25, sun_suppresses=true;
- hp_defense_phases: threshold_percent=[75,50,25], phase_features=[[armored,flying],[armored],[flying],[]], threshold_base=original_max_hp, monotonic=true.

Непустой bosses-массив по-прежнему содержит ровно четыре записи. Новый комплект содержит все четыре разных mechanic.type и четыре разных image; смешанный новый/legacy или дублированный профиль отклоняется. Unknown/missing
поля/типы, bool вместо int, нулевые/отрицательные периоды, неверные пороги, дубли
тегов и JSON-ключей, несуществующие Shadow-цели, неверный PNG отклоняются. Неизвестные
способности не исполняются. PNG проверяется отдельно; формат/CRC/квадрат обязателен.

## Порядок атаки и SQLite

1. В BEGIN IMMEDIATE проверяются игрок/мир, operation payload/replay, активность,
   московский день, лимит 10 и герой один раз в сутки на данный рейд, доступность.
2. prepare фиксирует effective_features и фазу на начало атаки, до Eclipse и HP-events.
3. O02 применяет matching-контрстихию до защиты и HP-damage. Первая matching-атака
   получает 100%, но не обходит flying; actual=0 всё равно активирует новый counter.
4. effects.resolve использует эффективные теги для reachability и armor/Sun bypass.
   Все дополнительные HP-events относятся к той же попытке; счётчик не увеличивают.
5. Завершающее лечение использует incoming Sun charge, сохранённый до расхода и
   выдачи новых баффов. Последний incoming заряд подавляет лечение; Sun, полученный
   только этим ударом, не подавляет его ретроактивно. Лечение не переносится на ход 16.
6. finish увеличивает confirmed_attacks ровно на один. Циклы переключаются для следующей
   атаки. HP-phase учитывает минимальный достигнутый HP всех событий и сохранённую фазу;
   лечение и Eclipse не возвращают защиту. Переход через несколько порогов — одно событие.
7. Damage ledger, state/HP/revision, attack receipt, яйца, audit и финальная награда
   сохраняются в той же транзакции. Любая ошибка audit/damage/attack/reward INSERT
   откатывает всё. Повтор возвращает сохранённый result_json; фазы не пересчитываются.

snapshot_json хранит неизменяемый validated mechanic и исходные features. Новые
start() сохраняют state.mechanic={version:1,type,confirmed_attacks:0,phase,effective_features}.
Динамические last_heal/last_transition ограничены последним событием; история находится
в mini_mythic_raid_audit и receipts атак. phase=1/2 для циклов, 1…4 для HP, null для heal.
Счётчик и HP проверяются на SQLite int64; умножения/пороги используют Python integer.
Схема таблиц не меняется: используются существующие state_json/snapshot_json/result_json.

Аудит: raid_mechanic_transition с phase/features before/after, attack_number, HP и
crossed_thresholds; raid_periodic_heal с requested/healed, Sun suppression, fatal flag.
Ключ raid-mechanic:<raid_id>:<confirmed_attack_number>:<event_index> уникален.
Счётчик каждой попытки дополнительно закреплён в UNIQUE attack ledger и revision.
Повтор start() проверяет сохранённую операцию до чтения live-каталога, поэтому hot-edit
не ломает восстановление уже начатой операции.

## Экономика и совместимость

Положительный actual HP damage записывается по actor_id каждого события (включая
отдельную атрибуцию Earth). Лечение не является отрицательным уроном и не уменьшает
вклад. Фонды R09 — 20/23/26/30 от original HP; O01 использует тот же original M и
сохранённый D, с насыщением min(D,M). За смену фазы/лечение отдельных наград нет.
Рейд завершается либо обычной победой HP=0, либо административной отменой; запись
под write-lock исключает двойную финализацию. Daily attempts и старые counter policies
сохраняются. Старые snapshots без mechanic не получают state/result-полей механики.
Новый код не переписывает старые бои, balances, fragments, normal/Boss/Tower/PvP/Stories.

Запуск старого кода на отдельной синтетической копии проверяет структуру/данные, а не
разрешает откат активных рейдов с новой механикой: старый engine её не исполняет.
Deployment/реальный rollback не выполнялись.

## Telegram и изображения

Карточка показывает название механики, число подтверждённых атак, действующие теги,
остаток атак до переключения/лечения либо фазу и следующий исходный HP-порог.
Receipt сообщает разрушение/возврат брони, посадку/взлёт, лечение/подавление Sun.
Фото caption ≤1024 UTF-16, public text ≤3500, личный ответ ≤4000. Publication
pending/sending/sent/uncertain и replay-контракт сохранены.

Реальные четыре вложения владельца сохранены как images/raid_boss_1.png…4.png;
соответствие исходникам и порядку записано в images/README.md. JPG→PNG — перекодирование
того же изображения без генерации или изменения размера, все 1280×1280. Реальных
code/name/описаний/Shadow-targets/Boss records в этой задаче не создано; bosses.json пуст.
S5 не импортирован. Известный master_puppeteer.png содержал JPEG: исправлена только
кодировка файла на настоящий PNG с сохранением декодированных пикселей и размера.

## Автоматические доказательства

Новые тесты — tests/v1_5/test_raid_mechanics.py: strict schema/profile/duplicate JSON,
все границы циклов, physical/magic/melee/ranged/flying, incoming vs newly acquired Sun,
15/30 heal/clamp/fatal/Eclipse, точные HP±1, множественные события/пороги, monotonic heal,
все восемь Mythic, legacy pipeline, hot-edit/restart, реальная SQLite и ledger, replay,
rollback разных INSERT, concurrent attempts/cancel/fatal, daily caps, R09/O01, migration3x,
полный Telegram→service→SQLite→photo/edit→replay на четырёх временных Boss records/PNG.
Иллюстрации фикстур существуют только в tempfile, production каталоги не подменяются.

Baseline f0b9c1c:138 V1.5 тестов, два падения из-за нового реального photo-path питомцев.
Старый test harness теперь mock-ает Bot.send_photo и по-прежнему запрещает любой HTTP.
Прежние138 после исправления PASS20.199s. Ни один тест не удалён/не skip'нут.

Все проверки через scripts/run_isolated_checks.py: fake token, disposable DB/legacy/Stories.
Linux3.11/3.13 явно проверяет exact HEAD; if:always() после content diagnostics обеспечивает
выполнение всех оставшихся миграций/валидаторов даже при некорректном авторском арте.
Ошибка контента остаётся ошибкой шага/job: continue-on-error и подмена exit не применяются.
Полные counts/skip/exit, точный final SHA и CI-ссылки фиксируются после последнего коммита
в итоговом отчёте/PR. Strict content не считается PASS при отсутствующих records/Stories.

Дополнение после полного Linux-прогона кандидата 93d7fa1: старый V1.4.2 guard
защищал целиком hero_images от любых новых файлов и отклонил прежнюю разрешённую
поставку S5/Mythic. Guard сохранён и уточнён: каждое изменение сравнивается с тем же
историческим SHA 293693d8; допустим только статус A для точных 17 S5 + 24 Mythic путей.
Любое M/D исходных артов/Shadow JSON/баланса и любое постороннее добавление по-прежнему
роняет тест. Тест не удалён/не skip'нут; он продолжает защищать все исходные байты.
Финальный полный прогон повторяется на новом коммите после этого исправления.
