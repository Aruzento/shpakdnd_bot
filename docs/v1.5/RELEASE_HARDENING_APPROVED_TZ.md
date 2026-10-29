# CODEX — D&D Mini V1.5: устранение замечаний независимого аудита и подготовка безопасного релиза

**Дата ТЗ:** 26.10.2026
**Репозиторий:** `Aruzento/shpakdnd_bot`
**Рабочая ветка:** существующая `codex/v1.5`
**PR:** [#8, Draft](https://github.com/Aruzento/shpakdnd_bot/pull/8)
**Последний независимо проверенный HEAD на момент составления ТЗ:** `cef77cc87f6a2286284f5cf8fdb851c775b04dd1`
**Base PR на момент составления:** `d077dd2a2c29ad420b138bf07af23c77f4674d0b`
**Исходный аудит:** `DND_Mini_V1.5_Independent_Audit_2026-10-26.md` (приложить к задаче).
**Режим работы:** исправление релизных и эксплуатационных рисков, а не новая игровая версия.

> **Запреты:** НЕ объединять PR; НЕ изменять и НЕ пушить `main`; НЕ выполнять `sudo deploy-shpakdnd`, staging/production deploy, live rollback и перезапуск systemd; НЕ взаимодействовать с действующей пользовательской SQLite и внешним рабочим `STORIES_DIR`; НЕ использовать реальный Telegram token, polling, реальных игроков и реальные финансовые операции; НЕ изменять авторские JSON/PNG, боевые формулы, вероятности, награды, игровые лимиты, механики Mythic, Raid, Story V2, PvP, Tower или гачи ради прохождения тестов; НЕ удалять/пропускать/ослаблять тесты; НЕ делать `force-push`/`reset --hard`. Любые новые миграции — только при доказанной необходимости и после отдельного согласования.

## 0. Входная проверка, инвентаризация, границы

1. Перед изменениями проверить `git status`, текущую ветку, `HEAD`, upstream, актуальный `origin/codex/v1.5`, `origin/main`, PR #8 и все новые коммиты после указанного SHA. Не перезаписывать чужие dirty/untracked данные.
2. Прочитать фактические файлы: `deploy/deploy-shpakdnd.sh`, `deploy/deploy_helpers.py`, `deploy/sqlite-deploy.py`, `deploy/pvp_ops.py`, `deploy/shpakdnd-bot-watch.path`, `scripts/run_isolated_checks.py`, `tools/check_v15.py`, `tools/validate_deploy_bundle.py`, `tools/validate_v15_migration.py`, `tools/validate_story_content.py`, `tools/validate_story_candidate.py`, `app/stories/{catalog,service,schema,publication}.py`, `app/mini/mythic/{schema,pets}.py`, `app/mini/mythic/raid/service.py`, `app/mini/mythic/schema.py`, `docs/v1.5/{COMPLETION_AUDIT,TEST_MATRIX,RELEASE_AND_ROLLBACK,STAGING_ACCEPTANCE,STORIES_IMPORT_GUIDE,IMPLEMENTATION_STATUS,DATABASE}.md`; все существующие deploy/PTY, compatibility и V1.5 тесты.
3. Составить точную таблицу рисков и затрагиваемых файлов **до внесения правок**. Документировать подтверждённую старую семантику; не заменять точные механики предположениями.
4. Сохранить существующий защищённый порядок интерактивного деплоя, fail-closed при ошибках, manual confirmation, запрет на автоматическое восстановление старой БД и отдельное управление watcher/systemd.
5. **Не выполнять никакую реальную выкладку этой задачей.** Здесь допускаются только код, документация и офлайн-проверки на disposable Linux/Windows SQLite и имитированных systemd/git/TTY.

## 1. P1: вынести длительные проверки ДО остановки production-бота

**Доказанная проблема:** в текущем `deploy/deploy-shpakdnd.sh` после `stop_stack()` вызывается `checks()`, а внутри — полный `python -m unittest discover -s tests -q`. На финальном V1.5 Linux CI он занимает порядка 11–12 минут. Такая последовательность делает простой зависимым от длительности тестов.

### Необходимое поведение

1. Построить **двухфазный** процесс: `preflight` без остановки рабочего бота и `maintenance/cutover` с короткими постпроверками. Не считать «зелёный CI» достаточным вместо полной офлайн-проверки текущего **точного TARGET_SHA** перед остановкой.
2. Сначала, под действующим deploy lock, выполнить `git fetch origin`, зафиксировать immutable `TARGET_SHA` из `origin/main` и `OLD_SHA`, сверить чистоту production worktree, git upstream, наличие необходимых инструментов, запуск systemd/watcher и права; отдельно проверить, что PR не развёртывается напрямую (production по-прежнему получает только `origin/main`). `--dry-run` не должен останавливать процессы или писать в БД.
3. До `stop_stack()` создать **изолированный checkout проверяемого target SHA вне monitored project/watcher** (например, detached `git worktree` в отдельном защищённом tmp; выбрать безопасный механизм с учётом существующих тестов, использующих `git show`). Не переключать live checkout ради тестов. Предусмотреть безопасную очистку tmp/worktree и журналирование без удаления чужих путей. Проверить чистоту и точный SHA этого checkout.
4. В изолированном checkout запускать полный `unittest`, `check_bot.py`, `compileall`, все обязательные валидаторы Hero/Boss/Tower/Editorial/Mythic/Story/PvP/D&D/shop/deploy, проверки migration/SQLite/compatibility, `git diff --check` и необходимые regression PTY. Переиспользовать штатный `scripts/run_isolated_checks.py` или эквивалентное доказуемое изолирование, не допуская обращения к рабочим SQLite, `STORIES_DIR` и реальному Telegram API/token. Положительные тесты не должны менять реальный `.env` и серверные файлы. В отчёте хранить команду, exit code, время, SHA, версию Python, пути изолированной среды; никаких секретов.
5. При любой ошибке preflight: немедленно fail-closed **без остановки рабочего бота**, без изменения live HEAD, без backup-restore и без запуска update service. Вывести конкретный неуспешный gate.
6. Перед остановкой **повторно сопоставить** протестированный `TARGET_SHA` с подтверждённым target для установки и `origin/main`, а также состояние live HEAD/worktree. Не использовать отчёт от другого коммита даже если ветка называется так же.
7. После явного пользовательского подтверждения: `stop_stack` → консистентный финальный SQLite backup с проверкой целостности и идентификатором/хешем → checkout **ровно проверенного** `TARGET_SHA` → короткие критические startup/DB/content/HEAD/smoke-gates → явное подтверждение запуска → `start_stack` + проверка сервиса и watcher + журналов. Не делать дорогостоящий полный unittest после остановки (его проверенный отчёт уже привязан к target SHA). Предусмотреть отдельный **онлайн-снимок read-only SQLite** для предварительной проверки миграции, если нужен, но он не заменяет финальный backup после остановки.
8. Не оставлять запущенной непроверенную версию, если любое короткое post-cutover условие не прошло. Сохранить fail-closed поведение и действия оператора при сбое. Ни под каким видом не восстанавливать старую БД поверх новых транзакций автоматически.
9. Сохранить/улучшить `--dry-run` так, чтобы оператор мог увидеть точный план/gates без изменений состояний systemd, кодовой ветки, live SQLite или внешнего Stories. Если нужен режим `--preflight-only`, сделать его безопасным, согласованным с текущей интерактивностью и явно документировать.
10. Изменения `deploy-shpakdnd.sh`, его защищённого helper bundle и раннего `git fetch` обязательно проверить на Linux. Не считывать команды/подтверждения из случайного stdin, сохранить нынешнюю защиту TTY, EOF, CRLF, логов, watcher и systemd.

### Приёмочные тесты

- Полная проверка работает на target SHA, пока фиктивный bot service в статусе `active`.
- Длительная/упавшая проверка, отсутствующий Story, неправильный SHA, недостаток места или плохая disposable SQLite **не приводят к вызову stop** и не меняют служебные состояния.
- Сценарий успешного preflight приводит к остановке только после подтверждения оператора и только один раз.
- Несовпадение SHA до cutover блокирует переключение.
- Сохранены обработка Ctrl+C, EOF, CRLF, повторного подтверждения, log redaction, lock и отказ при грязной live working tree.
- Реальная production SQLite остаётся недоступна тестовой среде; доказательство — sentinel/mock/перехват открытия файлов и/или отдельные PATH/environment тесты, а не утверждение Codex.
- Сбой после cutover не обходит rollback guard и не запускает bot/watcher самовольно.

## 2. P1: запретить небезопасный rollback на старый код

**Доказанная проблема:** текущий `rollback_preflight()` проверяет PvP через `pvp_ops.py`, но **не** гарантирует поддержку старым кодом активных Mythic Raid, Stories V2, питомцев и новой экономики. Успешный `old-code startup` на синтетической БД — только schema compatibility, НЕ безопасный functional rollback.

### Реализация

1. Добавить **read-only preflight guard** для отката (в существующий защищённый frozen helper bundle или отдельный проверенный helper, не зависящий от файла, исчезающего после checkout). Работать с SQLite read-only (`mode=ro`/`PRAGMA query_only`), проверять, что путь/DB относится к разрешённому проекту; ничего не изменять.
2. Составить полную матрицу сохраняющихся V1.5 состояний на реальной схеме: `mini_mythic_raids` active/unfinished; `story_sessions` active/registration (V2 различать по snapshot и известной внешней истории, при неясности — fail closed); `story_publications` pending/sending/uncertain; `mini_mythic_pets` active и незавершённые часы/evolution_pending; `mini_mythic_ledger`, `mini_mythic_operations`, баланс универсальных осколков, Star rewards и другие уже зафиксированные V1.5 операции; PvP escrow/cancel; незавершённые normal boss/Tower операции, зависящие от V1.5 policy snapshot. Конкретный набор опасных статусов вывести из schema/service и задокументировать.
3. Перед предложением вернуть старый HEAD проверять возможности **целевого rollback SHA**, а не только текущей схемы. Старый код может читать таблицы, но не обслуживать V2 и новые Raid snapshots. Отсутствие активных рейдов недостаточно для автоматического вывода о безопасном downgrade экономики/питомцев. Для неизвестного target или неоднозначного состояния — BLOCKED, явное описание причины и дальнейших ручных шагов.
4. Для несовместимого target при наличии любой активной/неразрешённой операции **запретить исполняемый автоматизированный rollback**. Безопасные действия: сохранить текущий SHA и backup, оставить систему остановленной/не запускать старую несовместимую версию, предоставить оператору список причин и документированный ручной план. Никакого `--force` для обхода гарантий по умолчанию.
5. Ручной drain возможен только по отдельному решению владельца: запрет новых сессий, завершение или согласованная отмена текущим совместимым кодом, повторная проверка и сохранение snapshot/ledger/steps/votes/publications/JSON. Даже после drain возврат может требовать совместимого промежуточного кода; это не право автоматически восстанавливать старый Git HEAD.
6. Проверки должны охватывать **и плановый rollback**, и интерактивный путь `[2] Вернуть предыдущий Git HEAD` после проваленного деплоя. Не давать откатиться на старую версию, игнорируя guard, через более короткую аварийную ветку.
7. Логи/отчёт: target/current SHA, результат PvP и V1.5 checks, агрегированные counts по типам незавершённых операций, код причины `BLOCKED_*`, backup path/checksum без персональных данных и секретов.

### Обязательные тесты

- V1.5 активный Raid, завершённый Raid, Story V1, Story V2 с активным голосованием, V2 registration, pending/sending/uncertain публикация, активный/резервный питомец, ожидающая эволюция, ненулевой ledger, пользователь с Mythic, PvP escrow, незавершённый Tower/Boss snapshot, неопознанная миграция/статус.
- Проверка с пустыми/отсутствующими таблицами для исторических старых SQLite: **не** падать непонятным исключением, корректно определить capability и fail-closed при неопределённости.
- Ни один guard не пишет в БД, не меняет состояние бота или watcher, не запускает старый executable.
- Даже при успешном старом startup guard не маркирует активные V2/Raid как `SAFE`.
- Полный отдельный PTY integration-test существующего interactive deployment/failure-choice поведения.

## 3. P1: обязательный `strict-content` gate для целевой внешней Истории

**Доказанная проблема:** нынешний CI успешно проверяет Story JSON из `content/stories/unpublished` только после копирования во временный каталог. Рабочий серверный `STORIES_DIR` пока не заполнен; стандартный `strict-content` на пустом каталоге честно даёт `BLOCKED_BY_INPUT`/exit 2. В production скрипте нет отдельно доказанного обязательного строгого gate на будущий **целевой внешний каталог**.

### Реализация

1. Определить явный release preflight для **целевого** `STORIES_DIR` и всех четырёх Raid/8 Mythic/17 S5/45 PNG. Вызвать текущие `tools/validate_story_content.py` и `python -m app.mini.mythic.content --strict-content` со значениями каталога **того окружения, для которого готовится релиз**, без записи в каталог и без доступов к рабочей DB. Если требуется, сделать защищённый read-only CLI gate с явным `--directory` вместо неявной среды.
2. Подтверждать `STORIES_DIR` как существующий абсолютный внешний каталог, не внутри checkout, не в Git/watcher, не symlink на защищённые/чужие пути; наличие и читаемость единственного необходимого авторского Story JSON `tayna_propavshey_ovtsy.json` (дополнительные авторские истории валидировать тоже, не придумывать требования «ровно одна история»). Не смешивать Story payload с `MANIFEST.json`, `GRAPH_AUDIT.json`, Prompt или прочими JSON несценарного типа.
3. Для V1.5 проверять **реальный** авторский код `tayna_propavshey_ovtsy`, schema 2, policy `once`, 53 узла, 113 выборов, 7 концовок, все ссылки и содержимое из утверждённого manifest/hash; дополнительно обычную Story `discover()` и отсутствие ошибок во всех JSON директории. Наличие файла в Git/CI-пакете НЕ считается его наличием в целевом внешнем каталоге.
4. В предусловиях preflight: пустой каталог → `BLOCKED_BY_INPUT`/exit 2 и никакой остановки рабочего сервиса; malformed/conflicting/corrupt/symlink → invalid/exit 1 и никакой остановки; полный валидный — PASS/0. Отчёт различает `CODE_CONTENT_PASS`, `TARGET_STORIES_DIR_READY`, `STRICT_CONTENT_TARGET_PASS`, а не маскирует блокер успешным offline-CI.
5. **НЕ копировать Story на production** в этой задаче. Подготовить отдельную инструкцию для владельца по явно разрешённому переносу только одного настоящего JSON в отдельную external staging-директорию, проверке checksum и затем, позднее, отдельной публикации на production. Опасные действия не должны происходить автоматически в `git checkout` или `deploy`.
6. Поддержать read-only проверки в mock staging/production configuration без фактического подключения к production. Существующий изолированный CI-story gate сохранить как независимое evidence.

### Обязательные тесты

- Отсутствующий каталог/файл, валидный V2, некорректный JSON, дубликаты code, изменённый approved hash, ссылка вне каталога, symlink, пустой/неполный контент, дополнительных валидных Story JSON, неожиданная версия схемы.
- Проверка не создаёт и не изменяет файлов в target `STORIES_DIR` и не запускает Telegram.
- `strict-content` на полном sandbox: 0; на пустом: 2; на испорченном: 1. При любом non-zero работающий фиктивный бот продолжает работать.

## 4. P2: согласовать документацию, матрицу и финальные статусы

1. `docs/v1.5/COMPLETION_AUDIT.md` устарел: O03 и O09 в исходной исторической матрице помечены `BLOCKED_BY_INPUT` и описывают отсутствие реальных боссов и авторского контента, которых уже есть в репозитории. **Не переписывать историю задним числом**: пометить историческую таблицу как «срез до импорта» и добавить ясный раздел/ссылку на актуальный срез итогового SHA, где 79 героев (71+8), 4 Raid bosses, 45 PNG и подготовленная, но ещё не опубликованная Story V2. Дополнить точной датой и SHA, не приписывать staging PASS.
2. В `docs/v1.5/TEST_MATRIX.md`, `CONTENT_IMPORT_CHECKLIST.md`, `IMPLEMENTATION_STATUS.md`, `RELEASE_AND_ROLLBACK.md`, `STAGING_ACCEPTANCE.md` привести статусы O03/O09 к единому содержательному состоянию: `REAL_CONTENT_IN_REPO=PASS`, `STORY_EXTERNAL_TARGET=NOT_PUBLISHED`, `STRICT_SANDBOX=PASS`, `STRICT_PRODUCTION=NOT_CHECKED/BLOCKED`, `PROD_DB_COPY=NOT_RUN`, `STAGING=NOT_RUN`. Не менять утверждённые O01–O10/R01–R20 решения.
3. Добавить единую релизную сводку с колонками «функциональность / код / unit+integration / CI финального SHA / реальная БД / Telegram staging / откат / release verdict». Любая строка без доказательств помечается `NOT_VERIFIED`, а не PASS.
4. В пользовательских и операторских пояснениях явным образом раскрыть: первая подтверждённая counter-атака активирует эффект даже при actual HP damage=0; Moon raid аркана выбирается до расчёта урона; `uncertain` Telegram публикации требуют ручной сверки.

## 5. P2: Лунный Mythic — отделить ошибку текста от изменения механики

**Согласованный движок:** рейдовая аркана выбирается при первой подтверждённой атаке Moon **до** вычисления фактического HP-урона; возможно actual=0. Сохранение результата и повторная операция не должны повторно кидать RNG. Текущий авторский `passive_text` может восприниматься как «только если нанёс урон».

- Подготовить **предлагаемую** новую формулировку UI-описания в отдельном review-документе и показать точный `before/after`; например: «При первом подтверждённом ударе в рейде выбирает и закрепляет лунную аркану. Аркана активируется даже при нулевом фактическом уроне; повторные удары её не перебрасывают». Уточнить, что это пояснение, не новая способность.
- **Не менять утверждённый авторский `mythic.json` и игровой код до отдельного согласования текста владельцем.** Если можно вывести дополнительное пояснение в независимом help/FAQ без изменения авторской карточки, предложить как отдельный вариант и также запросить согласование формулировки.
- Убедиться, что тесты фиксируют фактический raid Moon snapshot с нулевым урона и no-reroll. Никаких изменений формул или выпадения арканы.

## 6. P3: операционный протокол Telegram `uncertain`

- Документировать модель `pending → sending → sent/uncertain`, потому что SQLite и Telegram API не образуют одной транзакции.
- Для каждой публикации при `uncertain`: сохранить session/raid ID, step, timestamp, chat/thread, тип публикации и известный `message_id`; ручная сверка факта отправки, независимый безопасный способ завершить без повторной выплаты/повторной сцены. Не включать реальный chat/user PII в публичные логи.
- Не разрешать автоматическую слепую повторную отправку `sending/uncertain`; не пытаться реализовать «exactly once Telegram API». Любое отдельное изменение схемы публикаций вынести в новое согласованное ТЗ.

## 7. Тестирование и совместимость — ОБЯЗАТЕЛЬНАЯ часть реализации

Новая логика deploy/rollback/content должна быть полноценно покрыта unit, integration, Linux PTY/systemd-mock и regression тестами. Специально проверять **неуспешные сценарии**, иначе цель hardening не достигнута.

### 7.1. Unit/integration

- Safe staged checkout, точный SHA, invalid SHA, смена origin/main/HEAD между preflight и stop, race второго deploy, грязное live дерево, cleanup temp.
- Staging content full/missing/invalid/duplicate/symlink/hash mismatch, strict exit 0/1/2, отсутствие writes.
- Rollback V1.5 matrix: active raid/story V2, питомцы/выплаты/legacy, pending/uncertain publications, PvP escrow; несовместимый target, несуществующие таблицы, corrupt DB; запрет автоматического запуска старого кода.
- Повторные запуски, reboot/restart, недоступный disk/permissions, EOF/Ctrl+C/CRLF, Telegram notice error, watcher/update service states, post-cutover failure.
- Убедиться, что обычная V1.5 функциональность НЕ меняется: 79 heroes, 4 raid bosses, 45 PNG hashes, 53 Story nodes/113 edges/7 endings, O01–O10/R01–R20 economics and timers, 17 S5, gacha exclusion, pet reserve/upgrade, Tower, PvP/D&D.
- Проверить отсутствие перезапуска сервера, утечки токенов, изменений реальной DB и реальной внешней Story-директории во всех тестах.

### 7.2. Полный набор и системные проверки на финальном SHA

В Linux, соответствующем серверному окружению, и в Windows (где применимо):

```bash
python -m unittest discover -s tests -q
python check_bot.py
python -m compileall -q bot.py app
python scripts/run_isolated_checks.py --label v15-validators -- tools/check_v15.py
python scripts/run_isolated_checks.py --label v15-story-candidate -- tools/validate_story_candidate.py
# Использовать предусмотренные команды существующих валидаторов, D&D shop/CSV/coins/loot/PvP/PTY/migration.
git diff --check
git diff --check origin/main...HEAD
```

Соблюдать текущую изоляцию `scripts/run_isolated_checks.py` для всех команд, которые могут открыть SQLite, API, `.env` или STORIES_DIR; приведённые простые вызовы запускать только после подтверждения их безопасного окружения. Дополнительно запустить **все** новые release hardening тесты, а не только happy-path. Не удалять и не пропускать тесты и не менять правила ради их прохождения. Не объявлять успех по частичному прогону. Проверить Linux CI на Python 3.11 и 3.13 **после финального commit/push**; все 12 check_v15 validators, migrations, PTY, Story строгие проверки, без новых skips. Проверить конечную чистоту дерева и точный SHA.

### 7.3. Задокументировать фактические пределы проверки

- Синтетическая SQLite ≠ офлайн-копия production SQLite.
- Fake/PTY/systemd mocks ≠ реальный Telegram staging.
- CI success ≠ работа в production.
- Strict Story PASS во временном каталоге ≠ файл опубликован в live STORIES_DIR.

## 8. Развёртывание и откат — только **план**, ничего не выполнять

1. Подготовить отдельную последовательность: независимый финальный audit → разрешённая offline production DB copy + immutable SHA/checksum → read-only preflight на копии (migration3x/integrity/FK/ownership/wallet/legacy/active sessions) → **отдельное разрешение на staging** → staging real Telegram (2 players+admin, bosses/pets/tower/Story all 7 endings/crash) → повторный финальный SHA/CI → отдельное разрешение merge → `main` → отдельное разрешение production deploy.
2. Production-кандидат — строго `origin/main`, а не прямой deploy из PR. Штатный deploy `sudo deploy-shpakdnd` с подтверждением, резервным копированием, контролем watcher/systemd и логов, после явного решения пользователя.
3. Подготовить rollback decision tree: совместимый код / несовместимый код; active V1.5 states / drained; реальный backup + forward ledger; fail-closed; manual recovery. Никакого live rollback во время реализации этого ТЗ.

## 9. Что считать готовой работой

**Техническое исправление принимается, только если одновременно:**

- Полный unittest + check_bot + compileall + валидаторы + Linux CI на новом финальном SHA зелёные, zero unexplained failures/skips.
- Обязательные тесты доказали, что preflight завершается **до stop**, а при ошибке сервис продолжает работать.
- Старый код нельзя запустить через опасный rollback, пока его совместимость с активными V1.5 состояниями не подтверждена.
- Контентный gate читает именно внешний target STORIES_DIR и не путает непубликованный Story JSON с готовым production; на пустом каталоге блокирует релиз до остановки.
- Документация перестала утверждать, что реальные 4 босса и Mythic отсутствуют, и честно показывает NOT_RUN для production DB и staging.
- Авторский `mythic.json`, Story JSON, 45 PNG и все игровые правила побайтово/семантически не изменены; текст Moon предложен на утверждение отдельно.
- Рабочая SQLite/серверы/`main`/настоящий STORIES_DIR не затронуты.

**Итоговый статус после Codex:** максимум «В разработке» либо «Готово к тестовому стенду» **только после независимой повторной проверки ChatGPT** полного нового кода и обязательных автоматических проверок. Кодекс НЕ вправе выдавать «Готово к объединению с main» или «Можно в прод» без offline production DB copy, настоящего staging и отдельного утверждения владельца.

## 10. Формат финального отчёта Codex

Предоставить в PR и в ответе:

1. Текущие branch/PR/base, точный финальный SHA; подтверждение отсутствия изменений `main`/live.
2. **Каждый риск P1/P2/P3:** первопричина, точные изменённые файлы/строки, реализованная защита, новые тесты и отрицательный сценарий, остаточный риск.
3. Схема preflight с доказательством порядка действий **до stop**, run IDs, целевой SHA и прогоны с intentional failures.
4. Rollback capability matrix: какие состояния блокируются, какие проверяются, в каких случаях обязательна ручная процедура. Фактический тест `mode=ro`.
5. Content gate: результаты sandbox PASS0, missing exit2, invalid exit1; отдельная отметка «production STORIES_DIR NOT CHECKED/NOT PUBLISHED».
6. Полный список изменённых файлов; отсутствие правок в механике, 8+4+17, Story/PNG hashes.
7. Full test counts/skips/results, все валидаторы, SQLite integrity/FK, PTY, Linux 3.11/3.13 CI ссылки и точный SHA после **последнего** изменения.
8. Точные оставшиеся риски, отдельно NOT_RUN production DB copy / real Telegram staging / live deployment.
9. Обновлённый `RELEASE_AND_ROLLBACK.md` и конкретные команды **только для будущего отдельного разрешённого запуска**, не выполнять их сейчас.
10. Вердикт: «В разработке / Релиз заблокирован», пока не выполнены обязательные внешние проверки.
