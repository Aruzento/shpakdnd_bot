# D&D Mini — V1.2.5

Telegram-бот на aiogram 3 и SQLite. V1.2.5 отделяет общие UI, combat, economy
и effect contracts и расширяет Boss Combat v2 природными features и свойствами
героев. Существующие события сохраняют прежние правила; новая механика включается
для новых событий. Экономика, базовые характеристики, цены и награды сохранены.
У Железного исполина прежний mechanism явно заменён природной регенерацией construct.

## Возможности

D&D Mini: персональные ephemeral-меню, персонаж, daily, магазин, инвентарь и
использование предметов, гача, коллекция, звёзды и продажа осколков, события
«Святой / Демон / Жнец» и лабиринт, групповые боссы Combat v2.

Боевые герои фиксируются при старте в immutable loadout; текущий ход,
ability state, события и награды сохраняются в SQLite. Watcher восстанавливает
публичный ход и пропускает просроченные ходы после перезапуска. Фракции,
классовые исключения и 4-часовой таймер сохранены. Новые features и traits
дают броню, досягаемость, регенерацию, resurrection, poison, временный запас
награды и corruption; они сохраняются вместе с боем.

Обычные D&D команды персонажей, инвентаря, бросков и таймеров остаются в `app/handlers/`,
`app/db/` и `app/services/`. Mini использует отдельные таблицы с префиксом `mini_`.

Башня, экипировка и Mythic в этот релиз не входят.

## Структура

```text
bot.py                          startup, polling, routers, timers, Boss watcher
check_bot.py                    imports, schema, catalog sync, content safety
app/mini/
  handlers.py                   home / launcher / character; parent Mini router
  ui/                           shared context, callbacks, ephemeral transport
                                inventory, shop, heroes, daily/rules subrouters
  events/                       Telegram handlers + transactional game service
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
  content/                      hero/shop JSON and images
  daily.py / shop.py / gacha.py / items.py / hero_upgrades.py
                                feature services with SQLite transactions
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
python bot.py
```

`check_bot.py` не запускает polling, но создаёт/мигрирует `shpakdnd.db`, включает
настроенные worlds и синхронизирует каталоги. Для проверки сохранности данных
применяй миграции к отдельной копии БД, не к рабочему файлу.

Startup сам регистрирует темы с `mini=True`. В Mini-теме администратор выполняет
`/minipanel`, чтобы создать или обновить публичный launcher. Игрок создаётся
приватной командой `/minicreate "Имя персонажа"`.

## Обновление существующей БД

Останови сервис и сделай backup `shpakdnd.db`. Сохрани `.env` и рабочую БД при
замене кода. Миграции запускаются автоматически при старте:

- Mini: `init_mini_db()` выполняет core, inventory, activities и legacy steps
  в одной `BEGIN IMMEDIATE` транзакции, включая DDL.
- Boss: `init_boss_db()` сохраняет прежний отдельный transactional migrator,
  журнал legacy events и восстановление loadouts до синхронизации героев.
- Steps повторно запускаемы; legacy columns остаются для совместимости.

Проверены пустая БД, старые схемы, повторный запуск, rollback миграции и
сохранность всех 19 Mini-таблиц на копии существующей БД. Полный regression suite
содержит 348 тестов; исходные 321 сценарий сохранены.

Deploy watcher перечисляет новые вложенные module/catalog directories;
при обновлении переустанови path unit по инструкции deploy.

Инструкции: [deploy](deploy/README.md), [recovery](docs/operations/RECOVERY.md),
[admin grants](docs/operations/README_ADMIN_GRANTS.md).

## Контент и подготовка к V1.3

Hero passives находятся в `app/mini/combat/hero_abilities/abilities.json`,
Boss abilities — в `app/mini/boss/boss_abilities/abilities.json`.
Новые item effect keys регистрируются через `EffectDefinition` / `register_effect`;
каталоги проверяют ключи до синхронизации. Изменение coins выполняется только
через `change_balance()` или `change_balance_in_transaction()`.

Будущая Tower может использовать shared tags, matchups, hero hooks, players,
heroes, effects и wallet без импорта Boss combat. Её состояния и use cases
должны принадлежать отдельной feature; копировать Boss combat не требуется.
