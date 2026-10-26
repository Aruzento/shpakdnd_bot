# ТЕХНИЧЕСКОЕ ЗАДАНИЕ — D&D Mini V1.5: авторские 8 Mythic + 4 рейдовых босса

Дата пакета: 26.10.2026. Репозиторий: `Aruzento/shpakdnd_bot`. Ветка: `codex/v1.5`, draft PR #8. Проверенный исходный SHA: `407577640d3141cbd4cb9cc0ad3fdf3b9b28aeaa` (перед работой перепроверить).

## 1. Цель и объём

Сделать игровые записи восьми Mythic и четырёх боссов активным реальным контентом текущей V1.5; не менять механику, не придумывать имён, не генерировать или редактировать арт. 17 героев S5 уже реально импортированы, в active catalog 71 запись. 45 пользовательских PNG уже на feature branch и проходят специальные проверки. Следующий контентный блокер после этой итерации — **отсутствующие настоящие Stories JSON**, которые будут предоставлены позднее.

Затрагиваются: `app/mini/content/heroes/mythic.json`, `app/mini/mythic/raid/bosses.json`, валидаторы каталогов и Image/Story (только при подтверждённой необходимости), sync начальных данных в SQLite, combat normal/raid/hero ownership/evolution и связанные тесты/документация. Не менять структуру и балансы других JSON, гачу, экономику, Story schema, deployment scripts без обоснования и тестов. Миграция SQLite — только при доказанной необходимости, аддитивная и обратимо совместимая; предпочтительно использовать уже существующий sync.

## 2. Файлы для установки

| Файл в архиве | Целевое расположение в репозитории | Статус |
|---|---|---|
| `payload/app/mini/content/heroes/mythic.json` | `app/mini/content/heroes/mythic.json` | Авторские 8 записей; точный байтовый payload |
| `payload/app/mini/mythic/raid/bosses.json` | `app/mini/mythic/raid/bosses.json` | Авторские 4 записи; точный байтовый payload |
| `docs/v1.5/STORY_AUTHOR_TEMPLATE.json` | Уже существует в репозитории | Только образец, **не** production/story input |
| `app/mini/content/hero_images/*` | Уже существует в репозитории | 17 S5 + 24 Mythic PNG, **не** включать повторно |
| `app/mini/mythic/raid/images/raid_boss_1.png`…`raid_boss_4.png` | Уже существует | Четыре PNG, **не** менять |

При коллизии путей сверить текущий HEAD/реальный content: не перезаписывать новые изменения слепо. Не допускать незафиксированных пользовательских правок и сторонних файлов в PR.

## 3. Восемь авторских Mythic

Все Mythic полностью заданы в приложенном `mythic.json`. Сохранять точные `code`, `name`, описание и авторский `passive_text` — не редактировать без решения владельца. Поля `rarity=mythic`, `active=true`, `mythic_element`, `attack`, `image`, `passive_key`, `passives.normal.key`, `passives.raid.key` обязательны. Режимные способности зарегистрированы движком; для конкретных формул источником истины является утверждённый V1.5 engine, не произвольное изменение текста/механики.

| Стихия | Code | Имя | Базовая атака | Картинка | Оба ключа пассивок |
|---|---|---|---:|---|---|
| ice | `mythic_ice_fenrir` | Фенрир льда | 1 | `ice_adult.png` | `mythic_ice` |
| lightning | `mythic_lightning_daenerys_griffin` | Грифон Дейнерис | 2 | `lightning_adult.png` | `mythic_lightning` |
| dark | `mythic_dark_night_raven` | Ворон ночи | 2 | `dark_adult.png` | `mythic_dark` |
| fire | `mythic_fire_flame_hind` | Пламенная лань | 3 | `fire_adult.png` | `mythic_fire` |
| star | `mythic_star_unity_bear` | Медведь единства | 2 | `star_adult.png` | `mythic_star` |
| earth | `mythic_earth_morax_dragon` | Дракон Моракс | 3 | `earth_adult.png` | `mythic_earth` |
| moon | `mythic_moon_fate_rabbit` | Кролик судьбы | 1 | `moon_adult.png` | `mythic_moon` |
| sun | `mythic_sun_lion` | Мифический лев | 1 | `sun_adult.png` | `mythic_sun` |

Защитить получение Mythic: эволюция зверя -> взрослый Mythic-герой, `evolution_pending` при отложенном получении, без craft / покупки героя / обычной гачи. Улучшение до 6 звёзд за универсальные mythic shards; цены 20/35/55/80/110, не повышать базовый ATK. Существующие Hero DB rows / player ownership / stars / fragments / coins не стирать и не пересчитывать произвольно.

Сверить поля `faction`, `damage_type`, `attack_range`, `class_tag`, `special_trait` с основным каталогом и реестром реальных игровых тегов. Выявлять дубли code и расхождения с mode-passives. Если авторский `passive_text` не полностью соответствует вычислениям — отметить в отчёте, **не** менять формулу/описание молча.

## 4. Четыре рейдовых босса

Точные значения уже находятся в JSON. Все Boss code и names являются выбранными автором именами с согласованными латинскими кодами. Механики и фиксированные параметры не меняются.

| № | code | name | image | HP | element | counter | mechanic |
|---|---|---|---|---:|---|---|---|
| 1 | `baldur_adamantine_colossus` | Адамантиевый исполин подземелья Балдура | `raid_boss_1.png` | 100000 | fire | ice | armor_cycle |
| 2 | `tsurumi_thunderbird` | Громовая птица острова Цуруми | `raid_boss_2.png` | 115000 | lightning | star | flight_cycle |
| 3 | `wood_steel_butcher_golem` | Голем мясник из дерева и стали | `raid_boss_3.png` | 130000 | dark | sun | periodic_heal |
| 4 | `spatial_anomaly_angel` | Ангел из пространственной аномалии | `raid_boss_4.png` | 150000 | star | moon | hp_defense_phases |

Профили frozen в `app/mini/mythic/raid/mechanics.py`:

- `armor_cycle`: первые 10 атак с бронёй, следующие 5 без неё, повтор; исходные features `["armored"]`.
- `flight_cycle`: первые 8 атак в полёте, следующие 2 на земле, повтор; исходные features `["flying"]`.
- `periodic_heal`: после каждой пятнадцатой подтверждённой попытки heal = 325 HP (= 0,25% от исходных 130000), ограничение до максимума; Sun подавляет, не воскрешает; исходные features `[]`.
- `hp_defense_phases`: original_max_hp=150000, пороги `112500/75000/37500`, фазы `["armored","flying"]`, затем `["armored"]`, `["flying"]`, `[]` — только в сторону ослабления защиты.

Каждый `regeneration_percent=0`, `counter_policy=first_confirmed_counter_hero_attack_on_this_raid`, `shadow_extractable=false` и без `shadow_hero_code`. Первый подтверждённый удар подходящего Mythic активирует урон 100% для команды, включая hit с actual0, но `flying` может дать actual0. Не выбирать имена/коды по картинке заново, не изменять защиту/выплаты.

Проверить R09 и O01 награды от **исходного HP** (фонды 20/23/26/30), Moon Eclipse от original HP, атомарность HP/attempt/revision/damage/egg/audit/reward, no double-pay, cancel race, crash/restart, frozen snapshots. Любая ошибка ledger должна откатывать всю атаку. Старые рейды с legacy snapshots не мигрировать на новые механики автоматически.

## 5. Изображения и связи

В feature branch уже 45 реальных PNG. Их SHA-256 до/после должны совпасть. Углы: белый S5 RGB 252..255, чёрный Mythic и Boss RGB 0..4, alpha 255. Запрещено ослаблять этот допуск, заменять/перекрашивать арты, менять имена PNG, создавать заглушки. Проверить существование всех восьми adult + соответствующих 16 pet-стадий, четырёх boss, 17 S5. Реестр S5 уже импортирован и не должен копироваться повторно.

## 6. Истории (НЕ входят в author pack)

Истории — отдельные внешние JSON DAG в `STORIES_DIR`, вне Git checkout/watcher. Пользователь их пока не предоставил. Не включать примерный `STORY_AUTHOR_TEMPLATE.json` в production и не добавлять «историю-заглушку» в историю реального мира. Read-only `python tools/validate_story_content.py --directory <external_temp_dir>` — на временных фикстурах. Следующие авторские сценарии будут переданы отдельно. Требования: schema_version1, уникальный code/title/description/start_node, DAG без циклов, окончания, до 20 выборов на узел, UTF-16 ограничения.

Критерий корректного частичного завершения: `PNG_PASS`, `S5_IMPORTED`, `MYTHIC_RECORDS_READY`, `RAID_RECORDS_READY` = PASS; `STORIES_READY`, `STRICT_CONTENT_PASS` = BLOCKED_BY_INPUT. `--strict-content` обязан вернуть exit2 **только из-за отсутствия реальных Stories**, если иного блокера нет. Это НЕ считается выпускной готовностью. Если выявлены иные причины — исправить соответствующую техническую ошибку/описать несовместимость, не объявлять success.

## 7. Тестирование и совместимость (обязательно, часть реализации)

### Unit

- Проверка 8 авторских Mythic по основному валидатору и пассивному registry; уникальность code во всех rarity JSON; точные имена, стихии, 8 отдельных наборов normal/raid key/ATK/image.
- Отказ при: отсутствующем PNG, неправильном/дублированном элементе, code/названии, неверной rarity, key, невалидной фракции/attack_range, неверном фоне/alpha, отрицательном ATK.
- 4 авторских Boss: JSON contract, строго 4 + 4 image, immutable 4 mechanic profiles, counter policy, no Shadow links, unknown fields, duplicate keys/image/codes, некорректный HP.
- S5 уже 17/17 и все изображения по контрольным суммам; дубли/откат/частичный импорт отклоняются.

### Интеграционные

- `load_hero_catalog -> SQLite seed -> player collection -> hero card/photo -> normal/raid -> economy`; 8 Mythic, 4 bosses, Tower (если задействован), гача не призывает Mythic.
- Эволюция активного питомца: 3 стадии, one-time grant Mythic, pending -> resumption, stars 1..6, стоимость/баланс, запрет craft.
- Боссы: O02 контрпик при zero actual; цикл armor 10+5; flight 8+2 включая недосягаемые удары; heal 15/30 и Sun suppression; пороги HP 112500/75000/37500, monotonic/frozen, Eclipse и no-resurrection.
- Repeated callbacks, два администратора, race отмена vs смертельный удар, невозможность двойного начисления осколков, экономические ledger и идемпотентность.
- Защита пользовательских данных: старые 71 каталожные записи семантически неизменны, существующие player-hero rows, coins, shards, shadow и rewards не пропадают после 3 повторов миграции, рестарт, old-code startup на одноразовой копии.
- Story: отсутствие реальных сюжетов не мешает независимым режимам; ошибки и формат external source обрабатываются безопасно; не включать фикстуру в живую поставку.

### Полные обязательные команды

Из изолированного окружения, с раздельной временной SQLite, на **финальном SHA**:

```bash
python -m unittest discover -s tests -q
python check_bot.py
python -m compileall -q bot.py app
python -m app.mini.combat.hero_abilities.validate
python -m app.mini.boss.boss_abilities.validate
python -m app.mini.tower.validate
python -m app.mini.mythic.policies
python -m app.mini.mythic.content
python -m app.mini.mythic.content --strict-content
python tools/import_v15_s5.py --source docs/v1.5/S5_IMPORT_MANIFEST.json --images app/mini/content/hero_images
python tools/check_v15.py   # запускать ЧЕРЕЗ предусмотренный репозиторием scripts/run_isolated_checks.py
python tools/validate_v15_migration.py
python tools/validate_deploy_bundle.py
git diff --check
```

`--strict-content` exit2 ожидаем до Историй; **нельзя** подменять этот вывод на полноценный PASS. `tools/check_v15.py` запускать только по согласованному isolated harness с DND_CHECK_SANDBOX, а не напрямую без нужных переменных. Также запуск GitHub Linux CI Python 3.11+3.13, существующих D&D shop/PTY/SQLite тестов. Все результаты и skips — в отчёт. Операций на реальном production не делать.

## 8. Деплой, откат и отчёт

- Не менять `main`, production, staging, systemd, watcher, `sudo deploy-shpakdnd`, рабочую SQLite и live `STORIES_DIR`.
- Изменения только в `codex/v1.5`, тот же draft PR #8; после push проверить точный commit/HEAD и CI именно на нём.
- Документация: `docs/v1.5/CONTENT_IMPORT_CHECKLIST.md`, `IMPLEMENTATION_STATUS.md`, `TEST_MATRIX.md`, `WORK_LOG.md`, `RELEASE_AND_ROLLBACK.md`, зависимости content readiness, описание migration/rollback и запрет выкладки без реального сценария.
- Проверки изолированы, включая три replay на миграции и preserved existing data; backup реальной DB и сценическое тестирование — только позднее по решению владельца.
- Отчёт: 8+4 records, 79 героев всего если другие данные не изменились, 45 PNG byte-preserved, полный file diff и отсутствие внеплановых правок, тесты и точный SHA, CI URL, неудовлетворённые Story inputs, DB-copy/staging/production риски.
- До полного strict-content, independent review, копии реальной DB и живого staging — **«В разработке», «Релиз заблокирован»**. Отчёт Codex сам по себе не разрешение переносить в main.
