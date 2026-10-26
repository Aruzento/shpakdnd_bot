# Контент V1.5: авторские 8 Mythic и 4 Raid

> Актуальный срез 26.10.2026: [RELEASE_HARDENING.md](RELEASE_HARDENING.md).
> REAL_CONTENT_IN_REPO=PASS (79 heroes/4Raid/45PNG и Story53/113/7),
> STORY_EXTERNAL_TARGET=NOT_PUBLISHED, STRICT_SANDBOX=PASS на independently audited
> cef77cc87f6a2286284f5cf8fdb851c775b04dd1; новый final SHA/CI — в отчёте PR #8.
> STRICT_PRODUCTION=NOT_CHECKED/BLOCKED, PROD_DB_COPY=NOT_RUN, STAGING=NOT_RUN.
> **В разработке / Релиз заблокирован.** Упоминания отсутствующего контента ниже —
> исторические срезы до импорта, не текущий статус O03/O09.

## Исторические сведения (срезы до текущего hardening)


Статус: **В разработке. Релиз заблокирован**. Проход от проверенного
`407577640d3141cbd4cb9cc0ad3fdf3b9b28aeaa`, ветка `codex/v1.5`, draft PR #8.
Источник: `AUTHOR_CONTENT_IMPORT_APPROVED_TZ.md`, точные байтовые JSON и SHA-256
из `AUTHOR_CONTENT_MANIFEST.json`. Префикс доставки payload/ в Git не создаётся.

| Компонент | Результат |
|---|---|
| PNG_PASS | PASS: все 45 настоящих PNG, исходные SHA-256 сохранены |
| S5_IMPORTED | PASS: прежние 17 записей 5/6/4/2, без повторного импорта |
| MYTHIC_RECORDS_READY | PASS: 8 авторских активных карточек; 71 прежняя → 79 |
| RAID_RECORDS_READY | PASS: 4 авторские записи, существующие raid_boss_1..4.png |
| STORIES_READY | BLOCKED_BY_INPUT: настоящие Story DAG JSON пока не предоставлены |
| STRICT_CONTENT_PASS | BLOCKED_BY_INPUT, strict exit 2 только из-за Историй |

## Неизменность и связи

Все 71 прежние карточки семантически сохранены, settings/веса/цены/S5 manifest
не менялись. Обычная гача исключает Mythic/Shadow; старый craft Mythic запрещён.
Авторские имена/code/тексты/числа сохранены побайтно. SQLite использует штатный
sync каталога, дополнительная миграция схемы не нужна. Snapshot старого боя/рейда
не пересчитывается по новым JSON.

24 Mythic PNG: восемь элементов ice/lightning/dark/fire/star/earth/moon/sun,
стадии egg/young/adult. Внутренняя стадия wonder использует young.png.
Четыре босса используют raid_boss_1.png…raid_boss_4.png по порядку поставки.
Для 17 S5 RGB всех четырёх углов 252–255, для Mythic/Raid 0–4, alpha всегда 255.
Исходные 45 хешей — CONTENT_ASSET_SHA256.json. Арт не редактировался.

## Каталоги и проверки

Основной каталог проверяет Mythic до legacy defaults: integer ATK, явные
normal/raid ключи своего элемента, legacy key, adult PNG, boolean active и
авторские строки. Невалидные faction/range и неизвестные registry keys запрещены.
Полный Mythic content требует восемь разных элементов/code/name и все 24 PNG.
Raid сохраняет строгие четыре профиля, уникальные code/image, unknown/duplicate
JSON fields, no Shadow links, точные HP/контрстихии/счётчики и black PNG.

Новые тесты tests/v1_5/test_author_content.py используют настоящие JSON: byte
manifest, 79 catalog, сохранение прежних карточек, registry, normal и raid,
pet evolution/pending/resume/однократная выдача, звёзды 1..6 за 20/35/55/80/110,
Telegram/photo, counter при zero, фазы, heal, ledger/replay и restart. Общая
матрица также проверяет Sun suppression, Eclipse, гонки отмены/победы, rollback
транзакций и защиту снимков. Тестовые карточки остаются только в tests.

## Единственный оставшийся контентный пакет

Нужны настоящие внешние Story JSON по STORIES_IMPORT_GUIDE.md. Шаблон в docs
не устанавливается в STORIES_DIR и не считается поставленной Историей.
MYTHIC_AUTHOR_INPUT_REQUIRED.md и RAID_BOSSES_AUTHOR_INPUT_REQUIRED.md —
исторические незаполненные шаблоны предыдущего этапа; их запросы теперь закрыты.
Данные поставлены в игровых каталогах, дополнительного согласования механик нет.

Полные Windows/Linux3.11/3.13 count/skip/exit/log и точный финальный SHA —
в итоговом отчёте и том же draft PR. До Историй, независимого ревью, проверки
копии production DB и реального staging выкладка не разрешена.


### 26.10.2026: авторская История и once-маршруты

Подготовлен неопубликованный пакет «Тайна пропавшей овцы»: 46 сцен, 7 концовок,
113 неизменных выборов, schema_version2/visit_policy once. Общее групповое visited
восстанавливается из story_steps без миграции; фильтрация исключает повторы и тупики.
Авторский source/JSON/audit сохранены с MANIFEST; тесты сравнивают тексты и маршруты.
Изолированная проверка настоящего JSON может дать strict-content PASS0; рабочий пустой
STORIES_DIR остаётся BLOCKED_BY_INPUT/exit2. Публикация не разрешена и не выполнена.
Технические гарантии/пределы и команды: STORY_ONCE_VERIFICATION.md, STORIES_IMPORT_GUIDE.md.
Нужны независимое ревью, production DB copy и реальный Telegram staging.
Статус: В разработке / Релиз заблокирован. Точный проверенный SHA и CI — в отчёте PR #8.
