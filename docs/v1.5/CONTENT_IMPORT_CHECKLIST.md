# Контент V1.5 — импорт и ограничения

Никакие PNG, боссы или сюжеты не генерировались. Существующие 54 героя и старые коды сохранены. Реальный предоставленный `DND_Mini_17_heroes_NEW_PASSIVES.json` сохранён без изменения игровых данных в `S5_IMPORT_MANIFEST.json`. Это **импортный документ**, не активный игровой каталог.

## S5

Нужны ровно 17 настоящих квадратных PNG на белом фоне с именами `image` из манифеста; распределение героев 5 common / 6 uncommon / 4 rare / 2 legendary. Все 17 отсутствуют в `app/mini/content/hero_images/` и не обнаружены в проверенном комплекте Downloads/исходного проекта.

Проверка: `python tools/import_v15_s5.py --source <S5.json> --images <реальный-каталог-PNG>`; по умолчанию только чтение. После полного успеха `--apply` добавляет героев в соответствующие rarity JSON и PNG, не заменяет существующие коды/изображения и откатывает файловые изменения при ошибке записи. Не выполнять на сервере/в рабочей ветке main. Затем полный набор проверок, включая актуальные количества героев/изображений.

Валидатор проверяет все 17 до первой записи, дубликаты/поля/теги/пассивки общим валидатором, PNG/CRC/1:1/непрозрачные белые углы. Художественное соответствие/фон по всей площади подтверждает владелец при визуальной приёмке.

## Mythic

Нужны настоящие восемь hero records в `app/mini/content/heroes/mythic.json`: уникальный авторский code/name, `mythic_element`, normal/raid ключи `mythic_<element>`, базовая атака ice=1/lightning=2/dark=2/fire=3/star=2/earth=3/moon=1/sun=1, image=`<element>_adult.png`.

В `app/mini/content/hero_images/` нужны для каждого `ice, lightning, dark, fire, star, earth, moon, sun` три файла `<element>_egg.png`, `<element>_young.png`, `<element>_adult.png`: 24 настоящих PNG 1:1 с чёрным фоном. Один adult PNG используется зверем и героем. Отдельные восемь боевых изображений не нужны. До появления взрослого героя/его PNG рост сохраняется в evolution_pending, не создаёт вымышленного героя.

`python -m app.mini.mythic.content` сообщает блокеры; отсутствие данных не мешает независимым режимам. `--strict-content` возвращает 2 при неполном комплекте, 1 при некорректном контенте, 0 только при полной проверке. Диагностический выход 0 без strict **не означает готовность контента**.

## Рейдовые боссы

Путь: `app/mini/mythic/raid/bosses.json`, изображения в соседнем `images/`. Корень: schema_version=1, bosses (ровно 4 реальные записи при непустом каталоге). Для каждого обязательны code/name/max_hp>=100000/element/counter_elements/image. Допустимы features (`flying`, `armored`), regeneration_percent (0–100), shadow_extractable (boolean), shadow_hero_code и единственный поддержанный counter_policy из `catalog.COUNTER_POLICY`. Изображение — настоящий квадратный PNG basename; разрешённая тень обязана существовать в каталоге Shadow, иначе validation отклоняет ссылку.

Произвольный неизвестный ключ/защита/неподдерживаемая способность отклоняются: если владелец предоставит новый тип, сначала добавить обработчик и тест. Четыре фиктивных production-записи отсутствуют; примеры принадлежат только `tests/v1_5`.

## Истории

Сценарии размещаются во внешнем `STORIES_DIR`, вне любого Git checkout и watcher. Подробный формат — STORIES_AUTHOR_GUIDE.md. Discovery при list/create/start без рестарта. Сюжетных production-примеров нет.

## Следующая авторская поставка: ровно45PNG

Эта итерация не импортирует production content. S5 JSON уже сохранён побайтово, а все механики O01–O10 утверждены. Ни повторного согласования механик, ни вымышленных JSON/PNG не требуется.

17 белофоновых PNG в `app/mini/content/hero_images/`:

- `daenerys_dragonflame.png`
- `kaiden_void_magus.png`
- `ordo_vallis_secretary.png`
- `ordo_vallis_inquisitor.png`
- `ordo_vallis_soul_lord.png`
- `ordo_vallis_legionary.png`
- `ordo_vallis_commander.png`
- `varzek_ash_fury.png`
- `sairen_red_needle.png`
- `draghar_black_cleaver.png`
- `talvira_blackroot.png`
- `azark_storm_spear.png`
- `shaar_ashen_palm.png`
- `master_puppeteer.png`
- `kaeron_shadow_reaver.png`
- `veirissa_darkarrow.png`
- `ezio_auditore_isekai.png`

24 чёрнофоновых Mythic PNG в том же каталоге:

- `ice_egg.png`
- `ice_young.png`
- `ice_adult.png`
- `lightning_egg.png`
- `lightning_young.png`
- `lightning_adult.png`
- `dark_egg.png`
- `dark_young.png`
- `dark_adult.png`
- `fire_egg.png`
- `fire_young.png`
- `fire_adult.png`
- `star_egg.png`
- `star_young.png`
- `star_adult.png`
- `earth_egg.png`
- `earth_young.png`
- `earth_adult.png`
- `moon_egg.png`
- `moon_young.png`
- `moon_adult.png`
- `sun_egg.png`
- `sun_young.png`
- `sun_adult.png`

Оставшиеся4PNG: `app/mini/mythic/raid/images/{bosses[i].image}` для i=0,1,2,3. Четыре точных basename поступят в авторских4Boss records; в текущем репозитории имён нет. Они не придумываются Codex. Это41известноеимя+4точноопределяемыхавторскимJSON, всего45.

Нужны8Mythic records в `app/mini/content/heroes/mythic.json`: текущая rarity-схема heroes, уникальныйcode/name,mythic_element,парыnormal/raid.key=mythic_element,фиксированныйATK1/2/2/3/2/3/1/1,imageelement_adult.png; Stars1–6/cost20/35/55/80/110 задаются механикой. Через основной load_hero_catalog/hero validator, без отдельной особой гачи.
Нужны4Boss records `app/mini/mythic/raid/bosses.json`: schema_version1,bosses[4],code/name/element/image/max_hp≥100000/counter_elements, optionalfeatures/regeneration_percent/shadow_extractable/shadow_hero_code/counter_policy. Разрешённая newcounter_policy=first_confirmed_counter_hero_attack_on_this_raid. Типы/unknown fields/Shadow links/PNG строгие; новые неизвестные механики требуют author rule+typed handler/tests.
Нужны внешние Story JSON DAG в STORIES_DIR внеGit/watcher; schema из STORIES_AUTHOR_GUIDE.md. Разные внешние пути для будущихstaging/production, их файлы сейчас не изменяются. Ordinary raid definitions — будущее авторское наполнение, none/not_configured сейчас корректен.

## Безопасные команды после поставки

1. `python tools/import_v15_s5.py --help`
2. `python tools/import_v15_s5.py --source docs/v1.5/S5_IMPORT_MANIFEST.json --images <каталог17реальныхPNG>` — dry-run, чтение.
3. Только после полного валидного комплекта: та же команда с `--apply` вfeature-ветке (не выполнялась сейчас).
4. В изоляции: `python -m app.mini.mythic.content --strict-content`, hero/boss/Tower/Editorial/V1.5validators, затем весьunittest и LinuxexactSHA. При недоступном файлеexit2/BLOCKED_BY_INPUT; неверномJSON/PNG/ссылкеexit1; только полномкомплектеexit0. MissingS5art также блокируетstrict, даже еслиrecords уже есть.
5. Отдельно предоставленная безопасная productionSQLitecopy, migration/replay/oldcode, затем реальнаяTelegram/stagingприёмка и independentreview; merge/deploy решает владелец отдельно.

Текущий sourcepack неполон и это ожидаемый контентный BLOCKED_BY_INPUT. В тестах complete fixtures создаются только во временныхкаталогах; fakeproduction records/images не добавлялись.

## Поставка изображений и движок четырёх боссов — 26.10.2026

17 S5 и 24 Mythic PNG уже добавлены в feature-ветку; S5 records не импортированы.
Предоставленные JPG боссов перекодированы и размещены: app/mini/mythic/raid/images/raid_boss_1.png,
raid_boss_2.png, raid_boss_3.png, raid_boss_4.png. Порядок/оригиналы — images/README.md.
Это технические имена файлов по просьбе владельца, не новые игровые code/name.
Остаются реальные четыре Boss records (code/name/image и явная Shadow-связь при наличии),
восемь Mythic records, внешние Story DAG. Прежние слова «45 PNG отсутствуют» — исторический
статус до поставки, не актуальный inventory. PNG наличия недостаточно для strict PASS.
Почти белые углы ранее поставленных S5 (RGB252…255) не удовлетворяют прежнему exact-white
контракту; строгость не ослабляется без отдельного ответа владельца. Master Puppeteer
перекодирован из JPEG payload в настоящий PNG без перерисовки.
