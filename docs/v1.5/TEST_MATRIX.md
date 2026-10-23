# Матрица проверок V1.5

Запуск через `python scripts/run_isolated_checks.py --label NAME -- <python args>`. Фиктивный token, DB/legacy/STORIES_DIR в Temp, без polling. Новые Telegram tests дополнительно запрещают любой неожиданный HTTP через AiohttpSession.make_request; transport mocked, сервисы/SQLite настоящие.

| Команда/набор | Доказательство |
|---|---|
| `-m unittest discover -s tests -q` | Весь старый и новый набор на exact final SHA |
| `-m unittest tests.v1_5.test_completion_audit -q` | 28 адресных регрессий: team normal/raid/old snapshots/rollback/zero/targets/admin/Star/UTF16/policy/Tower hooks/Story/Pet/Raid handlers/restart |
| `tools/check_v15.py` | 12 отдельных checks: compileall,check_bot,protected bundle,policy,hero/boss abilities,Tower,V142,Editorial,content diagnostic,Editorial release,PvP migration |
| `tools/validate_v15_migration.py` | Заполненный old-code fixture,3replay,nonzero legacy fragments,integrity=ok/FK=[],старый код на отдельной копии |
| D&D shop/test_b/coins/loot validators + benchmark | Обычный D&D/реальные каталоги,CLI publish только disposable paths,экономика/old-code compatibility |
| Linux workflow3.11/3.13 | Полный unittest и отдельно original real PTY Ctrl+C/SIGTERM/EOF/no/cleanup/redaction,все validators/historical migrations |
| `-m app.mini.mythic.content --strict-content` | Отдельный BLOCKED/exit2 при отсутствии реальных файлов; диагностический exit0 не означает PASS контента |
| `git diff --check`, `git diff d077dd2a...HEAD --check` | Финальная чистота diff; protected bundle отдельный строгий validator |

Baseline аудита c4dfb0f:1973tests/652.842s/0failures/errors/19Windows skips/exit0. Platform skips не добавлялись; real Linux PTY на Windows не выдаётся за PASS. Промежуточный полный прогон1988tests/879.383s поймал2регрессии holy_relic: слишком широкая actual-HP проверка ограничена Arise, исходные tests не изменялись. Linux f79f359 также поймал эти2ошибки. История промежуточных результатов в WORK_LOG; final SHA/count/log/CI URLs в итоговом отчёте/PR после последнего коммита.

Подробная матрица R01–R20 с путями/SQLite/test: COMPLETION_AUDIT.md. Production SQLite copy, реальная Telegram-визуальная/staging приёмка НЕ ВЫПОЛНЕНЫ.
