# D&D Mini V1.5 — независимый аудит ChatGPT

Дата: 26.10.2026. Источник: непосредственно просмотренные исходники и тесты подключённого GitHub плюс журналы GitHub Actions. Полное локальное исполнение репозитория недоступно; не утверждается, что все 215 файлов изучены построчно.

- PR: https://github.com/Aruzento/shpakdnd_bot/pull/8 — draft, open, not merged, mergeable=true.
- Точный HEAD: `cef77cc87f6a2286284f5cf8fdb851c775b04dd1`.
- Base main: `d077dd2a2c29ad420b138bf07af23c77f4674d0b`; main и PR база на момент проверки совпадали.
- PR: 19 коммитов, 215 файлов (включая JSON, PNG, код, документацию и тесты).
- CI: https://github.com/Aruzento/shpakdnd_bot/actions/runs/40330857281 ; Python Linux 3.11 и 3.13 — по 2135 tests, zero skips, обе jobs SUCCESS. Проверены логи и шаги обоих заданий. Windows 2135, 19 пропусков — только по отчёту Codex, не воспроизведено.

## Матрица по игровым блокам

| Блок | Независимое свидетельство | Оценка | Что ещё подтвердить на staging |
|---|---|---|---|
| Основной Boss V1.5 (15 мин, 10 стартов/день, legacy) | `boss/combat.py`, `v15_schema.py`, тесты `test_boss_stories.py`; параметризированные таймеры | Код/CI подтверждены | Новый и старый бой, рестарт в ходе боя, админ-процедуры |
| Normal/Raid, способности Mythic | `combat/team.py`, `mythic/raid/effects.py`, `modes.py`, `test_effect_matrix.py` | Код/CI подтверждены | Нулевой урон, огонь, молния, луна, фронтенд |
| 17 S5 | `import_v15_s5.py`, активные редкостные JSON, `content.report()` и тесты | Код/CI подтверждены | Отображение каталога и гачи, копии, фракции |
| 8 Mythic | `mythic.json`, `catalog.py`, `content.py`, `test_author_content.py` | 79 всего; код/CI подтверждены | Эволюция, карточки, +звёзды, исключение гачи |
| 4 raid bosses | `raid/bosses.json`, `catalog.py`, `mechanics.py`, `test_raid_mechanics.py` | Контракт/CI подтверждены | Armor 10/5, flight 8/2, heal 15/30, HP 75/50/25% |
| Контрпик O02 | `raid/effects.py`, `raid/service.py`, тесты O01–O10 | Код/CI подтверждены | Первый нулевой подтверждённый удар активирует counter |
| Raid cancellation O01 | `raid/service.py` 342–600, `raid/rewards.py::compensation`, ledger | Транзакционная проверка/CI | Предпросмотр, причина, отмена с/без награды, race |
| R09 награды / Luna Eclipse | `raid/rewards.py`, `raid/effects.py`, `mechanics.py` | Код/CI подтверждены | Actual contribution, original M, lunar buffs |
| Яйца/питомцы/резерв/звёзды | `mythic/pets.py`, `wallet.py`, тесты core/approved/author | Код/CI подтверждены | 8 питомцев, уход, резерв/активация, 1-time эволюция |
| Звёздные награды/Golden/Thief/Blessing | `star_rewards.py`, `boss/rewards.py`, `tower/service.py` | Код/CI подтверждены | Независимый RNG, one-ledger, награда и сундуки |
| Tower O10 | `tower/service.py`, `combat/team.py`, тесты O01–O10 | Код/CI подтверждены | 3 щита + специальные, нулевые попадания и рестарт |
| Arise/Shadow и старые герои | тесты completion, raid reward/normal, exclusion | Регрессии CI пройдены | Выдача Shadow, запрет запрещённых источников, владельцы |
| Истории schema V2 | `stories/catalog.py`, `routing.py`, `service.py`, `presentation.py`, `test_story_once.py` | Код/CI подтверждены | Telegram по двум игрокам, stale buttons, restart, uncertain |
| Авторская овца | неопубликованный JSON в Git, 53 узла, 113 рёбер, 7 концовок; CI candidate | Контент валиден в изолированном внешнем каталоге | Отдельно установить в staging STORIES_DIR |
| SQLite/migration | `mini/migrations/v1_5.py`, `stories/schema.py`, изолированные миграции/FK | Синтетическая совместимость подтверждена | Offline копия production SQLite и SHA/integrity/ownership |
| PNG | 45 изображений, контракт alpha 255/фон, CI-content диагностик | Отчёт/CI PASS | Фактическое отображение карточек/PNG Telegram |
| Деплой и watcher | `deploy/deploy-shpakdnd.sh`, `deploy/shpakdnd-bot-watch.path`, `RELEASE_AND_ROLLBACK.md` | Имеются критичные эксплуатационные открытые вопросы | Dry-run, предзапускные тесты, guard активных операций, система мониторинга |

## Выявленные замечания и приоритет

1. **P1 — проверка production развёртывания продолжает длительные тесты во время остановки бота.** `deploy/deploy-shpakdnd.sh`: `stop_stack()` до `checks()`, а `checks()` вызывает полный `python -m unittest discover -s tests -q`. Проверять такой набор на точном SHA до остановки production, затем делать короткие smoke/checks после checkout. Любые изменения скрипта требуют отдельного тестирования.
2. **P1 — встроенный rollback guard явно учитывает PvP, но не проверяет активные рейды V1.5, Stories V2 и pet runtime.** `rollback_preflight()` запускает PvP helper, а старый код не умеет безопасно завершать V2 и новые рейды. Нужен явный запрет на rollback при незавершённых состояниях либо управляемый drain с ручным согласием; нельзя возвращать старую БД поверх новых финансовых операций.
3. **P1 — production strict-content не является обязательным доказанным gate деплоя.** В CI авторский Story JSON копируется в temp и `strict-content` PASS; в обычном `STORIES_DIR` его пока нет, штатная диагностика — BLOCKED_BY_INPUT. Скрипт деплоя не содержит явного вызова `--strict-content` для целевой конфигурации. До выпуска нужен отдельный, read-only preflight доступности и содержимого реального внешнего Story каталога, не меняющий production.
4. **P2 — устаревшие аудиторские документы:** `docs/v1.5/COMPLETION_AUDIT.md` по O03/O09 и часть `TEST_MATRIX.md` по-прежнему описывают отсутствующие 4 босса/контент и прошлые blockers. Обновить указатели на окончательные результаты, сохранить исторические сведения отдельно.
5. **P2 — Moon raid описание двусмысленно:** авторский `passive_text` говорит о первой успешной активации, движок выбирает аркану до расчёта фактического попадания, в том числе actual=0. Согласовать только текст UI, не менять формулу без решения владельца.
6. **P2 — обязательные проверки не проведены на offline copy настоящей production SQLite, и живой Telegram staging не проводился.** Это release blockers, а не утверждение об ошибке реализации.
7. **P3 — публикация Telegram не атомарна с SQLite.** `sending` после аварии становится `uncertain`, автоматической отправки нет. Нужен операционный протокол сверки/восстановления.
8. **P3 — контрпики могут активироваться на фактическом нулевом уроне**, в частности Ice 1ATK против armored и Star melee против flying. Это соответствует утверждённому O02, но требует понятного игрового текста.

## Обязательные следующие проверки

1. Исправить/проверить P1 эксплуатационные gate и актуализировать P2 документацию в новой ветке (не делать merge и deploy без разрешения).
2. После любого исправления повторно запустить полный unittest, check_bot, compileall, валидаторы Hero/Boss/Tower/Story/Content, SQLite migration/integrity, git diff --check, Linux Python 3.11/3.13 на новом SHA.
3. Получить согласованную offline SQLite backup-копию production, проверить источник read-only, затем три миграции на копии, integrity/FK, владение 79 героями, сохранение валют, фрагментов, активные бои, рейды, питомцев, события и Истории.
4. Отдельно разрешить staging с изолированными token/SQLite/STORIES_DIR. Пройти игровой цикл новых блоков, в том числе семь финалов, нагрузку/повторы, crash/unknown delivery; собрать журналы, message IDs и снимки БД до/после.
5. Повторная проверка окончательного SHA и реального fallback плана; только потом рассматривать merge → main и production.

## Вердикт

**Статус «В разработке». Релиз заблокирован.** Автоматическое CI успешно; критичных ошибок непосредственно в изученных механиках не установлено, но release-grade доказательства production-copy и staging отсутствуют, а эксплуатационные защитные барьеры требуют улучшения. Полная построчная проверка 215 файлов лично не осуществлена; тесты не запускались в личном окружении.
