# Матрица проверок V1.5

Все команды запускаются в feature-worktree через `python scripts/run_isolated_checks.py --label <имя> -- <python-аргументы>`. Фиктивный Telegram token, app.config DB_PATH и LEGACY_DB_PATH направлены в Temp; исходная SQLite/.env не копируются. `check_bot.py` выполняется только так или на одноразовой копии. Это offline-проверки, без polling и серверных действий.

| Команда | Что доказывает |
|---|---|
| `-m unittest discover -s tests -q` | Весь набор D&D/магазин/Inventory/день-ночь/Boss/Tower/Equipment/Gacha/Village/Duels/Events/PvP/Editorial/Shadow + V1.5 |
| `-m unittest discover -s tests/v1_5 -q` | Все 16 Mythic-свойств, 12+4 арканы/1–6★, integer floor/clamp/атрибуция, R09, normal/raid fallback, звери/лимиты/rollback/replay/конкуренция, Stories/реальные персонажи/параллельные финальные голоса |
| `tools/check_v15.py` | compileall, isolated check_bot, hero/boss/Tower/V142/Editorial/new-content validators, JSON/repeated startup и историческая PvP migration/rollback |
| `tools/validate_v15_migration.py` | Заполненная база из исходного SHA, три миграции с неизменными балансами/героями/старыми runtime и legacy fragments, integrity/FK, old-code startup на отдельной копии |
| `tools/validate_dnd_shop_release.py` | Историческая обычная D&D экономика/миграции/запуск старого кода; новые таблицы V1.5 разрешены по точному whitelist, старые строки проверяются целиком по исторической проекции |
| `-m app.mini.mythic.content --strict-content` | Полнота настоящего контента. Ожидаемый текущий результат BLOCKED/2, а не PASS |
| `git diff --check`; `git diff <base>...HEAD --check` | Проверка патча до/после коммита |

Baseline до изменения существующих модулей: **1901 tests, 0 failures/errors, 19 Windows skips, 778.335 s, exit 0**. Журнал: Temp/dnd-v15-baseline-buspp3sg/output.log.

Первый полный промежуточный прогон: 1934 tests, 55 failures, 50 errors, 19 skips, exit 1; обнаружил рассинхронизацию нового normal и legacy passive_key, отсутствующие необязательные Story-таблицы в isolated UI fixtures и старые ожидания. Ошибки исправлялись адресно. Новые правила, заменяющие ожидания: 900 секунд, один участник, eligibility без собственного timeout, floor, actual HP вместо overkill, сложение процентов, пассивки на extra и запрет старого craft. Проверки целостности/платежей/скрытого изменения прав сохранены.

Предфинальные проверки: матрица 58 новых тестов PASS; 83 Boss/Tower/core PASS; после дальнейших интеграций полный набор запускается заново. Все 10 офлайн-валидаторов tools/check_v15.py PASS на промежуточном коде. Финальный точный SHA, число тестов и пути журналов фиксируются в итоговом отчёте, после последнего коммита.

Linux CI сохраняет матрицу Python 3.11/3.13. Отменены только проверки старого релиза «Mini не изменился в V1.4.4» и AST-эквивалентность V1.4.2/V1.4.3: V1.5 явно изменяет Mini. Вместо них добавлены V1.5 content diagnostics и migration replay. Защищённые deployment-файлы и D&D/PvP-регрессии остаются проверяемыми. Новые пути добавлены только в Git-конфигурацию watch.path; внешние STORIES_DIR/Editorial и SQLite не наблюдаются.

Windows baseline skips относятся к Linux/PTY; они не сняты заглушками. Финальный Linux outcome необходимо смотреть отдельно. Копия production SQLite, реальные Telegram-публикации/визуальная приёмка и staging **НЕ ВЫПОЛНЕНЫ**: копия/ресурсы не предоставлены, deployment запрещён текущим заданием.

Кандидат 4105711: полный Windows-прогон 1973 tests / 650.698 s / 19 skips, exit 1 (3 failures + 3 errors). Пять Shadow-регрессий вызваны потерей инъекции RNG в normal→Arise adapter; исправлено сохранением прежнего RNG seam. Шестая — ожидание только старого D&D Shop watcher-каталога; whitelist расширен точными путями Stories и Raid, запрет внешних каталогов сохранён. После исправлений все 17 Shadow/compatibility tests PASS (5.391 s). Все 10 validators и populated migration replay на 4105711 PASS.

Linux CI первого кандидата дополнительно выявил test_ctrl_c_while_waiting_is_not_consent в неизменённых защищённых deployment-файлах (обе версии Python). Для проверки исходного состояния запущен тот же offline workflow на main; это не deployment. Итоговый outcome и точный SHA повторных прогонов приводятся в итоговом отчёте/PR.
