# D&D Mini — V1.4.2

Telegram-бот на aiogram 3 и SQLite. Ветка `codex/v1.4.2`, draft PR #2:
атомарный призыв ×10, гарант 10/25/85, купоны, Фонарь духов, Shadow-каталоги,
раздельные реестры предметов D&D/Mini и точные номиналы валют.
Завершена новая UI-навигация: приватная регистрация кнопкой, коллекция 4/страницу,
трёхэтапный выбор героя в Башне и управление жителями/работниками Деревни.

[Отчёт и ограничения проверки](docs/architecture/V1.4.2-COMPLETION.md),
[матрица 109 экранов](docs/ui/V1.4.2-MATRIX.md),
[механики первой части V1.4.2](docs/RELEASE_V1.4.2.md),
[deployment](docs/deployment.md).
Реализация передаётся на независимое ревью и 47 сценариев стенда;
это не разрешение на merge или production.

## Возможности

D&D Mini: персональные ephemeral-меню, персонаж, daily, магазин, инвентарь и
использование предметов, гача, коллекция, звёзды и продажа осколков,
деревня, дуэли, Events и групповые боссы Combat v2. События доступны из Ярмарки;
сохранены сессии, ставки и защита повторных операций.

Боевые герои фиксируются при старте в immutable loadout; текущий ход,
ability state, события и награды сохраняются в SQLite. Watcher восстанавливает
публичный ход и пропускает просроченные ходы после перезапуска. Фракции,
классовые исключения и 4-часовой таймер сохранены. Новые features и traits
дают броню, досягаемость, регенерацию, resurrection, poison, временный запас
награды и corruption; они сохраняются вместе с боем.

Обычные D&D команды персонажей, инвентаря, бросков и таймеров остаются в `app/handlers/`,
`app/db/` и `app/services/`. Mini использует отдельные таблицы с префиксом `mini_`.

🏰 Испытания: один герой из гачи, видимый противник, 3 щита и награда только за первый clear.
🛡 Экипировка игрока: шлем, кольцо и плащ. Бонус до +300 ATK действует в Tower.
✨ Mythic создаются за фрагменты конкретного героя; 🌑 Shadow получаются через Arise.
Shadow-каталог содержит 10 героев с изображениями и Boss-связями; Mythic-каталог
пока пуст. Обе редкости не входят в обычную гачу.

Mini superadmin авторизуется только по закреплённому `SUPERADMIN_USER_ID = 694384548`
в `app/mini/superadmin/access.py`. ID подтверждён владельцем; username и topic-admin
права не дают доступа к `/superlook`, `/superadd`, `/superdel`, `/superchars`, `/superluck`.

`/supertitle @user 7d "Титул"` доступна администратору текущей Mini-темы.
Титул хранится до указанного срока в БД и отображается как `[Титул]@username`.
Сертификат `chat_title` сохраняет прежний запрос администратору.

## Структура

```text
bot.py                          startup, polling, routers, timers, Boss watcher
check_bot.py                    imports, schema, catalog sync, content safety
app/mini/
  handlers.py                   home / launcher / character; parent Mini router
  ui/                           context/transport, durable navigation/publication state,
                                private registration, home, summons, collection/inventory/shop
  registration.py               owner/TTL name requests + atomic creation and starter gift
  village/                      timestamp production, workers, food, boosts, ephemeral UI
  duels/                        pending locks, escrow, simultaneous resolver, ephemeral UI
  mythic/                       fragment grants and one-time atomic crafting
  shadow.py / weekly.py          Arise extraction and weekly chest rewards
  tower/                        handlers, service, rarity/list/card selection, durable retry,
                                combat adapter, repository, floors.json, balance.json
  equipment/                    handlers, service, catalog, items.json
  titles/                       временные титулы, parser/service/admin handler
  onboarding.py / notifications.py  atomic starter tickets, durable public notices и watcher
  rules.py                      тексты десяти разделов пользовательской справки
  superadmin/                   numeric access, strict parser, service, handlers + persistent audit
  combat/                       common tags, integer matchups, creatures, hero_abilities
  boss/
    handlers.py                 combat callbacks; parent Boss router
    registration.py             registration and admin callbacks
    selection.py                battle hero selection
    presentation.py / ui.py     private screens, keyboards, announcement transport
    public.py / watcher.py      public messages and recovery
    combat.py                   stable combat use cases and transactions
    calculations.py             primary-hit calculations
    runtime.py                  turn progression and hook orchestration
    repository.py / rewards.py  snapshots, journal, transactional rewards
    boss_abilities/             Boss-specific engine and catalog
    schema.py                   Boss schema and legacy battle migrations
  effects/                      effect registry, contracts, existing use handlers
  wallet.py                     canonical coin mutations and ledger
  migrations/                   ordered idempotent Mini schema steps
  schema.py                     transactional migration entrypoint
  content/                      hero settings, heroes/{rarity}.json, shop JSON and images
  daily.py / shop.py / gacha.py / items.py / hero_upgrades.py
                                feature services with SQLite transactions
docs/ui/                        archived design maps, actual 109-screen matrix and generator
docs/architecture/              audit and extension contracts
docs/operations/                admin grants and recovery
docs/history/                   historical release documents
deploy/                         systemd deployment and watcher
```

Подробности: [архитектура и расширение](docs/architecture/ARCHITECTURE.md),
[независимый аудит V1.2.5](docs/architecture/V1.2.5_AUDIT.md),
[Combat v2](app/mini/boss/COMBAT_V2.md),
[аудит расширения](docs/architecture/V1.2.5_COMBAT_AUDIT.md),
[порядок effects и persistent state](docs/architecture/CREATURE_TRAITS.md).

## Запуск и проверка

Python 3.13, зависимости из `requirements.txt`. Создай venv и установи зависимости:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

На Windows используй `.venv\Scripts\python.exe` и `.venv\Scripts\pip.exe`.
В `.env` нужны `BOT_TOKEN` и, при необходимости, `BOT_TIMEZONE=Europe/Moscow`.
Настройки тем, Mini worlds и администраторов находятся в `app/topics.py`.
Не включай `.env`, рабочую БД или backup БД в Git.

```bash
python -m unittest discover -s tests -v
python check_bot.py
python -m compileall -q bot.py app
python -m app.mini.combat.hero_abilities.validate
python -m app.mini.boss.boss_abilities.validate
python -m app.mini.tower.validate
python -m app.mini.validate_v142
git diff --check
python bot.py
```

`check_bot.py` не запускает polling, но создаёт/мигрирует `shpakdnd.db`, включает
настроенные worlds и синхронизирует каталоги. Для проверки сохранности данных
применяй миграции к отдельной копии БД, не к рабочему файлу.

Startup сам регистрирует темы с `mini=True`. В Mini-теме администратор выполняет
`/minipanel`, чтобы создать или обновить публичный launcher. Игрок создаётся
кнопкой «Создать персонажа»: имя вводится в личном чате по owner-bound deep link.
Состояние живёт 30 минут и переживает рестарт; `/cancel` отменяет ввод.
Резервная `/minicreate "Имя персонажа"` сохранена.

## Обновление существующей БД

Останови сервис и сделай backup `shpakdnd.db`. Сохрани `.env` и рабочую БД при
замене кода. Миграции запускаются автоматически при старте:

- Mini: `init_mini_db()` выполняет core, inventory, activities, legacy и v1_3–v1_4_2 steps
  в одной `BEGIN IMMEDIATE` транзакции, включая DDL.
- Boss: `init_boss_db()` сохраняет прежний отдельный transactional migrator,
  журнал legacy events и восстановление loadouts до синхронизации героев.
- Steps повторно запускаемы; legacy columns остаются для совместимости.

Миграции additive/idempotent: существующие игроки, коллекции, валюты, Boss state,
обычный D&D и таймеры остаются в своих таблицах. V1.4 добавляет 11 таблиц и nullable/совместимые snapshot-поля.
Завершение V1.4.2 добавляет шесть UI/batch/retry таблиц без изменения старых строк.
Проверки старой БД/повторных миграций/старого startup и ограничения rollback:
[отчёт](docs/architecture/V1.4.2-COMPLETION.md).

Deploy watcher перечисляет новые вложенные module/catalog directories;
при обновлении переустанови path unit по инструкции deploy.

Инструкции: [deploy](deploy/README.md), [recovery](docs/operations/RECOVERY.md),
[admin grants](docs/operations/README_ADMIN_GRANTS.md).

## Контент и контракты

Hero passives находятся в `app/mini/combat/hero_abilities/abilities.json`,
Boss abilities — в `app/mini/boss/boss_abilities/abilities.json`.
Новые item effect keys регистрируются через `EffectDefinition` / `register_effect`;
каталоги проверяют ключи до синхронизации. Изменение coins выполняется только
через `change_balance()` или `change_balance_in_transaction()`.

Tower использует canonical shared combat без импорта Boss combat. Новые каталоги
валидируются на startup; чтение Tower и Equipment не запускает migrations/catalog sync.

Герои находятся в `app/mini/content/heroes/{common,uncommon,rare,legendary,mythic,shadow}.json`.
Каждый файл содержит `{"heroes": [...]}`; настройки гачи и улучшений остаются
в `content/heroes.json`. `mythic.fragment_cost` — положительное целое число.
В boss content опционален `shadow_hero_code`; отсутствие counterpart допустимо.
`shadow_extractable=false` запрещает извлечение, в том числе у манекена.

Производство Mythic настраивается в `app/mini/village/balance.json`
через `mythic_hourly_rate` (по умолчанию `"1.25"`, шаг `0.125`).
Пользовательские JSON и изображения в этом завершении UI не изменялись.
Каталоги, ability keys, ссылки Boss → Shadow и изображения проверяются валидаторами.
Новые товары магазина в будущих версиях добавляются через существующий Mini registry;
покупка/продажа/конвертация обычных D&D валют пока не реализованы.
