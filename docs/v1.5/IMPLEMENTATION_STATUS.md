# D&D Mini V1.5 — утверждённые O01–O10

> Актуальный срез 26.10.2026: [RELEASE_HARDENING.md](RELEASE_HARDENING.md).
> REAL_CONTENT_IN_REPO=PASS (79 heroes/4Raid/45PNG и Story53/113/7),
> STORY_EXTERNAL_TARGET=NOT_PUBLISHED, STRICT_SANDBOX=PASS на independently audited
> cef77cc87f6a2286284f5cf8fdb851c775b04dd1; новый final SHA/CI — в отчёте PR #8.
> STRICT_PRODUCTION=NOT_CHECKED/BLOCKED, PROD_DB_COPY=NOT_RUN, STAGING=NOT_RUN.
> **В разработке / Релиз заблокирован.** Упоминания отсутствующего контента ниже —
> исторические срезы до импорта, не текущий статус O03/O09.

## Исторические сведения (срезы до текущего hardening)


Статус: **В разработке** до авторского контента и живой приёмки. Все механические решения O01–O10 утверждены; повторного согласования не требуется. Приоритетный источник: [O01_O10_APPROVED_TZ.md](O01_O10_APPROVED_TZ.md), предоставленный как123.md. R01–R20 сохраняются за исключением явных новых решений.

Ветка codex/v1.5, тот же draft PR#8. Начальный проверенный HEAD dfe7df7311d9789469063c783d65f8390b281bcb; PR base/origin/main d077dd2a2c29ad420b138bf07af23c77f4674d0b. Оригинальное пользовательское дерево Z: и рабочая SQLite сохранены; merge/deployment/systemd/polling не выполнялись.

| O | Реализация | Статус |
|---|---|---|
| O01 | Две отмены; compensation P=floor(ceil(M/5000)*min(D,M)/M), largest remainder с player_id tie, forecast/reason/revision/audit/ledger/replay/races, cancelled без победных бонусов | IMPLEMENTED_VERIFIED |
| O02 | Первая подтверждённая контр-атака активирует100% даже приactual0; источник сохранён; legacy positive-only snapshots не переписываются | IMPLEMENTED_VERIFIED |
| O03 | Строгий контракт ровно4Boss, PNG/Shadow/features/counter, empty-catalog UI и temporary-fixture tests готовы | IMPLEMENTED_VERIFIED: четыре авторских records и PNG подключены |
| O04 | Раздельные normal/raid, none/not_configured без normal leak; typed effects/ссылки/поля проверяются | IMPLEMENTED_VERIFIED; авторские ordinary raid definitions добавляются позднее |
| O05 | Eclipse10–15% original HP один раз, current/max увеличиваются, p/gain/stars frozen, фонды R09/O01 original | IMPLEMENTED_VERIFIED |
| O06 | Независимый Star-бросок каждому eligible normal Boss; Tower player/floor once; durable journal, no admin/banish/loss/Raid/PvP | IMPLEMENTED_VERIFIED |
| O07 | Golden/Thief/Blessing стандартной награды; независимые источники не умножаются/не дублируются, chest сохраняется; floor draw frozen с первого входа | IMPLEMENTED_VERIFIED |
| O08 | Inventory/manual active/manual reserve,75currency/8distinct/no ninth,active-time care/growth/claim/equipment,explicit confirmation, evolution_pending | IMPLEMENTED_VERIFIED; 8 авторских records и 24 PNG подключены |
| O09 | Import/schema/PNG/unique/references/strict0/1/2 проверены на temporary fixtures; fake production content не создаётся | S5_IMPORTED; BLOCKED_BY_INPUT: только настоящие Stories |
| O10 | Solo3обычных щита; отмена→Ice→Lunar→обычные; zero не даёт on-hit/Arise, extra без рекурсии, explicit Shadow target, restart/reward protection | IMPLEMENTED_VERIFIED |

IMPLEMENTED_VERIFIED означает трассировку и изолированные автоматические проверки, а не выполненную Telegram/релизную приёмку. Финальный exact SHA, полные count/skip/exit/log и Linux3.11/3.13 фиксируются в итоговом отчёте после последнего коммита. Подробная матрица O→handler→service→SQLite→UI→test: COMPLETION_AUDIT.md.

Активная policy schema_version2/statusAPPROVED. PROVISIONAL остаётся только в immutable legacy constants и именах старых frozen policies для совместимости, не как открытое решение владельца. Никаких BLOCKED_BY_RULE по O01–O10 больше нет. Strict content пока BLOCKED_BY_INPUT/exit2, отдельно от технических проверок.

## Следующая итерация: четыре механики рейдовых боссов

Утверждённый engine реализован отдельно от авторских records. Начальный HEAD f0b9c1c;
полная спецификация, JSON/SQLite/UI/test trace и ограничения — [RAID_BOSSES_MECHANICS.md](RAID_BOSSES_MECHANICS.md).
Четыре настоящих босса-изображения теперь лежат в raid/images/raid_boss_1.png…4.png
по порядку вложений. Реальные Boss code/name/records и восемь Mythic records/Stories ещё нужны.
Статус «В разработке» сохраняется; финальные проверки относятся к SHA итогового отчёта.

## Контентный проход 26.10.2026 от 9ef98aa

PNG_PASS: 45/45, исходные байты сохранены; black 0..4 только Mythic/Raid, white 252..255
только S5, alpha 255. S5_IMPORTED: 17 реальных записей (5/6/4/2), общий каталог 71;
повторный apply — no-op, конфликты/частичный набор отклоняются до записи. Добавления
не меняют 54 прежних героев и не затрагивают SQLite. Миграция схемы не нужна.
MYTHIC_RECORDS_READY/RAID_RECORDS_READY/STORIES_READY: BLOCKED_BY_INPUT. Точные запросы
и Story template/guide размещены вне активных каталогов. strict-content остаётся exit 2.
Технические тесты, final SHA и Linux CI фиксируются после последнего коммита в отчёте/PR #8.
Вердикт: Релиз заблокирован до полного контента, independent review, DB copy и staging.


## Авторский импорт 8+4, 26.10.2026

От SHA 4075776 установлены точные JSON из единого ZIP, SHA-256 совпадают с
AUTHOR_CONTENT_MANIFEST.json. Теперь 79 героев (все прежние 71 сохранены),
8 реальных Mythic и 4 реальных рейдовых босса. Все 45 PNG неизменны.
PNG/S5/MYTHIC/RAID readiness PASS; STORIES/STRICT BLOCKED_BY_INPUT (strict2).
Новая проверка Mythic до legacy-нормализации запрещает замаскированные ошибки
режимных ключей и bool/float ATK. Игровые формулы/описания не менялись.
Исторические записи ниже/выше о недостающих Mythic/Raid больше не текущий блокер.
Полные результаты окончательного SHA фиксируются в итоговом отчёте и PR #8.


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
