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

Путь: `app/mini/mythic/raid/bosses.json`, изображения в соседнем `images/`. Корень: schema_version=1, bosses (ровно 4 реальные записи при непустом каталоге). Для каждого обязательны code/name/max_hp>=100000/element/counter_elements/image. Допустимы features (`flying`, `armored`), regeneration_percent (0–100), shadow_extractable (boolean), shadow_hero_code и единственный поддержанный counter_policy из `catalog.COUNTER_POLICY`. Изображение — настоящий квадратный PNG basename; разрешённая тень обязана существовать в каталоге Shadow, иначе Arise не выдаётся.

Произвольный неизвестный ключ/защита/неподдерживаемая способность отклоняются: если владелец предоставит новый тип, сначала добавить обработчик и тест. Четыре фиктивных production-записи отсутствуют; примеры принадлежат только `tests/v1_5`.

## Истории

Сценарии размещаются во внешнем `STORIES_DIR`, вне любого Git checkout и watcher. Подробный формат — STORIES_AUTHOR_GUIDE.md. Discovery при list/create/start без рестарта. Сюжетных production-примеров нет.

## Повторная инвентаризация входов

Targeted read-only поиск: C:/Users/Aruko/Downloads, C:/Users/Aruko/.codex/attachments, исходный Z: проект и C: worktree. Full independent entity set не найден; пользовательские original JSON/PNG не менялись, посторонние архивы не открывались. S5 manifest побайтово соответствует предоставленному JSON.

Точные недостающие файлы в app/mini/content/hero_images/:

- `daenerys_dragonflame.png` — S5, белый фон.
- `kaiden_void_magus.png` — S5, белый фон.
- `ordo_vallis_secretary.png` — S5, белый фон.
- `ordo_vallis_inquisitor.png` — S5, белый фон.
- `ordo_vallis_soul_lord.png` — S5, белый фон.
- `ordo_vallis_legionary.png` — S5, белый фон.
- `ordo_vallis_commander.png` — S5, белый фон.
- `varzek_ash_fury.png` — S5, белый фон.
- `sairen_red_needle.png` — S5, белый фон.
- `draghar_black_cleaver.png` — S5, белый фон.
- `talvira_blackroot.png` — S5, белый фон.
- `azark_storm_spear.png` — S5, белый фон.
- `shaar_ashen_palm.png` — S5, белый фон.
- `master_puppeteer.png` — S5, белый фон.
- `kaeron_shadow_reaver.png` — S5, белый фон.
- `veirissa_darkarrow.png` — S5, белый фон.
- `ezio_auditore_isekai.png` — S5, белый фон.

- `ice_egg.png` — Mythic, чёрный фон.
- `ice_young.png` — Mythic, чёрный фон.
- `ice_adult.png` — Mythic, чёрный фон.
- `lightning_egg.png` — Mythic, чёрный фон.
- `lightning_young.png` — Mythic, чёрный фон.
- `lightning_adult.png` — Mythic, чёрный фон.
- `dark_egg.png` — Mythic, чёрный фон.
- `dark_young.png` — Mythic, чёрный фон.
- `dark_adult.png` — Mythic, чёрный фон.
- `fire_egg.png` — Mythic, чёрный фон.
- `fire_young.png` — Mythic, чёрный фон.
- `fire_adult.png` — Mythic, чёрный фон.
- `star_egg.png` — Mythic, чёрный фон.
- `star_young.png` — Mythic, чёрный фон.
- `star_adult.png` — Mythic, чёрный фон.
- `earth_egg.png` — Mythic, чёрный фон.
- `earth_young.png` — Mythic, чёрный фон.
- `earth_adult.png` — Mythic, чёрный фон.
- `moon_egg.png` — Mythic, чёрный фон.
- `moon_young.png` — Mythic, чёрный фон.
- `moon_adult.png` — Mythic, чёрный фон.
- `sun_egg.png` — Mythic, чёрный фон.
- `sun_young.png` — Mythic, чёрный фон.
- `sun_adult.png` — Mythic, чёрный фон.

Отдельно нужны8авторских records в mythic.json,4настоящие Boss records в raid/bosses.json и PNG в images/ (имена/features/counter/shadow links задаёт автор), реальные внешние Story JSON и доступный STORIES_DIR. Диагностика missing_inputs выводит абсолютные пути41недостающего hero PNG с причиной; агрегированные blockers перечисляют records/Stories.
O01 требует решения, а не файлов: получатели,формула/валюта/размер,status/победа/Arise. O04 — авторские ordinary raid definitions, если нужны вместо none. Утвердить PROVISIONAL O02/O05/O06/O07/O08/O10 отдельно. Неизвестные имена/сюжеты/формулы не генерируются.
